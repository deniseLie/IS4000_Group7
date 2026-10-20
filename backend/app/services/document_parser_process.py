"""Run untrusted file parsing in a child with no application credentials.

Kill and reap on cancellation, a five-second wall budget, or sampled RSS above 256 MiB.
RSS sampling every 20 ms is a watchdog, not an OS-enforced hard memory ceiling. Format
readers also enforce byte/decode/tree/text/page/pixel budgets. No file contents are logged.
"""
import asyncio
import os
import sys
from pathlib import Path
import psutil
from app.schemas.document_inspection import ParsedDocument
from app.schemas.document_uploads import DocumentMime
from app.services.document_parsers import empty_result


class DocumentParserError(RuntimeError):
    """The isolated parser failed unexpectedly instead of returning a verified technical result."""


def memory_bytes(pid: int) -> int | None:
    try:return psutil.Process(pid).memory_info().rss
    except psutil.NoSuchProcess:return None
    except psutil.AccessDenied as error:raise DocumentParserError('The parser child process memory usage could not be monitored') from error


async def read_output(reader: asyncio.StreamReader,limit: int,label: str) -> bytes:
    pieces: list[bytes]=[];total=0
    while chunk:=await reader.read(min(65536,limit-total+1)):
        total+=len(chunk)
        if total>limit:raise DocumentParserError(f'Document parser {label} exceeded {limit} bytes; file text is withheld')
        pieces.append(chunk)
    return b''.join(pieces)


async def exchange(process: asyncio.subprocess.Process,data: bytes) -> tuple[bytes,bytes]:
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise DocumentParserError('Document parser requires private stdin, stdout and stderr pipes')
    output=asyncio.create_task(read_output(process.stdout,196608,'output'))
    errors=asyncio.create_task(read_output(process.stderr,4096,'diagnostics'))
    try:
        process.stdin.write(data)
        try:await process.stdin.drain()
        except (BrokenPipeError,ConnectionResetError) as error:
            raise DocumentParserError('Document parser closed its input before the file was transferred') from error
        finally:process.stdin.close()
        stdout,stderr=await asyncio.gather(output,errors)
        await process.wait()
        return stdout,stderr
    finally:
        # Collect cancelled readers during cleanup; their original errors still propagate above.
        for task in (output,errors):
            if not task.done():task.cancel()
        await asyncio.gather(output,errors,return_exceptions=True)


async def parse(data: bytes,mime_type: DocumentMime) -> ParsedDocument:
    if not 1<=len(data)<=10485760:return empty_result('limits_exceeded','file_limits',None)
    environment={name:os.environ[name] for name in ['PATH','SYSTEMROOT'] if name in os.environ}
    environment.update({'PYTHONIOENCODING':'utf-8','PYTHONNOUSERSITE':'1'})
    process=await asyncio.create_subprocess_exec(sys.executable,'-m','app.services.document_parser_worker',mime_type,
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
        cwd=Path(__file__).resolve().parents[2],env=environment,limit=196608)
    result=asyncio.create_task(exchange(process,data),name='document-parser-output')
    try:
        async with asyncio.timeout(5):
            while not result.done():
                resident=memory_bytes(process.pid)
                if resident is None:
                    await asyncio.shield(result)
                    break
                if resident>256*1024*1024:
                    return empty_result('limits_exceeded','file_limits',None)
                await asyncio.sleep(0.02)
            output,errors=await asyncio.shield(result)
        if process.returncode!=0:
            # Stderr can contain untrusted file fragments. Report only the worker exit code.
            raise DocumentParserError(f'Document parser exited with code {process.returncode}; MIME {mime_type}, input bytes {len(data)}. File text is withheld.')
        if errors:raise DocumentParserError('Document parser emitted diagnostics; technical results were withheld instead of silently accepting parser warnings')
        return ParsedDocument.model_validate_json(output)
    except TimeoutError:return empty_result('limits_exceeded','file_limits',None)
    finally:
        if process.returncode is None:process.kill()
        if not result.done():result.cancel()
        # Cleanup must not replace a timeout, cancellation or the original parser failure.
        await asyncio.gather(result,return_exceptions=True)
        # A killed child can leave paused pipe buffers; drain without retaining file fragments.
        for reader in (process.stdout,process.stderr):
            if reader is not None:
                while await reader.read(65536):pass
        await process.wait()
