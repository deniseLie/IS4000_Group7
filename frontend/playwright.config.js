import {defineConfig} from '@playwright/test';
export default defineConfig({
 testDir:'./tests',testMatch:'chat.spec.js',timeout:30000,workers:1,
 use:{baseURL:'http://127.0.0.1:5177',headless:true},
 webServer:[
  {command:'../.venv/bin/python -m uvicorn tests.browser_server:app --app-dir ../backend --host 127.0.0.1 --port 8037',url:'http://127.0.0.1:8037/health',reuseExistingServer:false},
  {command:'npm run dev -- --host 127.0.0.1 --port 5177',url:'http://127.0.0.1:5177',env:{VITE_API_URL:'/api',BACKEND_PORT:'8037'},reuseExistingServer:false}
 ]
});
