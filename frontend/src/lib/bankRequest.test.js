import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer as createHttpServer} from 'node:http';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

test('bank questions render open, responded and closed states without inventing acceptance',async()=>{
 const server=await createServer({server:{middlewareMode:true,hmr:{server:createHttpServer()}},appType:'custom'});
 try {
  const {default:BankRequest}=await server.ssrLoadModule('/src/components/BankRequest.jsx');
  const request={request_id:'00000000-0000-4000-8000-100200000001',question:'Please explain this transfer.',created_at:'2026-11-01T00:00:00Z',due_date:null};
  const render=change=>renderToStaticMarkup(createElement(BankRequest,{review:{status:'information_requested'},request:{...request,...change},disabled:false}));
  assert.match(render({status:'open'}),/data-testid="bank-request-answer"/);
  const responded=render({status:'responded',responded_at:'2026-11-02T00:00:00Z',response:'Synthetic explanation'});
  assert.match(responded,/waiting for review/);
  assert.doesNotMatch(responded,/bank-request-answer|Accepted by bank/);
  assert.match(render({status:'closed',outcome:'accepted'}),/Accepted by bank/);
  for(const outcome of [null,'more_info'])assert.doesNotMatch(render({status:'closed',outcome}),/Accepted by bank/);
 } finally {await server.close();}
});
