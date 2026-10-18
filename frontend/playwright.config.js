import {defineConfig} from '@playwright/test';
if(process.env.TEST_ENV_FILE)process.loadEnvFile(process.env.TEST_ENV_FILE);
export default defineConfig({
 testDir:'./tests',testMatch:['chat.spec.js','live.spec.js'],timeout:180000,workers:1,
 reporter:[['list'],['html',{open:'never'}]],
 use:{baseURL:'http://127.0.0.1:5178',headless:true,screenshot:'only-on-failure',trace:'retain-on-failure'},
 webServer:[
  {command:'../.venv/bin/python -m uvicorn app.main:app --app-dir ../backend --host 127.0.0.1 --port 8038',url:'http://127.0.0.1:8038/health',env:{TRACE_STORE:'0',AUDIT_STORE:'0',GUEST_RATE_CAPACITY:'100000',RATE_CAPACITY:'100000',PROMPT_VERSION:process.env.PROMPT_VERSION||'v2',LANGSMITH_TRACING:'false',LANGCHAIN_TRACING_V2:'false'},reuseExistingServer:false},
  {command:'npm run dev -- --host 127.0.0.1 --port 5178 --strictPort',url:'http://127.0.0.1:5178',env:{VITE_API_URL:'/api',BACKEND_PORT:'8038'},reuseExistingServer:false}
 ]
});
