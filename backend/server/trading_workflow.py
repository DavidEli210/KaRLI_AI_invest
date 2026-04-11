import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from typing import Any, Dict, List, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import create_model
from alpaca.trading.enums import OrderSide

from alpacaTrading import (
    create_client,
    get_account_info,
    get_open_positions,
    get_portfolio_history,
    submit_order,
)
from cognito_utils import get_user_alpaca_credentials_by_sub


logger = logging.getLogger(__name__)


class TradingState(TypedDict):
    user_id: str
    portfolio_data: Dict[str, Any]
    market_research: Dict[str, Any]
    trade_signals: List[Dict[str, Any]]


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


def get_alpaca_portfolio(user_id: str) -> Dict[str, Any]:
    credentials = get_user_alpaca_credentials_by_sub(user_id)
    if not credentials:
        raise ValueError(f"Alpaca credentials not found for user_id={user_id}")

    client = create_client(credentials["api_key"], credentials["api_secret"])
    account_info = _to_jsonable(get_account_info(client))
    positions = _to_jsonable(get_open_positions(client))
    portfolio_history = _to_jsonable(get_portfolio_history(client))

    return {
        "account_info": account_info,
        "positions": positions,
        "portfolio_history": portfolio_history,
    }


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
            executed.append(
                {
                    "instruction": instruction,
                    "status": "skipped",
                    "reason": "Instruction requires action in {buy,sell}, symbol, and quantity",
                }
            )
            continue

        try:
            side = OrderSide.BUY if action == "buy" else OrderSide.SELL
            result = submit_order(client, symbol=str(symbol), quantity=float(quantity), action=side)
            # Defensive guard: treat structured error payloads as failures.
            if isinstance(result, dict) and result.get("error"):
                raise RuntimeError(str(result["error"]))
            executed.append(
                {
                    "instruction": instruction,
                    "status": "submitted",
                    "result": _to_jsonable(result),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Failed to submit order for user_id=%s symbol=%s action=%s",
                user_id,
                symbol,
                action,
            )
            executed.append(
                {
                    "instruction": instruction,
                    "status": "error",
                    "error": str(exc),
                }
            )

    return {"status": "complete", "executed": executed}


def _json_type_to_python(schema: Dict[str, Any]) -> Any:
    json_type = schema.get("type")
    if json_type == "string":
        return str
    if json_type == "integer":
        return int
    if json_type == "number":
        return float
    if json_type == "boolean":
        return bool
    if json_type == "array":
        return List[Any]
    if json_type == "object":
        return Dict[str, Any]
    return Any


def _build_tool_args_model(tool_name: str, input_schema: Dict[str, Any]):
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))
    fields = {}

    for field_name, field_schema in properties.items():
        py_type = _json_type_to_python(field_schema)
        default = ... if field_name in required else None
        fields[field_name] = (py_type, default)

    model_name = f"{tool_name.title().replace('-', '_')}Args"
    if not fields:
        return create_model(model_name)
    return create_model(model_name, **fields)


async def _mcp_tool_result_to_text(result: Any) -> str:
    if result is None:
        return "null"

    content = getattr(result, "content", None)
    if content is None:
        return json.dumps(_to_jsonable(result), default=str)

    normalized_parts: List[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text is not None:
            normalized_parts.append(text)
        else:
            normalized_parts.append(json.dumps(_to_jsonable(item), default=str))
    return "\n".join(normalized_parts)


def _convert_mcp_tools_to_langchain(session: ClientSession, mcp_tools: Any) -> List[StructuredTool]:
    tools: List[StructuredTool] = []

    for mcp_tool in mcp_tools:
        tool_name = mcp_tool.name
        description = mcp_tool.description or f"MCP tool: {tool_name}"
        input_schema = getattr(mcp_tool, "inputSchema", {}) or {}
        args_schema = _build_tool_args_model(tool_name, input_schema)

        async def _tool_coroutine(_tool_name: str = tool_name, **kwargs):
            result = await session.call_tool(_tool_name, kwargs)
            return await _mcp_tool_result_to_text(result)

        def _tool_sync(**kwargs):
            raise RuntimeError("This tool is async-only. Use ainvoke in the strategy workflow.")

        tools.append(
            StructuredTool.from_function(
                func=_tool_sync,
                coroutine=_tool_coroutine,
                name=tool_name,
                description=description,
                args_schema=args_schema,
            )
        )

    return tools


async def _extract_trade_json(text: str) -> List[Dict[str, Any]]:
    cleaned = text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", maxsplit=1)[1]
    if "```" in cleaned:
        cleaned = cleaned.split("```", maxsplit=1)[0]
    cleaned = cleaned.strip()

    parsed = json.loads(cleaned)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        if "instructions" in parsed and isinstance(parsed["instructions"], list):
            return parsed["instructions"]
        return [parsed]
    raise ValueError(
        "Strategy output must be a JSON list, a single instruction object, "
        "or an object containing an 'instructions' list."
    )


async def _run_strategy_with_tools(
    state: TradingState,
    llm_with_tools: Any,
    tool_lookup: Dict[str, StructuredTool],
) -> List[Dict[str, Any]]:
    system_prompt = (
        "You are an institutional trading strategist. Prioritize capital preservation, "
        "position sizing discipline, drawdown limits, and concentration risk. Avoid overtrading. "
        "Output ONLY valid JSON: a list of instructions with keys "
        "action (buy/sell/hold), symbol, quantity, and rationale."
    )

    user_payload = {
        "user_id": state["user_id"],
        "portfolio_data": state["portfolio_data"],
        "market_research": state["market_research"],
    }

    messages: List[Any] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=json.dumps(user_payload, default=str)),
    ]

    for _ in range(5):
        ai_message = await llm_with_tools.ainvoke(messages)
        messages.append(ai_message)

        tool_calls = getattr(ai_message, "tool_calls", None) or []
        if not tool_calls:
            content = ai_message.content if isinstance(ai_message.content, str) else str(ai_message.content)
            return await _extract_trade_json(content)

        for call in tool_calls:
            tool_name = call["name"]
            tool = tool_lookup.get(tool_name)
            if tool is None:
                tool_result = f"Tool '{tool_name}' is unavailable."
            else:
                tool_result = await tool.ainvoke(call.get("args", {}))

            messages.append(
                ToolMessage(
                    content=tool_result if isinstance(tool_result, str) else json.dumps(tool_result, default=str),
                    tool_call_id=call["id"],
                )
            )

    raise RuntimeError("Strategy exceeded max tool-call rounds without final JSON output.")


def _build_graph():
    graph_builder = StateGraph(TradingState)

    async def analyst_node(state: TradingState) -> Dict[str, Any]:
        logger.info("Analyst node: fetching Alpaca portfolio for user_id=%s", state["user_id"])
        portfolio_data = get_alpaca_portfolio(state["user_id"])
        return {"portfolio_data": portfolio_data}

    async def researcher_node(state: TradingState) -> Dict[str, Any]:
        logger.info("Researcher node: collecting market context via MCP")
        symbols = []
        for pos in state.get("portfolio_data", {}).get("positions", []) or []:
            symbol = pos.get("symbol") or pos.get("asset_symbol") or pos.get("ticker")
            if symbol:
                symbols.append(symbol)
        if not symbols:
            logger.warning(
                "Researcher node found no symbols in positions payload. "
                "Expected key 'symbol' from Alpaca model_dump(); fallback keys checked: "
                "'asset_symbol', 'ticker'."
            )
        return {
            "market_research": {
                "focus_symbols": symbols[:10],
                "notes": "MCP tools are available to the Strategy node through Claude tool-calling.",
            }
        }

    async def strategy_node(state: TradingState, config: RunnableConfig) -> Dict[str, Any]:
        logger.info("Strategy node: generating trade signals with Claude")
        runtime = config.get("configurable", {})
        if "llm_with_tools" not in runtime or "tool_lookup" not in runtime:
            raise ValueError("Missing runtime tools in graph config.")
        llm_with_tools = runtime["llm_with_tools"]
        tool_lookup = runtime["tool_lookup"]
        instructions = await _run_strategy_with_tools(state, llm_with_tools, tool_lookup)
        return {"trade_signals": instructions}

    async def executor_node(state: TradingState) -> Dict[str, Any]:
        logger.info("Executor node: submitting trade instructions to Alpaca")
        result = await asyncio.to_thread(
            execute_alpaca_trades,
            state.get("trade_signals", []),
            state["user_id"],
        )
        logger.info("Executor node complete: %s", json.dumps(result, default=str))
        return {}

    graph_builder.add_node("analyst", analyst_node)
    graph_builder.add_node("researcher", researcher_node)
    graph_builder.add_node("strategy", strategy_node)
    graph_builder.add_node("executor", executor_node)

    graph_builder.add_edge(START, "analyst")
    graph_builder.add_edge("analyst", "researcher")
    graph_builder.add_edge("researcher", "strategy")
    graph_builder.add_edge("strategy", "executor")
    graph_builder.add_edge("executor", END)

    return graph_builder.compile()


TRADING_GRAPH = _build_graph()


async def _run_trading_workflow_async(user_id: str) -> None:
    logger.info("Trading workflow started for user_id=%s", user_id)
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    if not anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY is missing. Aborting workflow for user_id=%s", user_id)
        return

    # MCP server launch command can be customized using env vars.
    mcp_command = os.getenv("ALPHA_VANTAGE_MCP_COMMAND", "uvx")
    mcp_args_raw = os.getenv("ALPHA_VANTAGE_MCP_ARGS", "alphavantage-mcp")
    mcp_args = [arg for arg in mcp_args_raw.split(" ") if arg]

    async with AsyncExitStack() as stack:
        server_params = StdioServerParameters(command=mcp_command, args=mcp_args)
        read_stream, write_stream = await stack.enter_async_context(stdio_client(server_params))

        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        logger.info("MCP session initialized for Alpha Vantage server")

        listed_tools = await session.list_tools()
        mcp_tools = getattr(listed_tools, "tools", []) or []
        langchain_tools = _convert_mcp_tools_to_langchain(session, mcp_tools)
        tool_lookup = {tool.name: tool for tool in langchain_tools}
        logger.info("Loaded %s MCP tools for strategy agent", len(langchain_tools))

        llm = ChatAnthropic(
            model="claude-opus-4-6",
            temperature=0,
            api_key=anthropic_api_key,
        )
        llm_with_tools = llm.bind_tools(langchain_tools)

        initial_state: TradingState = {
            "user_id": user_id,
            "portfolio_data": {},
            "market_research": {},
            "trade_signals": [],
        }
        final_state = await TRADING_GRAPH.ainvoke(
            initial_state,
            config={
                "configurable": {
                    "llm_with_tools": llm_with_tools,
                    "tool_lookup": tool_lookup,
                }
            },
        )
        logger.info(
            "Trading workflow finished for user_id=%s with %s trade signals",
            user_id,
            len(final_state.get("trade_signals", [])),
        )


def run_trading_workflow(user_id: str) -> None:
    try:
        asyncio.run(_run_trading_workflow_async(user_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Workflow crashed for user_id=%s: %s", user_id, str(exc))