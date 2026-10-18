import os

os.environ['TRACE_STORE']='0'  # tests must never write traces to a real Supabase project
os.environ['AUDIT_STORE']='0'  # nor audit events
os.environ['CHAT_TRANSPORT']='api'  # unit suites must never use the signed-in CLI
