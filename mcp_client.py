"""
mcp_client.py
=============
A tiny **sync -> async bridge** that lets the synchronous, Flask-threaded brain
(`chatbot.py`) talk to an MCP server.

The MCP Python SDK is asyncio-based and a stdio session must stay open for its
whole lifetime, but `chatbot.answer()` is a plain blocking function called from
WhatsApp webhook threads. So we:

  1. Spin up ONE background thread running a persistent asyncio event loop.
  2. On that loop, spawn the MCP server (`mcp_server.py`) over stdio, open a
     `ClientSession`, and keep it alive via an `AsyncExitStack`.
  3. Expose blocking helpers — `get_tools()` and `call_tool()` — that submit
     coroutines to the background loop and wait for the result.

A single shared session is reused across all requests; `call_tool` is guarded by
an asyncio lock so concurrent webhook threads serialize on the stdio stream
instead of interleaving their JSON-RPC frames.

Public API:
    get_tools() -> list[dict]            # Anthropic-shaped tool definitions
    call_tool(name, arguments) -> str    # run an MCP tool, return its text
"""

import asyncio
import atexit
import os
import sys
import threading
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# The MCP server we manage: this repo's company-docs server, same interpreter.
_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
_SERVER_PARAMS = StdioServerParameters(
    command=sys.executable,
    args=[os.path.join(_REPO_DIR, "mcp_server.py")],
    cwd=_REPO_DIR,
    env=os.environ.copy(),
)

# --- Background event loop running in its own thread -----------------------
_loop = asyncio.new_event_loop()
_thread = threading.Thread(target=_loop.run_forever, name="mcp-loop", daemon=True)
_thread.start()

# State owned by the background loop (only touched from coroutines on it).
_stack: AsyncExitStack | None = None
_session: ClientSession | None = None
_call_lock: asyncio.Lock | None = None
_ready = False


def _run(coro):
    """Submit a coroutine to the background loop and block for its result."""
    return asyncio.run_coroutine_threadsafe(coro, _loop).result()


async def _ensure_session() -> ClientSession:
    """Open (once) and return the persistent MCP client session."""
    global _stack, _session, _call_lock, _ready
    if _ready and _session is not None:
        return _session

    _stack = AsyncExitStack()
    read, write = await _stack.enter_async_context(stdio_client(_SERVER_PARAMS))
    session = await _stack.enter_async_context(ClientSession(read, write))
    await session.initialize()

    _session = session
    _call_lock = asyncio.Lock()
    _ready = True
    return session


def get_tools() -> list[dict]:
    """Return the server's tools as Anthropic tool definitions."""

    async def _list():
        session = await _ensure_session()
        result = await session.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            }
            for tool in result.tools
        ]

    return _run(_list())


def call_tool(name: str, arguments: dict) -> str:
    """Invoke an MCP tool by name and return its text output."""

    async def _call():
        session = await _ensure_session()
        async with _call_lock:  # serialize the shared stdio session
            result = await session.call_tool(name, arguments or {})
        # Concatenate any text blocks the tool returned.
        parts = [
            block.text
            for block in result.content
            if getattr(block, "type", None) == "text"
        ]
        text = "\n".join(p for p in parts if p).strip()
        if result.isError:
            return f"[tool error] {text or 'unknown error'}"
        return text or "(no result)"

    return _run(_call())


@atexit.register
def _shutdown() -> None:
    """Close the MCP session and stop the background loop on interpreter exit."""
    if _stack is not None:
        try:
            _run(_stack.aclose())
        except Exception:  # best-effort cleanup
            pass
    _loop.call_soon_threadsafe(_loop.stop)
