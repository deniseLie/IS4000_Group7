import config from './playwright.config.js';

export default {...config,testMatch:['foundation.spec.js'],webServer:config.webServer.map((server,index)=>
  index===0?{...server,env:{...server.env,PIPELINE:'v2',KNOWLEDGE_STORE:'memory',CONVERSATION_STORE:'off',RUN_WORKER_IN_API:'false'}}:server)};
