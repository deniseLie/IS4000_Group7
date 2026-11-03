import test from 'node:test';
import assert from 'node:assert/strict';
import {isTeam,scoreLabel,seconds,summary} from './quality.js';

test('shows the Quality page only for administrator-set team roles',()=>{
  assert.equal(isTeam({user:{app_metadata:{demobank_role:'rm'}}}),true);
  assert.equal(isTeam({user:{app_metadata:{demobank_role:'support'}}}),true);
  assert.equal(isTeam({user:{app_metadata:{},user_metadata:{demobank_role:'rm'}}}),false);
  assert.equal(isTeam(null),false);
});

test('counts cases and labels scores',()=>{
  assert.deepEqual(summary([{status:'pass'},{status:'fail'},{status:'fail'},{status:'skipped'}]),{pass:1,fail:2,skipped:1,error:0});
  assert.deepEqual([1,0,0.5,null].map(scoreLabel),['pass','fail','0.5','n/a']);
  assert.equal(seconds(31.46),'31.5 s');assert.equal(seconds(null),'—');
});
