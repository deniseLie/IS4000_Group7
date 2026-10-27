import test from 'node:test';
import assert from 'node:assert/strict';
import {cardChanges} from './cardChanges.js';

test('only allowed card changes are offered and cancelled is terminal',()=>{
  assert.deepEqual(cardChanges('temporarily_blocked'),['card.unblock','card.cancel_permanently']);
  assert.deepEqual(cardChanges('active'),['card.cancel_permanently']);
  for(const status of ['cancelled','blocked','expired'])assert.deepEqual(cardChanges(status),[]);
});
