import {defineConfig} from '@playwright/test';
if(process.env.TEST_ENV_FILE)process.loadEnvFile(process.env.TEST_ENV_FILE);
export default defineConfig({
 testDir:'./tests',testMatch:'live.spec.js',timeout:90000,workers:1,
 use:{baseURL:'http://127.0.0.1:5178',headless:true},
 webServer:[
  {command:'../.venv/bin/python -m uvicorn app.main:app --app-dir ../backend --host 127.0.0.1 --port 8038',url:'http://127.0.0.1:8038/health'},
  {command:'npm run dev -- --host 127.0.0.1 --port 5178',url:'http://127.0.0.1:5178',env:{VITE_API_URL:'/api',BACKEND_PORT:'8038'}}
 ]
});
