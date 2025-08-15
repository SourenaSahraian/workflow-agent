# pip install langgraph langchain-core
from __future__ import annotations
import time, random
from typing import TypedDict, Optional, Dict, Any, List

from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_core.tools import tool

# ---------- State ----------
class State(TypedDict, total=False):
    ticker: str
    messages: List[dict]              # ToolNode works over a messages list
    data: Dict[str, Any]
    error: Dict[str, str]
    attempts: int
    max_attempts: int
    last_good: Dict[str, Any]

# ---------- A flaky tool (simulates rate limit + schema fails) ----------
@tool
def lookup_price(ticker: str) -> Dict[str, Any]:
    """Look up a stock price (simulated; randomly fails)."""
    r = random.random()
    if r < 0.3:
        # transient
        raise TimeoutError("Transient timeout talking to price API")
    if 0.3 <= r < 0.4:
        # hard (non-retryable)
        raise ValueError("Bad response schema from provider")
    return {"ticker": ticker.upper(), "price": round(100 + random.random() * 20, 2)}

# ---------- Utilities ----------
def backoff_sleep(attempt: int):
    # 1->0.5s, 2->1s, 3->2s ...
    delay = 0.5 * (2 ** max(0, attempt - 1))
    time.sleep(delay)

def is_tool_error_message(msg: dict) -> Optional[str]:
    """
    Minimal heuristic for ToolNode error messages.
    When handle_tool_errors=True, ToolNode places the exception text in the tool message.
    Adapt this to your message schema if you wrap/normalize messages differently.
    """
    if msg.get("type") == "tool" and isinstance(msg.get("content"), str):
        txt = msg["content"]
        # You can choose your own sentinel; here we just treat any plain string as an error text.
        # If your tool returns dicts on success, and strings only on error, this works well.
        return txt
    return None

# ---------- Nodes ----------
# 1) Ask for a tool call (in a real app, this would be the LLM's choice/output)
def plan_tool_call(state: State) -> State:
    call = {
        "type": "tool_call",
        "tool_name": "lookup_price",
        "arguments": {"ticker": state["ticker"]},
    }
    return {"messages": state.get("messages", []) + [call]}

# 2) The ToolNode (lets LLM/tool protocol run; catches tool errors into messages)
tools_node = ToolNode([lookup_price], handle_tool_errors=True)

# 3) Extract success OR flag error in state
def parse_tool_result(state: State) -> State:
    msgs = state.get("messages", [])
    if not msgs:
        return {"error": {"kind": "internal", "msg": "No messages after ToolNode"}}

    last = msgs[-1]
    # Success path: tool returned a dict payload
    if last.get("type") == "tool" and isinstance(last.get("content"), dict):
        payload = last["content"]
        return {
            "data": payload,
            "last_good": payload,
            "error": {},  # clear error if any
        }

    # Error path: tool "content" is a string (the exception text)
    err_text = is_tool_error_message(last)
    if err_text:
        return {"error": {"kind": "tool_error", "msg": err_text}}

    # Fallback: unknown message type
    return {"error": {"kind": "unknown", "msg": f"Unexpected tool message: {last}"}}

# 4) Retry controller: if error + attempts < max → sleep + re-issue the tool call
def maybe_retry(state: State) -> State:
    if not state.get("error"):
        return {}
    attempts = state.get("attempts", 0) + 1
    max_attempts = state.get("max_attempts", 3)

    # Classify retryability: here we retry only "transient" style errors; simple rule:
    msg = state["error"].get("msg", "").lower()
    retryable = "timeout" in msg or "rate" in msg or "transient" in msg

    if attempts <= max_attempts and retryable:
        backoff_sleep(attempts)
        # append another tool_call; ToolNode will run again
        call = {
            "type": "tool_call",
            "tool_name": "lookup_price",
            "arguments": {"ticker": state["ticker"]},
        }
        return {
            "attempts": attempts,
            "messages": state.get("messages", []) + [call],
        }

    # Give up (either exceeded attempts or non-retryable error)
    return {"attempts": attempts}

# 5) Repair: degrade gracefully (cached/stale or clean "unavailable")
def repair(state: State) -> State:
    if state.get("last_good"):
        return {"data": {**state["last_good"], "stale": True}}
    return {"data": {"ticker": state["ticker"].upper(), "price": None, "note": "unavailable"}}

# ---------- Graph wiring with automatic retries around ToolNode ----------
g = StateGraph(State)

g.add_node("plan",     plan_tool_call)
g.add_node("tools",    tools_node)
g.add_node("parse",    parse_tool_result)
g.add_node("maybe_retry", maybe_retry)
g.add_node("repair",   repair)

g.set_entry_point("plan")
g.add_edge("plan", "tools")
g.add_edge("tools", "parse")

def branch_after_parse(state: State) -> str:
    # If there is no error → END
    if not state.get("error"):
        return END
    # Otherwise decide: retry or repair
    attempts = state.get("attempts", 0)
    max_attempts = state.get("max_attempts", 3)
    msg = (state.get("error", {}) or {}).get("msg", "").lower()
    retryable = "timeout" in msg or "rate" in msg or "transient" in msg
    if retryable and attempts < max_attempts:
        return "maybe_retry"
    return "repair"

g.add_conditional_edges("parse", branch_after_parse,
                        {"maybe_retry": "maybe_retry", "repair": "repair", END: END})

# If we decided to retry, jump back into the ToolNode
g.add_edge("maybe_retry", "tools")

graph = g.compile(recursion_limit=50)

# ---------- Demo ----------
if __name__ == "__main__":
    random.seed()  # try multiple runs to see retry/repair behaviors
    # Case 1: normal run with up to 3 attempts
    out = graph.invoke({"ticker": "NCLH", "max_attempts": 3})
    print("OUTPUT:", out)

    # You can force-retry behavior by temporarily biasing the random fail rates in the tool.