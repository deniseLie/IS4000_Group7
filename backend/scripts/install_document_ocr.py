"""Build the local macOS Vision adapter; otherwise configure an existing tesseract executable."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main() -> None:
    compiler=shutil.which('swiftc')
    if sys.platform!='darwin' or not compiler:raise RuntimeError('On other platforms set DOCUMENT_OCR_COMMAND to an absolute tesseract path')
    destination=Path(sys.executable).parent/'demobank-document-ocr'
    with tempfile.TemporaryDirectory(prefix='demobank-swift-') as cache:
        subprocess.run([compiler,'-module-cache-path',cache,str(Path(__file__).with_name('document_ocr.swift')),'-o',str(destination)],check=True)
    print('Set DOCUMENT_OCR_COMMAND='+str(destination.resolve()))


if __name__=='__main__':main()
