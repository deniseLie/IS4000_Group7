import test from 'node:test';
import assert from 'node:assert/strict';
import {cacheOwner,cached,remember,setCacheOwner} from './cache.js';

test('guests have no cache',()=>{
  setCacheOwner(null);
  remember('actions',[1]);
  assert.equal(cached('actions'),null);
});
test('reads are kept for the signed-in user and cleared when the user changes',()=>{
  setCacheOwner('alex');
  remember('actions',[1]);
  assert.deepEqual(cached('actions'),[1]);
  setCacheOwner('alex');  // the same user again keeps the cache
  assert.deepEqual(cached('actions'),[1]);
  setCacheOwner('michelle');
  assert.equal(cached('actions'),null);
  setCacheOwner('alex');
  assert.equal(cached('actions'),null);
});
test('a read that finishes after the user changed is not kept',()=>{
  setCacheOwner('alex');
  const scope=cacheOwner();
  setCacheOwner('michelle');
  remember('inbox',{items:[]},scope);
  assert.equal(cached('inbox'),null);
  setCacheOwner('alex');
  assert.equal(cached('inbox'),null);
});
