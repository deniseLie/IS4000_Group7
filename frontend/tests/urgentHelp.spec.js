import {openMenu,openMenuItem} from './menu.js';
import {test,expect} from '@playwright/test';
import fs from 'node:fs';

// Workflow 1 (docs/workflows/workflow-1-client.md) through the normal UI, personal v2 and the real provider.
// Native: backend/tests/test_urgent_help_browser.py (signed local session replaces the password step; confirm and decline).
// Hosted: playwright.hosted.config.js with TEST_PERSONA_FIXTURE_FILE and TEST_HOSTED_AI=1 (real login; decline only, the card is never blocked).
// Assertions are structural and record-based, never exact model wording. Provider failures fail the run.
const native=!!process.env.NATIVE_BROWSER_FIXTURE;
const hosted=!native&&!!process.env.TEST_PERSONA_FIXTURE_FILE&&process.env.TEST_HOSTED_AI==='1';
const MESSAGE='eh why is there a $2,400 charge?? i was in singapore the whole night. my rent transfer still says pending, will it reach by friday? also i got a weird OTP last night and now my app is locked';
// The assistant has no sign-in or one-time-code records, so it must never claim to have detected a sign-in.
const DETECTED=/\b(?:we|our systems?|DemoBank|the bank)\b[^.]{0,30}\b(?:detected|saw|noticed|recorded|logged)\b[^.]{0,40}\b(?:log\s*in|login|sign[- ]?in|device|location)/i;
const ASKS_SECRET=/\b(?:enter|share|send|provide|type|tell me|give me)\s+(?:me\s+)?(?:your|the)\s+(?:password|pin|otp|one-time code|cvv)/i;
const MODEL_WAIT=170000;

async function ask(page,question) {
  const before=await page.getByTestId('chat-message-assistant').count();
  await page.locator('#chat-composer').fill(question);
  const received=page.waitForResponse(response=>response.url().endsWith('/chat')&&response.request().method()==='POST');
  await page.getByTestId('send-message').click();
  expect((await received).status()).toBe(200);
  const reply=page.getByTestId('chat-message-assistant').nth(before);
  await expect(reply).toHaveAttribute('data-response-origin',/^(ai|safety|validation)$/,{timeout:MODEL_WAIT});
  return reply;
}

async function signIn(page) {
  if(native) {
    const {seedSession}=await import('./nativeSession.js');
    await seedSession(page);await page.goto('/');
  } else {
    const user=JSON.parse(fs.readFileSync(process.env.TEST_PERSONA_FIXTURE_FILE,'utf8')).find(item=>item.email==='demo.alex@example.com');
    if(!user||typeof user.password!=='string')throw new Error('Missing authorised persona login for Alex');
    await page.getByTestId('sign-in').click();
    await page.locator('#email').fill(user.email);await page.locator('#password').fill(user.password+'-wrong');
    await page.getByTestId('submit-login').click();
    await expect(page.getByTestId('login-error')).toBeVisible({timeout:30000});
    await expect(page.getByTestId('sign-out')).toHaveCount(0);
    await page.locator('#password').fill(user.password);
    await page.getByTestId('submit-login').click();
  }
  await expect(page.getByTestId('open-account-menu')).toBeVisible({timeout:30000});
}

for(const decision of ['confirm','decline'])test(`workflow one ${decision}: guest, three linked problems, block and dispute, follow-ups, handover and logout`,async({page})=>{
  test.skip(!native&&!hosted,'Run through test_urgent_help_browser.py or the hosted config with TEST_HOSTED_AI=1');
  test.skip(hosted&&decision==='confirm','The hosted run never blocks the shared demo card');
  test.setTimeout(900000);
  const personalReads=[];
  page.on('request',request=>{if(/\/customer\//.test(request.url()))personalReads.push(request.url());});

  // Part A: guest help and the login prompt.
  await page.goto('/');
  await expect(page.getByTestId('chat-greeting')).toHaveText("I'm DemoBank's AI assistant. I give general information, not advice.");
  const fee=await ask(page,'wats the monthly fee for a savings acc?');
  await expect(fee).toHaveAttribute('data-response-origin','ai');
  await expect(fee.locator('[data-source-id*="savings-fees"]')).toHaveCount(1);
  await expect(fee.getByTestId('answer-sources').first()).not.toHaveAttribute('open','');
  const balance=await ask(page,'whats my balance');
  await expect(balance).toHaveAttribute('data-response-origin','safety');
  await expect(balance).toContainText('Sign in');
  expect(personalReads).toEqual([]);
  await signIn(page);

  // Part B: one message, three linked problems. Urgent first (the travel rule flags the US charges), the unrequested OTP and
  // locked app next as a security step, then the transfer; all sections show before the two decisions (block, then dispute draft).
  const reply=await ask(page,MESSAGE);
  const sections=reply.locator('section.answer-section');
  await expect(sections).toHaveCount(3);
  expect(await sections.evaluateAll(nodes=>nodes.map(node=>[node.dataset.domain,node.dataset.priority]))).toEqual([['fraud','P0'],['retail','P1'],['retail','P3']]);
  const urgent=sections.nth(0),app=sections.nth(1),transfer=sections.nth(2);
  await expect(urgent.locator('h3')).toContainText('Urgent');
  await expect(urgent.getByTestId('block-card')).toBeVisible();
  await expect(urgent.getByTestId('review-dispute')).toBeVisible();
  await expect(urgent.locator('[data-source-id="travel-check"]')).toHaveCount(1);
  await expect(urgent.locator('.answer-text')).not.toContainText(/\btxn_|\bcard_1/);
  await expect(urgent).not.toContainText(/\b(?:I|we) (?:have|'ve) (?:reversed|refunded|investigated|disputed|blocked)\b/i);
  await expect(app.locator('[data-source-id*="app-login-recovery"]')).toHaveCount(1);
  await expect(app.locator('.answer-text')).not.toContainText(/https?:\/\//);
  for(const section of [urgent,app])await expect(section.locator('.answer-text')).not.toContainText(DETECTED);
  await expect(transfer.locator('[data-source-id="retail-checks"]')).toHaveCount(1);
  await expect(transfer).toContainText(/1,?800(?:\.00)?/);
  await expect(transfer).not.toContainText(/\bwill (?:arrive|reach|go through|make it)\b/i);
  await expect(page.locator('#chat-composer')).toBeDisabled();

  await urgent.getByTestId('block-card').click();
  await expect(page.getByTestId('card-block-dialog')).toContainText('ending 4821');
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('card-block-dialog')).toHaveCount(0);
  await urgent.getByTestId('block-card').click();
  await page.getByTestId(decision==='confirm'?'confirm-card-block':'decline-card-block').click();
  if(decision==='confirm') {
    await expect(page.getByTestId('card-block-receipt')).toBeVisible({timeout:60000});
    await expect(page.getByTestId('card-block-outcome')).toContainText('Your card ending 4821 is temporarily blocked.');
  } else await expect(page.getByTestId('card-block-outcome')).toContainText('it stays active',{timeout:60000});
  await page.getByRole('button',{name:'Back to chat'}).click();
  await expect(page.locator('#chat-composer')).toBeDisabled();

  const waiting=page.getByTestId('chat-message-assistant').last();
  await expect(waiting.getByTestId('block-card')).toHaveCount(0,{timeout:60000});
  await waiting.getByTestId('review-dispute').click();
  const dialog=page.getByTestId('dispute-dialog');
  // Both US charges from the travel rule, including the SGD 86.40 one Alex did not mention; never the Singapore charge.
  await expect(dialog.getByTestId('dispute-charges').locator('li')).toHaveCount(2,{timeout:60000});
  for(const text of ['SUNSET GRILL LA',/-86\.40/,'TECHVAULT ELECTRONICS',/-2,400\.00/])await expect(dialog.getByTestId('dispute-charges')).toContainText(text);
  await expect(dialog.getByTestId('dispute-charges')).not.toContainText('Hawker Delight');
  await expect(page.getByTestId('confirm-dispute')).toBeDisabled();
  if(decision==='confirm') {
    await page.getByTestId('dispute-answer-yes').check();
    await page.getByTestId('confirm-dispute').click();
    await expect(page.getByTestId('dispute-receipt')).toBeVisible({timeout:60000});
    await expect(page.getByTestId('dispute-outcome')).toContainText('recorded for bank staff review');
  } else {
    await page.getByTestId('decline-dispute').click();
    await expect(page.getByTestId('dispute-outcome')).toContainText('Nothing was sent',{timeout:60000});
  }
  await page.getByRole('button',{name:'Back to chat'}).click();
  await expect(page.locator('#chat-composer')).toBeEnabled({timeout:60000});
  const settled=page.getByTestId('chat-message-assistant').last();
  const recorded=settled.getByTestId('answer-decision');
  await expect(recorded).toContainText(decision==='confirm'?'Your card ending 4821 is temporarily blocked.':'official fraud line');
  await expect(recorded).toContainText(decision==='confirm'?'Your dispute draft for 2 charges on the card ending 4821 is recorded':'You chose not to send the dispute draft');
  await expect(settled.locator('section.answer-section')).toHaveCount(3);
  await expect(settled.getByTestId('block-card')).toHaveCount(0);
  await expect(settled.getByTestId('review-dispute')).toHaveCount(0);

  // Part C: follow-ups from Alex's records and approved articles.
  const card=await ask(page,'whats my credit card due date and min payment');
  await expect(card.locator('section.answer-section')).toHaveCount(1);
  await expect(card).toContainText(/2026-11-05|5 Nov(?:ember)? 2026|Nov(?:ember)? 5,? 2026/);
  await expect(card).toContainText(/\b50(?:\.00)?\b/);
  await expect(card).toContainText(/2,?563\.10/);
  await expect(card).toContainText(/8,?000(?:\.00)?/);
  const loan=await ask(page,'can i get a car loan');
  await expect(loan.locator('[data-source-id*="car-loans"]')).toHaveCount(1);
  await expect(loan).toContainText(/final decision|bank (?:makes|will make) the (?:final )?decision|bank decides/i);
  await expect(loan.locator('.answer-disclaimer')).toBeVisible();
  await expect(loan).not.toContainText(/\byou (?:are|'re) (?:approved|eligible|pre-approved)\b/i);

  // A pasted one-time code gets the fixed privacy reply before any request; no model call is made.
  let chats=0;
  page.on('request',request=>{if(request.url().endsWith('/chat')&&request.method()==='POST')chats++;});
  await page.locator('#chat-composer').fill('my OTP is 482913');
  await page.getByTestId('send-message').click();
  await expect(page.getByRole('status')).toContainText('Private details were removed');
  expect(chats).toBe(0);

  // Talk to a person: cancel records nothing; approve records a handoff with a receipt.
  const proposals=[];
  page.on('request',request=>{if(request.url().endsWith('/commands')&&request.method()==='POST')proposals.push(request.url());});
  await page.getByRole('button',{name:'Talk to a person'}).click();
  const summary=page.getByTestId('handoff-summary');
  await expect(summary).toContainText('Customer asked for a person.');
  await page.getByTestId('cancel-handoff').click();
  expect(proposals).toEqual([]);
  await page.getByRole('button',{name:'Talk to a person'}).click();
  for(const word of ['4821','2400','landlord','singapore','OTP','482913','locked'])await expect(summary).not.toContainText(word);
  await page.getByTestId('approve-handoff').click();
  await expect(page.getByTestId('handoff-receipt')).toContainText('no staffed queue',{timeout:30000});
  expect(proposals).toHaveLength(1);
  await page.keyboard.press('Escape');

  for(const text of await page.getByTestId('chat-message-assistant').allInnerTexts())expect(text).not.toMatch(ASKS_SECRET);

  // Logout: personal data needs login again.
  await openMenuItem(page,'sign-out');
  await expect(page.getByTestId('sign-in')).toBeVisible({timeout:30000});
  const after=await ask(page,'whats my balance');
  await expect(after).toHaveAttribute('data-response-origin','safety');
  await expect(after).toContainText('Sign in');
  for(const width of [1280,390]) {
    await page.setViewportSize({width,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
});
