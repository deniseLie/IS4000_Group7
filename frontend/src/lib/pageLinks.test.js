import test from 'node:test';
import assert from 'node:assert/strict';
import {pageLink} from './pageLinks.js';

test('personal questions point to the page they are about, typos included (CR2)', () => {
  assert.equal(pageLink('wats my crad bal').key,'cards');
  assert.equal(pageLink('show my recent transactions').key,'transactions');
  assert.equal(pageLink('whats my balance').key,'accounts');
  assert.equal(pageLink('what documents do i still need to upload').key,'compliance');
  assert.equal(pageLink('do i have any reminders').key,'inbox');
  assert.equal(pageLink('what is the status of TCK-001050').key,'requests');
  assert.equal(pageLink('how do i apply for a car loan').key,'loans');
  assert.equal(pageLink('hello'),null);
});


test('ambiguous spending power and fraud language do not navigate to Recent activity', () => {
  assert.equal(pageLink('I need more spending power for my overseas trip.'),null);
  assert.equal(pageLink('Someone used my card without permission.'),null);
  assert.equal(pageLink('There is a suspicious charge on my card.'),null);
  assert.equal(pageLink('I spent $50 yesterday').key,'transactions');
});
