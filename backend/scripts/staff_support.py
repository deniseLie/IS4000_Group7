"""Operate synthetic support handoffs with a support staff member's existing aal2 access token.

An administrator must assign app_metadata.demobank_role='support' in Supabase Auth;
user_metadata is never trusted. The staff member must enrol MFA and obtain an aal2
access token through Auth. Hosted provisioning/login remain an operator task.

Set SUPABASE_URL, the public SUPABASE_ANON_KEY and STAFF_ACCESS_TOKEN in the named
private, git-ignored env file. Never use a service-role key. Examples, from backend/:
  python -m scripts.staff_support --env-file ../.env.staff queue --limit 20
  python -m scripts.staff_support --env-file ../.env.staff claim --handoff UUID --command UUID
  python -m scripts.staff_support --env-file ../.env.staff unblock --handoff UUID --command UUID --card card_10011
  python -m scripts.staff_support --env-file ../.env.staff cancel --handoff UUID --command UUID --card card_10011
  python -m scripts.staff_support --env-file ../.env.staff close --handoff UUID --command UUID

Choose a fresh command UUID for a new decision; reuse that UUID after a network
failure to retrieve its original receipt. A failed receipt is final for that UUID.
This module prints recorded, synthetic results for its operator; it never prints tokens.
"""
import argparse
import asyncio
import json
from io import StringIO
from pathlib import Path
from uuid import UUID,uuid4
from dotenv import dotenv_values
from postgrest import SyncPostgrestClient
from app.schemas.staff_settings import StaffSettings
from app.services.actions import staff


async def run(args: argparse.Namespace) -> None:
    values=dotenv_values(stream=StringIO(Path(args.env_file).read_text()),interpolate=False)
    required={'SUPABASE_URL','SUPABASE_ANON_KEY','STAFF_ACCESS_TOKEN'}
    if missing:=required-values.keys():raise ValueError('Staff env file is missing required settings: '+', '.join(sorted(missing)))
    config=StaffSettings(supabase_url=values['SUPABASE_URL'],public_key=values['SUPABASE_ANON_KEY'],
        access_token=values['STAFF_ACCESS_TOKEN'])
    with SyncPostgrestClient(config.supabase_url.rstrip('/')+'/rest/v1',
        headers={'apikey':config.public_key.get_secret_value(),'X-Request-ID':str(uuid4())}) as db:
        db.auth(config.access_token.get_secret_value())
        if args.operation=='queue':
            print(json.dumps([item.model_dump(mode='json') for item in await staff.queue(args.limit,db)],indent=2))
            return
        if args.operation=='claim':outcome=await staff.claim(args.command,args.handoff,db)
        elif args.operation=='close':outcome=await staff.close(args.command,args.handoff,db)
        elif args.operation=='unblock':outcome=await staff.unblock(args.command,args.handoff,args.card,db)
        elif args.operation=='cancel':outcome=await staff.cancel(args.command,args.handoff,args.card,db)
        else:raise ValueError('Staff operation is not registered: '+args.operation)
        print(outcome.model_dump_json(indent=2))


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--env-file',required=True)
    operations=parser.add_subparsers(dest='operation',required=True)
    queue=operations.add_parser('queue')
    queue.add_argument('--limit',required=True,type=int)
    for name in ['claim','close','unblock','cancel']:
        command=operations.add_parser(name)
        command.add_argument('--handoff',required=True,type=UUID)
        command.add_argument('--command',required=True,type=UUID)
        if name in ['unblock','cancel']:command.add_argument('--card',required=True)
    asyncio.run(run(parser.parse_args()))


if __name__=='__main__':main()
