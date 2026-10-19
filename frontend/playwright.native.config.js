import {defineConfig} from '@playwright/test';

if(!process.env.NATIVE_BROWSER_FIXTURE)throw new Error('Run native browser suites through the backend pytest browser integration tests.');
const port=process.env.NATIVE_FRONTEND_PORT;
export default defineConfig({
  testDir:'./tests',testMatch:['consent.spec.js','relationship.spec.js'],timeout:30000,workers:1,reporter:[['list']],
  outputDir:`${process.env.NATIVE_SCREENSHOT_DIR}/artifacts`,
  use:{baseURL:`http://127.0.0.1:${port}`,headless:true,screenshot:'only-on-failure',trace:'retain-on-failure'},
  webServer:{command:`node node_modules/vite/bin/vite.js --host 127.0.0.1 --port ${port} --strictPort`,
    url:`http://127.0.0.1:${port}`,reuseExistingServer:false,
    env:{VITE_API_URL:'/api',BACKEND_PORT:process.env.NATIVE_BACKEND_PORT,
      VITE_SUPABASE_URL:process.env.NATIVE_DATA_API_URL,VITE_SUPABASE_ANON_KEY:'native-public-test-key'}},
});
