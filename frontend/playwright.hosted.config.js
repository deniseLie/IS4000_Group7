import {defineConfig} from '@playwright/test';

// The normal application must already be running against the approved hosted project.
export default defineConfig({
 testDir:'./tests',testMatch:['hosted.spec.js','urgentHelp.spec.js'],timeout:180000,workers:1,
 reporter:[['list'],['html',{open:'never'}]],
 use:{baseURL:'http://127.0.0.1:5173',headless:true,screenshot:'only-on-failure',trace:'off'},
});
