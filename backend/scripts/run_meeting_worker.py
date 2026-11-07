"""Run the existing restricted meeting worker with a private DB URL, approved Zoom grant and stable checkpoint key.

Run from backend: python -m scripts.run_meeting_worker --runtime /absolute/private/runtime.json.
The worker has no RM login or banking-reader grants. The runtime file must pass owner/permission checks.
"""
import argparse,os,subprocess,sys
from pathlib import Path
from app.services.meetings.runtime import read,worker_environment

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runtime',type=Path,required=True)
    args=parser.parse_args();configuration=read(args.runtime)
    raise SystemExit(subprocess.call([sys.executable,'-m','app.services.meetings.worker'],env=worker_environment(configuration,os.environ)))
