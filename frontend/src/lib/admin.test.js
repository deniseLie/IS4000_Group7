import test from 'node:test';
import assert from 'node:assert/strict';
import {isAdmin,scoreLabel,seconds,summary} from './admin.js';
import {isRm} from './rm.js';

test('shows the admin dashboard only for the administrator-set admin role',()=>{
  assert.equal(isAdmin({user:{app_metadata:{demobank_role:'admin'}}}),true);
  for(const role of ['rm','support','customer'])assert.equal(isAdmin({user:{app_metadata:{demobank_role:role}}}),false);
  assert.equal(isAdmin({user:{app_metadata:{},user_metadata:{demobank_role:'admin'}}}),false);
  assert.equal(isAdmin(null),false);
  assert.equal(isRm({user:{app_metadata:{demobank_role:'admin'}}}),false);
});

test('counts cases and labels scores',()=>{
  assert.deepEqual(summary([{status:'pass'},{status:'fail'},{status:'fail'},{status:'skipped'}]),{pass:1,fail:2,skipped:1,error:0});
  assert.deepEqual([1,0,0.5,null].map(scoreLabel),['pass','fail','0.5','n/a']);
  assert.equal(seconds(31.46),'31.5 s');assert.equal(seconds(null),'—');
});
