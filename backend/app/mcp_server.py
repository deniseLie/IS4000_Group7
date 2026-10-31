"""DemoBank tools as a Model Context Protocol server (F25). Requires the `mcp` package (v2).

In-process: tools.call() with TOOL_TRANSPORT=mcp connects an MCP client to this server inside the API process, so
every agent tool call is a real MCP tools/call. Stdio: `python -m scripts.mcp_server` serves the same tools to an
external MCP client (for example an MCP inspector) as a guest, or as a customer with DEMOBANK_ACCESS_TOKEN.
MCP reports a failing tool only as a generic error result, so an in-process call re-raises the tool's own exception
(permission, integrity or provider error) and the orchestrator withholds that section exactly as with direct calls.
"""
import json
from collections.abc import Awaitable,Callable
from contextvars import ContextVar
from functools import lru_cache,wraps
from typing import Any
from app.services.tools import TOOLS

FAILURES: ContextVar[list[BaseException] | None]=ContextVar('mcp_tool_failures',default=None)


class ToolCallError(RuntimeError):
    """The MCP server reported a tool error; the original is in the server log."""


@lru_cache(maxsize=1)
def build_server():
    from mcp.server.mcpserver import MCPServer
    server=MCPServer('demobank-tools',instructions='DemoBank coursework tools. Every call is permission-checked and audited; '
        'personal tools need a signed-in customer. Tools read records only; they never change banking data.')
    for name,function in TOOLS.items():server.tool(name=name,description=(function.__doc__ or name).strip())(recorded(function))
    return server


def recorded(function: Callable[...,Awaitable[dict[str,Any]]]) -> Callable[...,Awaitable[dict[str,Any]]]:
    """Keep the tool's exception for the in-process caller; stdio clients still get MCP's error result."""
    @wraps(function)
    async def run(**kwargs: Any) -> dict[str,Any]:
        try:return await function(**kwargs)
        except Exception as error:
            failures=FAILURES.get()
            if failures is not None:failures.append(error)
            raise
    return run


def result_data(result: Any) -> dict[str,Any]:
    if result.is_error:raise ToolCallError('The tool reported an error')
    data=result.structured_content
    if isinstance(data,dict) and set(data)=={'result'} and isinstance(data['result'],dict):data=data['result']
    if not isinstance(data,dict):data=json.loads(result.content[0].text)
    return data


async def call_in_process(name: str,args: dict[str,Any]) -> dict[str,Any]:
    from mcp import Client
    failures: list[BaseException]=[]
    token=FAILURES.set(failures)
    try:
        async with Client(build_server(),raise_exceptions=True) as client:result=await client.call_tool(name,args)
    finally:FAILURES.reset(token)
    if result.is_error and failures:raise failures[0]
    return result_data(result)
