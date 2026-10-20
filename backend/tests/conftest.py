import os

pytest_plugins=['tests.database_support','tests.data_api_support']

os.environ['TRACE_STORE']='0'  # tests must never write traces to a real Supabase project
os.environ['AUDIT_STORE']='0'  # nor audit events (append-only, cannot be cleaned up)
os.environ['GUEST_RATE_CAPACITY']=os.environ['RATE_CAPACITY']='100000'  # one test client address; tests/test_gateway.py checks the limit itself
os.environ['CHAT_TRANSPORT']='api'  # unit suites must never use the signed-in CLI
os.environ['PIPELINE']='v1'  # individual integration fixtures opt in to their pipeline/store
os.environ['KNOWLEDGE_STORE']='memory'
os.environ['CONVERSATION_STORE']='off'
os.environ['RUN_WORKER_IN_API']='false'  # native worker fixtures opt in with their restricted local login
