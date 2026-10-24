"""Local OCR adapter: tesseract TSV or the bundled macOS Vision command. No network access."""
import csv
import io
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile


def execute(arguments: list[str],folder: Path,name: str) -> bytes:
    with (folder/name).open('wb') as output:
        run=subprocess.run(arguments,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.DEVNULL,timeout=12,check=False)
    file=folder/name
    if run.returncode or file.stat().st_size>1000000:raise ValueError('OCR command failed')
    return file.read_bytes()


def main() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE,(16*1024*1024,16*1024*1024))
    payload=sys.stdin.buffer.read(10485761)
    if not 1<=len(payload)<=10485760:raise ValueError('OCR size limit')
    command=Path(os.environ['DOCUMENT_OCR_COMMAND'])
    if not command.is_absolute() or not command.is_file():raise ValueError('Configure an absolute local OCR executable')
    with tempfile.TemporaryDirectory(prefix='demobank-ocr-') as directory:
        folder=Path(directory);source=folder/'source';source.write_bytes(payload)
        if sys.argv[1]=='application/pdf':
            renderer=shutil.which('pdftoppm')
            if not renderer:raise ValueError('PDF OCR requires pdftoppm')
            execute([renderer,'-f','1','-l','20','-scale-to','1800','-png',str(source),str(folder/'page')],folder,'renderer.txt')
            images=sorted(folder.glob('page-*.png'))
        else:images=[source]
        if not images or len(images)>20:raise ValueError('OCR page limit')
        output=[]
        for index,file in enumerate(images):
            rows=csv.DictReader(io.StringIO(execute([str(command),str(file),'stdout','-l',os.environ.get('DOCUMENT_OCR_LANGUAGES','eng'),'tsv'],folder,f'ocr-{index}.tsv').decode()),delimiter='\t')
            lines={};scores=[]
            for row in rows:
                word=row.get('text','').strip()
                if not word:continue
                confidence=float(row['conf'])
                if confidence<float(os.environ.get('DOCUMENT_OCR_WORD_CONFIDENCE','80')):raise ValueError('OCR confidence too low')
                scores.append(confidence)
                key=tuple(row.get(k,'') for k in ('page_num','block_num','par_num','line_num'))
                lines.setdefault(key,[]).append(word)
            if not scores or sum(scores)/len(scores)<float(os.environ.get('DOCUMENT_OCR_MEAN_CONFIDENCE','90')):raise ValueError('OCR confidence too low')
            output.append('\n'.join(' '.join(words) for words in lines.values()))
        text='\n'.join(output)
        if len(text)>32000:raise ValueError('OCR text limit')
        print(json.dumps({'text':text,'pages':len(images)}))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,subprocess.TimeoutExpired):print(json.dumps({'text':'','pages':1}))
