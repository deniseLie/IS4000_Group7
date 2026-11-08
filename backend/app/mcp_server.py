"""UBS Connect tools as a Model Context Protocol server (F21; `mcp` v2 is a core dependency).

In-process: tools.call() (and tools.meeting_read, for the meeting agents) sends every tool read to this server as a real
MCP tools/call, through one shared client per event loop (the API opens it at startup, core/lifecycle.tool_lifespan, so the first chat does not pay the SDK import). The server runs the tool inline in the caller's task, so the tool_call
span, audit batch and trace are the caller's. The caller's identity is never a tool argument: each call registers its
RequestContext under an opaque one-time key in CALLS and sends only the key in the request's `_meta`; the server looks it
up per call and refuses a call without one, or with a key issued for another tool. A meeting agent's read
(tools.meeting_read) also registers its MeetingAccess under the key. The server is read-only: writes (actions, offers,
approved RM writes, uploads, insight actions, meeting drafts) and the meeting worker's private login keep their direct calls.

Stdio: `python -m scripts.mcp_server` serves the same tool functions to an external MCP client (for example an MCP
inspector) as a guest, or as a customer with DEMOBANK_ACCESS_TOKEN; it has no agent specs, so no field views or limits.
"""
import asyncio
import contextvars
import inspect
import json
import typing
from collections.abc import Awaitable,Callable
from functools import lru_cache
from typing import Any,NamedTuple
from uuid import uuid4
from weakref import WeakKeyDictionary
from app.schemas.contracts import RequestContext
from app.schemas.meeting_agents import MeetingAccess
from app.services import tools

CALL_KEY='demobank/call'  # the `_meta` key that names the call; its value is opaque and used once


class Call(NamedTuple):
    tool: str  # the one tool this key may run
    ctx: RequestContext
    access: MeetingAccess | None  # a meeting agent's read (tools.meeting_read); None for every other tool
    failures: list[Exception]


CALLS: dict[str,Call]={}
_clients: WeakKeyDictionary[asyncio.AbstractEventLoop,tuple[asyncio.Future[Any],asyncio.Task[None]]]=WeakKeyDictionary()


class ToolCallError(RuntimeError):
    """The MCP server reported a tool error without the original exception."""


def served(name: str,function: Callable[...,Awaitable[dict[str,Any]]]) -> Callable[...,Awaitable[dict[str,Any]]]:
    """The MCP handler for one tool: the tool's own arguments (its schema) plus the SDK's request Context, from which the
    call's key in `_meta` gives the caller's RequestContext (and a meeting read's MeetingAccess). The tool is looked up at
    call time (tools.TOOLS, tools.MEETING_READS)."""
    from mcp.server.mcpserver import Context
    from mcp.server.mcpserver.exceptions import ToolError
    async def run(mcp_call: Context,**args: Any) -> dict[str,Any]:
        call=CALLS.get(str((mcp_call.request_context.meta or {}).get(CALL_KEY)))
        if call is None or call.tool!=name:raise ToolError('no request context')
        token,access=tools.TOOL_CTX.set(call.ctx),tools.MEETING_ACCESS.set(call.access)
        try:return await (tools.TOOLS[name] if name in tools.TOOLS else tools.MEETING_READS[name])(**args)
        except Exception as error:
            call.failures.append(error)  # the caller re-raises the original; the protocol carries only its type
            raise ToolError(type(error).__name__) from error
        finally:
            tools.MEETING_ACCESS.reset(access);tools.TOOL_CTX.reset(token)
    signature=inspect.signature(function)
    run.__signature__=signature.replace(parameters=[*signature.parameters.values(),
        inspect.Parameter('mcp_call',inspect.Parameter.KEYWORD_ONLY,annotation=Context)])
    run.__annotations__={**typing.get_type_hints(function),'mcp_call':Context}
    run.__name__,run.__doc__=name,function.__doc__
    return run


@lru_cache(maxsize=1)
def build_server():
    from mcp.server.mcpserver import MCPServer
    server=MCPServer('demobank-tools',instructions='UBS Connect coursework tools. Every call is permission-checked and audited; '
        'personal tools need a signed-in customer. Tools read records only; they never change banking data.')
    for name,function in tools.SERVED.items():server.tool(name=name,description=(function.__doc__ or name).strip())(served(name,function))
    return server


def result_data(result: Any) -> dict[str,Any]:
    if result.is_error:raise ToolCallError('The tool reported an error')
    data=result.structured_content
    if isinstance(data,dict) and set(data)=={'result'} and isinstance(data['result'],dict):data=data['result']
    if not isinstance(data,dict):data=json.loads(result.content[0].text)
    return data


async def _hold(ready: asyncio.Future[Any]) -> None:
    """Open the loop's client and keep it open until the loop cancels this task (at shutdown)."""
    from mcp import Client
    try:
        async with Client(build_server(),raise_exceptions=True,cache=None) as client:  # no response cache: every call is a new read
            ready.set_result(client)
            await asyncio.Event().wait()
    except BaseException as error:
        if not ready.done():ready.set_exception(error if isinstance(error,Exception) else ToolCallError('MCP client closed'))
        raise


async def client() -> Any:
    """This event loop's shared MCP client, opened on first use by a task with an empty context (no caller's spans)."""
    loop=asyncio.get_running_loop()
    held=_clients.get(loop)
    if held is None or held[1].done():
        ready: asyncio.Future[Any]=loop.create_future()
        held=_clients[loop]=(ready,loop.create_task(_hold(ready),context=contextvars.Context()))
    return await asyncio.shield(held[0])


async def close() -> None:
    """Close this event loop's client (API shutdown); a later call opens a new one."""
    held=_clients.pop(asyncio.get_running_loop(),None)
    if held is None:return
    held[1].cancel()
    await asyncio.gather(held[1],return_exceptions=True)


async def call_in_process(name: str,args: dict[str,Any],ctx: RequestContext,access: MeetingAccess | None=None) -> dict[str,Any]:
    """One MCP tools/call as the caller: its context (and a meeting read's access) goes in CALLS under a one-time key for
    this tool, never in the arguments."""
    key,failures=uuid4().hex,[]
    CALLS[key]=Call(name,ctx,access,failures)
    try:
        result=await (await client()).call_tool(name,args,meta={CALL_KEY:key})
        if failures:raise failures[0]
        return result_data(result)
    finally:CALLS.pop(key,None)
