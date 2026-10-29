import test from 'node:test';
import assert from 'node:assert/strict';
import {buildStatement,lastFullMonths,monthLabel,statementSources} from './statements.js';

const account={account_id:'acct_10021',type:'multi_currency',number:'****7734',balance:250000,currency:'SGD',status:'active'};
const card={card_id:'card_10021',name:'Signature Card',last4:'7310',outstanding:5169.6,limit:20000};
const row=(id,date,amount,extra={})=>({txn_id:`txn_${id}`,date,description:'x',amount,currency:'SGD',status:'posted',card_id:null,card_last4:null,...extra});
const onCard={card_id:'card_10021',card_last4:'7310'};
// Michelle's September and October activity (0063, 0075, 0077).
const txns=[
  row(100232,'2026-09-03',18500),row(100233,'2026-09-14',-6200),row(100234,'2026-09-15',-405.5),row(100211,'2026-09-22',120000),
  row(100212,'2026-10-03',18500),row(100213,'2026-10-14',-6200),row(100217,'2026-10-28',-12000,{status:'pending'}),
  row(100235,'2026-09-05',-201.15,onCard),row(100236,'2026-09-15',405.5,onCard),row(100237,'2026-09-19',-780,onCard),row(100238,'2026-09-26',-269.25,onCard),
  row(100214,'2026-10-27',-68.2,onCard),row(100215,'2026-10-27',-1,onCard),row(100216,'2026-10-27',-3850,onCard)];

test('statements cover the last three full months in Singapore time', () => {
  assert.deepEqual(lastFullMonths(new Date('2026-10-29T03:00:00Z')),['2026-09','2026-08','2026-07']);
  assert.deepEqual(lastFullMonths(new Date('2026-12-31T17:00:00Z')),['2026-12','2026-11','2026-10']);  // already 1 Jan in Singapore
  assert.equal(monthLabel('2026-09'),'September 2026');
});

test('an account statement works back from today and ignores pending payments', () => {
  const statement=buildStatement({kind:'account',source:account},txns,'2026-09');
  assert.equal(statement.closing,237700);
  assert.equal(statement.opening,105805.5);
  assert.deepEqual([statement.money_in,statement.money_out],[138500,6605.5]);
  assert.deepEqual(statement.lines.map(line=>line.balance),[124305.5,118105.5,117700,237700]);
  assert.equal(buildStatement({kind:'account',source:account},txns,'2026-10').pending.length,1);
});

test('a card statement shows what is owed, with purchases raising it', () => {
  const statement=buildStatement({kind:'card',source:card},txns,'2026-09');
  assert.equal(statement.closing,1250.4);
  assert.equal(statement.opening,405.5);
  assert.deepEqual(statement.lines.map(line=>line.balance),[606.65,201.15,981.15,1250.4]);
});

test('account rows are only attributed when one open account has that currency', () => {
  assert.deepEqual(statementSources([account],[card]).map(source=>source.key),['account:acct_10021','card:card_10021']);
  const second={...account,account_id:'acct_2',number:'****1111'};
  assert.deepEqual(statementSources([account,second],[]).map(source=>source.key),[]);
  assert.deepEqual(statementSources([account,{...second,status:'closed'}],[]).map(source=>source.key),['account:acct_10021']);
});
