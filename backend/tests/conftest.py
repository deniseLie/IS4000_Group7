import os

os.environ['TRACE_STORE']='0'  # tests must never write traces to a real Supabase project
