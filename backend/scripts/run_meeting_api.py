"""Start the normal FastAPI app with the reviewed private meeting configuration; no .env edits or secrets in argv.

Run from backend: python -m scripts.run_meeting_api --runtime /absolute/private/runtime.json --port 8000.
The API receives the own-user OAuth directory; worker credentials and its checkpoint key stay outside this process.
"""
import argparse,os,subprocess,sys
from pathlib import Path
from app.services.meetings.runtime import api_environment,read

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,required=True);parser.add_argument('--port',type=int,required=True)
    args=parser.parse_args()
    if not 1024<=args.port<=65535:raise ValueError('Use an unprivileged API port from 1024 to 65535')
    configuration=read(args.runtime)
    raise SystemExit(subprocess.call([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(args.port),'--no-access-log'],
        env=api_environment(configuration,os.environ)))
