"""Bounded local OCR subprocess; inherits no app credentials and never logs extracted text."""
import asyncio
import json
import os
import signal
import sys
from pathlib import Path
import psutil
from app.core.config import settings
from app.services.document_parser_process import DocumentParserError,exchange


async def extract(payload: bytes,mime: str,pages: int | None) -> tuple[str,int | None]:
    if not settings.document_ocr_command or os.name!='posix':return '',pages
    environment={name:os.environ[name] for name in ('PATH','SYSTEMROOT') if name in os.environ}
    environment['DOCUMENT_OCR_COMMAND']=settings.document_ocr_command
    environment.update(DOCUMENT_OCR_LANGUAGES=settings.document_ocr_languages,DOCUMENT_OCR_VISION_LANGUAGES=settings.document_ocr_vision_languages,
        DOCUMENT_OCR_WORD_CONFIDENCE=str(settings.document_ocr_word_confidence),DOCUMENT_OCR_MEAN_CONFIDENCE=str(settings.document_ocr_mean_confidence))
    environment['PYTHONIOENCODING']='utf-8'
    environment['PYTHONNOUSERSITE']='1'
    process=await asyncio.create_subprocess_exec(sys.executable,'-m','app.services.document_ocr_worker',mime,
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
        cwd=Path(__file__).resolve().parents[2],env=environment,start_new_session=True)
    result=asyncio.create_task(exchange(process,payload))
    try:
        async with asyncio.timeout(20):
            while not result.done():
                try:
                    parent=psutil.Process(process.pid)
                    used=sum(p.memory_info().rss for p in [parent,*parent.children(recursive=True)] if p.is_running())
                except psutil.NoSuchProcess:used=0
                if used>512*1024*1024:return '',pages
                await asyncio.sleep(0.02)
            stdout,stderr=await result
            if process.returncode or stderr:return '',pages
            value=json.loads(stdout)
            text=value.get('text')
            count=value.get('pages')
            if not isinstance(text,str) or len(text)>32000 or not isinstance(count,int) or not 1<=count<=20:return '',pages
            return text,count
    except (TimeoutError,ValueError,DocumentParserError,psutil.Error):return '',pages
    finally:
        # Kill the whole process group, including renderer/OCR grandchildren, on every exit.
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        if not result.done():result.cancel()
        await asyncio.gather(result,return_exceptions=True)
        await process.wait()
