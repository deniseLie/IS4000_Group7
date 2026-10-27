import test from 'node:test';
import assert from 'node:assert/strict';
import {displayName,firstName,formatDate,formatDateTime,formatMoney,formatTime} from './format.js';

test('instants show in Singapore time with a 12-hour clock',()=>{
  assert.equal(formatDateTime('2026-10-27T01:30:00Z'),'27 Oct 2026, 9:30 am');
  assert.equal(formatDateTime('2026-10-27T16:05:00Z'),'28 Oct 2026, 12:05 am');
  assert.equal(formatTime('2026-10-27T06:00:00Z'),'2:00 pm');
});
test('date-only values stay on their calendar day',()=>{
  assert.equal(formatDate('2026-11-05'),'5 Nov 2026');
  assert.equal(formatDate('2026-01-01',{weekday:'short',year:undefined}),'Thu, 1 Jan');
});
test('money has a currency and two decimals',()=>{
  assert.equal(formatMoney(5230.45),'SGD 5,230.45');
  assert.equal(formatMoney(64.2,'USD'),'USD 64.20');
});
test('names drop only the trailing demo marker',()=>{
  assert.equal(displayName('Priya Nair (DEMO)'),'Priya Nair');
  assert.equal(displayName('DEMO Bank (DEMO) Team'),'DEMO Bank (DEMO) Team');
  assert.equal(firstName('Marcus Chua (DEMO)'),'Marcus');
  assert.equal(displayName(null),'');
});
