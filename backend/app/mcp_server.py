"""DemoBank tools as a Model Context Protocol server (F21). Requires the optional `mcp` package (v2).

In-process: tools.call() with TOOL_TRANSPORT=mcp connects an MCP client to this server inside the API process, so
every agent tool call is a real MCP tools/call. Stdio: `python -m scripts.mcp_server` serves the same tools to an
external MCP client (for example an MCP inspector) as a guest, or as a customer with DEMOBANK_ACCESS_TOKEN.
"""
import json
from functools import lru_cache,wraps
from contextvars import ContextVar
from collections.abc import Awaitable,Callable
from typing import Any,ParamSpec,TypeVar
from app.services.tools import TOOLS

tool_failures: ContextVar[list[Exception] | None]=ContextVar('mcp_tool_failures',default=None)
P=ParamSpec('P')
R=TypeVar('R')


class ToolCallError(RuntimeError):
    """The MCP server reported a tool error; the original is in the server log."""


def capture_failure(function: Callable[P,Awaitable[R]]) -> Callable[P,Awaitable[R]]:
    """Preserve an in-process tool's exception across the SDK's protocol-error conversion."""
    @wraps(function)
    async def wrapped(*args: P.args,**kwargs: P.kwargs) -> R:
        try:return await function(*args,**kwargs)
        except Exception as error:
            failures=tool_failures.get()
            if failures is not None:failures.append(error)
            raise
    return wrapped


@lru_cache(maxsize=1)
def build_server():
    from mcp.server.mcpserver import MCPServer
    server=MCPServer('demobank-tools',instructions='DemoBank coursework tools. Every call is permission-checked and audited; '
        'personal tools need a signed-in customer. Tools read records only; they never change banking data.')
    for name,function in TOOLS.items():server.tool(name=name,description=(function.__doc__ or name).strip())(capture_failure(function))
    return server


def result_data(result: Any) -> dict[str,Any]:
    if result.is_error:raise ToolCallError('The tool reported an error')
    data=result.structured_content
    if isinstance(data,dict) and set(data)=={'result'} and isinstance(data['result'],dict):data=data['result']
    if not isinstance(data,dict):data=json.loads(result.content[0].text)
    return data


async def call_in_process(name: str,args: dict[str,Any]) -> dict[str,Any]:
    from mcp import Client
    failures: list[Exception]=[]
    token=tool_failures.set(failures)
    try:
        async with Client(build_server(),raise_exceptions=True) as client:result=await client.call_tool(name,args)
        if failures:raise failures[0]
        return result_data(result)
    finally:tool_failures.reset(token)
