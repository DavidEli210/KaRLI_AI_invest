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
    # `add_messages` reducer appends to the list rather than overwriting it,
    # which is exactly what the strategy ↔ tool loop needs.
    messages: Annotated[List[BaseMessage], add_messages]
    trade_signals: List[Dict[str, Any]]


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
    """Reduce portfolio data to only the fields the LLM needs, cutting ~80% of tokens."""
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

    portfolio = _trim_portfolio(account_info, positions)
    logger.info(
        "Portfolio trimmed: %d positions, account keys: %s",
        len(portfolio["positions"]),
        list(portfolio["account_info"].keys()),
    )
    return portfolio


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


# ---------------------------------------------------------------------------
# MCP → LangChain tool conversion
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

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


def _safe_preview(text: str, max_len: int = 240) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= max_len:
        return normalized
    return f"{normalized[:max_len]}..."


def _summarize_trade_instructions(instructions: List[Dict[str, Any]]) -> Dict[str, Any]:
    buys: List[Dict[str, Any]] = []
    sells: List[Dict[str, Any]] = []

    for instruction in instructions:
        action = str(instruction.get("action", "")).lower()
        if action == "buy":
            buys.append(instruction)
        elif action == "sell":
            sells.append(instruction)

    symbols = [
        str(instruction.get("symbol"))
        for instruction in instructions
        if instruction.get("symbol")
    ]
    rationale_preview = [
        _safe_preview(str(instruction.get("rationale", "")), max_len=120)
        for instruction in instructions
        if instruction.get("rationale")
    ][:3]

    return {
        "instruction_count": len(instructions),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "symbols": sorted(set(symbols)),
        "rationale_preview": rationale_preview,
    }


async def _summarize_decision_from_text(text: str) -> Dict[str, Any]:
    preview = _safe_preview(text)
    try:
        trade_signals = await _extract_trade_json(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return {
            "parse_ok": False,
            "reason": "final_model_output_not_valid_trade_json",
            "error": str(exc),
            "content_preview": preview,
            "content_length": len(text or ""),
        }

    summary = _summarize_trade_instructions(trade_signals)
    if summary["buy_count"] == 0:
        summary["no_buy_reason"] = "model_produced_no_buy_instructions"
    summary["parse_ok"] = True
    return summary


async def _invoke_with_backoff(llm_with_tools: Any, messages: List[Any], max_retries: int = 4) -> Any:
    for attempt in range(max_retries):
        try:
            return await llm_with_tools.ainvoke(messages)
        except Exception as exc:
            if "rate_limit" in str(exc).lower() or "429" in str(exc):
                wait = (2 ** attempt) + random.uniform(0, 1)
                logger.warning(
                    "Rate limited, retrying in %.1fs (attempt %d/%d)",
                    wait, attempt + 1, max_retries,
                )
                await asyncio.sleep(wait)
            else:
                raise
    raise RuntimeError("Exceeded max retries due to rate limiting.")


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def _build_graph(filtered_tools: List[StructuredTool]):
    """
    Graph topology:

        START → analyst → strategy ⇄ tools (loop until no tool calls)
                                  ↓
                              executor → END

    The `tools_condition` prebuilt router inspects the last AIMessage in
    `state["messages"]`:
      - if it contains tool_calls  → route to "tools"
      - otherwise                  → route to "executor"

    The ToolNode executes all requested tools concurrently and appends
    ToolMessage results back into `state["messages"]`, then returns to
    "strategy" for the next LLM call.
    """
    graph_builder = StateGraph(TradingState)

    # ------------------------------------------------------------------
    # analyst node — fetch portfolio, seed the message list
    # ------------------------------------------------------------------
    async def analyst_node(state: TradingState) -> Dict[str, Any]:
        logger.info("Analyst node: fetching Alpaca portfolio for user_id=%s", state["user_id"])
        portfolio_data = get_alpaca_portfolio(state["user_id"])

        cash = portfolio_data.get("account_info", {}).get("cash", "unknown")
        positions = portfolio_data.get("positions", [])
        held_symbols = [p["symbol"] for p in positions if p.get("symbol")]

        system_prompt = (
            "You are an institutional trading strategist with full autonomy to discover and act on market opportunities.\n\n"

            "RESEARCH:\n"
            "You are NOT limited to existing holdings. Use your available tools proactively to find promising stocks. "
            "Research any symbols you believe have opportunity — use price, quote, technical indicator, and news tools "
            "to build conviction before deciding.\n\n"

            "CAPITAL RULES:\n"
            f"- Your available cash is: {cash}. This is fixed. Do not assume it will increase.\n"
            "- Do NOT count on proceeds from any sell instructions in this session — "
            "sell settlements are not immediate, so treat available cash as the number above only.\n"
            "- Do NOT deploy all available cash. Keep a minimum 20-30% reserve at all times.\n"
            "- Spread buys across multiple positions. No single buy should exceed 20% of available cash.\n"
            "- Size positions conservatively. Prioritize capital preservation over maximizing deployment.\n\n"

            "SELL RULES:\n"
            f"- You may only sell symbols you currently hold: {held_symbols if held_symbols else 'none'}.\n"
            "- Do not instruct a sell for any symbol not in that list.\n\n"

            "TOOL USAGE:\n"
            "- You may call tools to research opportunities. After gathering sufficient data, stop calling tools.\n"
            "- When you are ready to decide, do NOT call any more tools. You may call up to a total of 10 in the whole conversation.\n\n"

            "OUTPUT RULES:\n"
            "- When you have finished your research, output ONLY valid JSON: a list of trade instructions.\n"
            "- Each instruction must have exactly these keys: action (buy or sell), symbol, quantity, rationale.\n"
            "- Do NOT include hold instructions — omit them entirely.\n"
            "- If no actionable opportunities exist, return an empty list: []\n"
        )

        seed_messages: List[BaseMessage] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=json.dumps({"portfolio_data": portfolio_data}, default=str)),
        ]

        return {
            "portfolio_data": portfolio_data,
            "messages": seed_messages,  # add_messages will append these
        }

    # ------------------------------------------------------------------
    # strategy node — single LLM call; graph routes back here after tools
    # ------------------------------------------------------------------
    async def strategy_node(state: TradingState, config: RunnableConfig) -> Dict[str, Any]:
        logger.info("Strategy node: invoking LLM (message count=%d)", len(state["messages"]))
        runtime = config.get("configurable", {})
        if "llm_with_tools" not in runtime:
            raise ValueError("Missing 'llm_with_tools' in graph config.")

        llm_with_tools = runtime["llm_with_tools"]

        # Count how many tool rounds have already happened so we can
        # inject a stop instruction if the model is being too chatty.
        tool_round_count = sum(
            1 for m in state["messages"]
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
        )

        messages = list(state["messages"])
        if tool_round_count >= 2:
            messages.append(
                HumanMessage(
                    content=(
                        "You have gathered enough data. "
                        "Do NOT call any more tools. "
                        "Respond ONLY with a valid JSON list of trade instructions. "
                        "If there are no actionable trades, return an empty list: []"
                    )
                )
            )

        ai_message = await _invoke_with_backoff(llm_with_tools, messages)
        tool_calls = getattr(ai_message, "tool_calls", None) or []
        logger.info("Strategy node: tool_calls=%d tool_round_count=%d", len(tool_calls), tool_round_count)

        if tool_calls:
            tool_names = [str(call.get("name", "unknown_tool")) for call in tool_calls if isinstance(call, dict)]
            logger.info(
                "Strategy decision summary: requested_tools=%s requested_tool_count=%d",
                tool_names,
                len(tool_names),
            )
        else:
            content = ai_message.content if isinstance(ai_message.content, str) else str(ai_message.content)
            decision_summary = await _summarize_decision_from_text(content)
            logger.info(
                "Strategy decision summary: %s",
                json.dumps(decision_summary, default=str),
            )
        return {"messages": [ai_message]}

    # ------------------------------------------------------------------
    # tool node — LangGraph's prebuilt ToolNode runs all tool calls
    # concurrently and appends ToolMessage results to state["messages"]
    # ------------------------------------------------------------------
    tool_node = ToolNode(tools=filtered_tools)

    # ------------------------------------------------------------------
    # executor node — parse final JSON from last AIMessage and trade
    # ------------------------------------------------------------------
    async def executor_node(state: TradingState) -> Dict[str, Any]:
        logger.info("Executor node: parsing trade signals from final AI message")

        # Find the last AIMessage that has no tool calls (the final decision)
        final_ai_message = None
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
                final_ai_message = msg
                break

        if final_ai_message is None:
            logger.warning("Executor node: no final AI message found; skipping trades")
            return {"trade_signals": []}

        content = (
            final_ai_message.content
            if isinstance(final_ai_message.content, str)
            else str(final_ai_message.content)
        )

        try:
            trade_signals = await _extract_trade_json(content)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error(
                "Executor node: failed to parse trade JSON — %s | content_length=%d | preview=%s",
                exc,
                len(content),
                _safe_preview(content),
            )
            return {"trade_signals": []}

        signal_summary = _summarize_trade_instructions(trade_signals)
        logger.info("Executor node decision summary: %s", json.dumps(signal_summary, default=str))
        if signal_summary["buy_count"] == 0:
            logger.warning(
                "Executor node: no buy instructions generated. sell_count=%d symbols=%s",
                signal_summary["sell_count"],
                signal_summary["symbols"],
            )

        logger.info("Executor node: submitting %d trade instructions to Alpaca", len(trade_signals))
        result = await asyncio.to_thread(
            execute_alpaca_trades,
            trade_signals,
            state["user_id"],
        )
        logger.info("Executor node complete: %s", json.dumps(result, default=str))
        return {"trade_signals": trade_signals}

    # ------------------------------------------------------------------
    # Wire up the graph
    # ------------------------------------------------------------------
    graph_builder.add_node("analyst", analyst_node)
    graph_builder.add_node("strategy", strategy_node)
    graph_builder.add_node("tools", tool_node)
    graph_builder.add_node("executor", executor_node)

    graph_builder.add_edge(START, "analyst")
    graph_builder.add_edge("analyst", "strategy")

    # tools_condition inspects the last message in state["messages"]:
    #   has tool_calls  → "tools"
    #   no tool_calls   → <default_sink> which we map to "executor"
    graph_builder.add_conditional_edges(
        "strategy",
        tools_condition,
        {
            "tools": "tools",
            END: "executor",  # tools_condition returns END when there are no tool calls
        },
    )

    # After the tool node executes, always loop back to strategy
    graph_builder.add_edge("tools", "strategy")
    graph_builder.add_edge("executor", END)

    return graph_builder.compile()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def _run_trading_workflow_async(user_id: str) -> None:
    logger.info("Trading workflow started for user_id=%s", user_id)

    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    alphavantage_api_key = os.getenv("ALPHAVANTAGE_API_KEY")

    logger.info("ANTHROPIC_API_KEY present: %s", bool(anthropic_api_key))
    logger.info("ALPHAVANTAGE_API_KEY present: %s", bool(alphavantage_api_key))

    if not anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY is missing. Aborting workflow for user_id=%s", user_id)
        return
    if not alphavantage_api_key:
        logger.error("ALPHAVANTAGE_API_KEY is missing. Aborting workflow for user_id=%s", user_id)
        return

    mcp_command = os.getenv("ALPHA_VANTAGE_MCP_COMMAND", "alphavantage-mcp")
    mcp_args_raw = os.getenv("ALPHA_VANTAGE_MCP_ARGS", "")
    mcp_args = [arg for arg in mcp_args_raw.split(" ") if arg]

    async with AsyncExitStack() as stack:
        server_params = StdioServerParameters(
            command=mcp_command,
            args=mcp_args,
            env={**os.environ, "ALPHAVANTAGE_API_KEY": alphavantage_api_key},
        )
        read_stream, write_stream = await stack.enter_async_context(stdio_client(server_params))

        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        logger.info("MCP session initialized for Alpha Vantage server")

        listed_tools = await session.list_tools()
        mcp_tools = getattr(listed_tools, "tools", []) or []
        langchain_tools_raw = _convert_mcp_tools_to_langchain(session, mcp_tools)

        # Deduplicate and filter to only relevant tools
        all_tools = {tool.name: tool for tool in langchain_tools_raw}
        KEYWORDS = {"price", "quote", "indicator", "rsi", "macd", "sma", "news"}
        filtered_tools = [
            t for t in all_tools.values()
            if any(kw in t.name.lower() for kw in KEYWORDS)
        ] or list(all_tools.values())

        logger.info(
            "Tools: %d total, %d after dedup, %d after keyword filter",
            len(langchain_tools_raw), len(all_tools), len(filtered_tools),
        )

        llm = ChatAnthropic(
            model="claude-opus-4-6",
            temperature=0,
            api_key=anthropic_api_key,
        )
        llm_with_tools = llm.bind_tools(filtered_tools)

        # Graph is built fresh each invocation so it closes over the live
        # MCP session (which is scoped to this AsyncExitStack).
        trading_graph = _build_graph(filtered_tools)

        initial_state: TradingState = {
            "user_id": user_id,
            "portfolio_data": {},
            "messages": [],
            "trade_signals": [],
        }

        final_state = await trading_graph.ainvoke(
            initial_state,
            config={
                "configurable": {
                    "llm_with_tools": llm_with_tools,
                }
            },
        )

        logger.info(
            "Trading workflow finished for user_id=%s with %d trade signals",
            user_id,
            len(final_state.get("trade_signals", [])),
        )
        final_signals = final_state.get("trade_signals", []) or []
        if isinstance(final_signals, list):
            final_summary = _summarize_trade_instructions(final_signals)
            logger.info("Trading workflow final decision summary: %s", json.dumps(final_summary, default=str))
            if final_summary["buy_count"] == 0:
                logger.info(
                    "Trading workflow: no buy signal generated for user_id=%s (sell_count=%d, symbols=%s)",
                    user_id,
                    final_summary["sell_count"],
                    final_summary["symbols"],
                )


def run_trading_workflow(user_id: str) -> None:
    try:
        asyncio.run(_run_trading_workflow_async(user_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Workflow crashed for user_id=%s: %s", user_id, str(exc))