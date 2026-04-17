import asyncio
import json
import logging
import os
import random
from contextlib import AsyncExitStack
from typing import Annotated, Any, Dict, List, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import create_model
from alpaca.trading.enums import OrderSide

from alpacaTrading import (
    create_client,
    get_account_info,
    get_open_positions,
    submit_order,
)
from cognito_utils import get_user_alpaca_credentials_by_sub


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class TradingState(TypedDict):
    user_id: str
    portfolio_data: Dict[str, Any]
    messages: Annotated[List[BaseMessage], add_messages]
    trade_signals: List[Dict[str, Any]]
    tool_call_count: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_jsonable(value: Any) -> Any:
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


def _trim_portfolio(account_info: Any, positions: Any) -> Dict[str, Any]:
    raw_account = _to_jsonable(account_info) or {}
    raw_positions = _to_jsonable(positions) or []

    trimmed_account = {
        k: raw_account[k]
        for k in ("cash", "equity")
        if raw_account.get(k) is not None
    }

    trimmed_positions = [
        {
            k: pos[k]
            for k in ("symbol", "qty", "market_value", "unrealized_pl")
            if pos.get(k) is not None
        }
        for pos in (raw_positions if isinstance(raw_positions, list) else [])
    ]

    return {
        "account_info": trimmed_account,
        "positions": trimmed_positions,
    }


def get_alpaca_portfolio(user_id: str) -> Dict[str, Any]:
    credentials = get_user_alpaca_credentials_by_sub(user_id)
    if not credentials:
        raise ValueError(f"Alpaca credentials not found for user_id={user_id}")

    client = create_client(credentials["api_key"], credentials["api_secret"])
    account_info = get_account_info(client)
    positions = get_open_positions(client)

    return _trim_portfolio(account_info, positions)


def execute_alpaca_trades(instructions: List[Dict[str, Any]], user_id: str) -> Dict[str, Any]:
    credentials = get_user_alpaca_credentials_by_sub(user_id)
    if not credentials:
        return {"status": "error", "message": "Missing Alpaca credentials", "executed": []}

    client = create_client(credentials["api_key"], credentials["api_secret"])
    executed: List[Dict[str, Any]] = []

    for instruction in instructions:
        action = str(instruction.get("action", "")).lower()
        symbol = instruction.get("symbol")
        quantity = instruction.get("quantity")

        if not symbol or not quantity or action not in {"buy", "sell"}:
            executed.append({"instruction": instruction, "status": "skipped"})
            continue

        try:
            side = OrderSide.BUY if action == "buy" else OrderSide.SELL
            result = submit_order(client, symbol=str(symbol), quantity=float(quantity), action=side)
            executed.append({"instruction": instruction, "status": "submitted", "result": _to_jsonable(result)})
        except Exception as exc:
            executed.append({"instruction": instruction, "status": "error", "error": str(exc)})

    return {"status": "complete", "executed": executed}


# ---------------------------------------------------------------------------
# MCP → LangChain tool conversion
# ---------------------------------------------------------------------------

def _json_type_to_python(schema: Dict[str, Any]) -> Any:
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": List[Any],
        "object": Dict[str, Any],
    }.get(schema.get("type"), Any)


def _build_tool_args_model(tool_name: str, input_schema: Dict[str, Any]):
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))
    fields = {}

    for field_name, field_schema in properties.items():
        py_type = _json_type_to_python(field_schema)
        default = ... if field_name in required else None
        fields[field_name] = (py_type, default)

    return create_model(f"{tool_name.title()}Args", **fields) if fields else create_model(f"{tool_name.title()}Args")


async def _mcp_tool_result_to_text(result: Any) -> str:
    if result is None:
        return "null"

    content = getattr(result, "content", None)
    if content is None:
        return json.dumps(_to_jsonable(result), default=str)

    return "\n".join(
        getattr(item, "text", None) or json.dumps(_to_jsonable(item), default=str)
        for item in content
    )


def _convert_mcp_tools_to_langchain(session: ClientSession, mcp_tools: Any) -> List[StructuredTool]:
    tools: List[StructuredTool] = []

    for mcp_tool in mcp_tools:
        tool_name = mcp_tool.name
        args_schema = _build_tool_args_model(tool_name, getattr(mcp_tool, "inputSchema", {}) or {})

        async def _tool_coroutine(_tool_name: str = tool_name, **kwargs):
            result = await session.call_tool(_tool_name, kwargs)
            return await _mcp_tool_result_to_text(result)

        tools.append(
            StructuredTool.from_function(
                func=lambda **kwargs: None,
                coroutine=_tool_coroutine,
                name=tool_name,
                description=mcp_tool.description or tool_name,
                args_schema=args_schema,
            )
        )

    return tools


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def _build_graph(filtered_tools: List[StructuredTool]):

    graph_builder = StateGraph(TradingState)

    async def analyst_node(state: TradingState) -> Dict[str, Any]:
        portfolio_data = get_alpaca_portfolio(state["user_id"])

        return {
            "portfolio_data": portfolio_data,
            "messages": [
                SystemMessage(content="You are a trading strategist."),
                HumanMessage(content=json.dumps({"portfolio_data": portfolio_data}))
            ],
        }

    async def strategy_node(state: TradingState, config: RunnableConfig) -> Dict[str, Any]:
        runtime = config.get("configurable", {})
        llm_with_tools = runtime["llm_with_tools"]

        tool_call_count = state.get("tool_call_count", 0)
        messages = list(state["messages"])

        if tool_call_count >= 8:
            messages.append(
                HumanMessage(
                    content="Do NOT call any more tools. Return JSON trade instructions only."
                )
            )

        ai_message = await _invoke_with_backoff(llm_with_tools, messages)

        new_calls = len(getattr(ai_message, "tool_calls", []) or [])

        return {
            "messages": [ai_message],
            "tool_call_count": tool_call_count + new_calls,
        }

    tool_node = ToolNode(tools=filtered_tools)

    async def executor_node(state: TradingState) -> Dict[str, Any]:
        final_ai = next(
            (m for m in reversed(state["messages"]) if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None)),
            None
        )

        if not final_ai:
            return {"trade_signals": []}

        trade_signals = await _extract_trade_json(str(final_ai.content))

        await asyncio.to_thread(execute_alpaca_trades, trade_signals, state["user_id"])

        return {"trade_signals": trade_signals}

    graph_builder.add_node("analyst", analyst_node)
    graph_builder.add_node("strategy", strategy_node)
    graph_builder.add_node("tools", tool_node)
    graph_builder.add_node("executor", executor_node)

    graph_builder.add_edge(START, "analyst")
    graph_builder.add_edge("analyst", "strategy")

    graph_builder.add_conditional_edges(
        "strategy",
        tools_condition,
        {"tools": "tools", END: "executor"},
    )

    graph_builder.add_edge("tools", "strategy")
    graph_builder.add_edge("executor", END)

    return graph_builder.compile()


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

async def _run_trading_workflow_async(user_id: str) -> None:

    async with AsyncExitStack() as stack:
        session = await stack.enter_async_context(ClientSession(*await stdio_client(StdioServerParameters(command="alphavantage-mcp"))))
        await session.initialize()

        tools = _convert_mcp_tools_to_langchain(session, (await session.list_tools()).tools)

        llm = ChatAnthropic(model="claude-opus-4-6", temperature=0)
        llm_with_tools = llm.bind_tools(tools)

        graph = _build_graph(tools)

        initial_state: TradingState = {
            "user_id": user_id,
            "portfolio_data": {},
            "messages": [],
            "trade_signals": [],
            "tool_call_count": 0,
        }

        await graph.ainvoke(
            initial_state,
            config={"configurable": {"llm_with_tools": llm_with_tools}},
        )


def run_trading_workflow(user_id: str) -> None:
    asyncio.run(_run_trading_workflow_async(user_id))