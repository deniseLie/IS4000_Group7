import {openMenu,openMenuItem} from './menu.js';
import {test,expect} from '@playwright/test';
import fs from 'node:fs';

// Workflow 1 (docs/workflows/workflow-1-alex.md) through the normal UI, personal v2 and the real provider.
// Native: backend/tests/test_workflow1_browser.py (signed local session replaces the password step; confirm and decline).
// Hosted: playwright.hosted.config.js with TEST_PERSONA_FIXTURE_FILE and TEST_HOSTED_AI=1 (real login; decline only, the card is never blocked).
// Assertions are structural and record-based, never exact model wording. Provider failures fail the run.
const native=!!process.env.NATIVE_BROWSER_FIXTURE;
const hosted=!native&&!!process.env.TEST_PERSONA_FIXTURE_FILE&&process.env.TEST_HOSTED_AI==='1';
const MESSAGE='theres a $2400 charge i dont recognise, my transfer to my landlord still says pending, and i got locked out of the app on my phone';
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

for(const decision of ['confirm','decline'])test(`workflow one ${decision}: guest, three problems, follow-ups, handover and logout`,async({page})=>{
  test.skip(!native&&!hosted,'Run through test_workflow1_browser.py or the hosted config with TEST_HOSTED_AI=1');
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

  // Part B: one message, three problems, urgent first; all sections show before the decision.
  const reply=await ask(page,MESSAGE);
  const sections=reply.locator('section.answer-section');
  await expect(sections).toHaveCount(3);
  expect(await sections.evaluateAll(nodes=>nodes.map(node=>[node.dataset.domain,node.dataset.priority]))).toEqual([['fraud','P0'],['retail','P3'],['retail','P3']]);
  const urgent=sections.nth(0),transfer=sections.nth(1),app=sections.nth(2);
  await expect(urgent.locator('h3')).toContainText('Urgent');
  await expect(urgent.getByTestId('block-card')).toBeVisible();
  await expect(urgent).not.toContainText(/\b(?:I|we) (?:have|'ve) (?:reversed|refunded|investigated|disputed)\b/i);
  await expect(transfer).toContainText(/1,?800(?:\.00)?/);
  await expect(transfer).toContainText(/pending/i);
  await expect(app.locator('[data-source-id*="app-login-recovery"]')).toHaveCount(1);
  await expect(app.locator('.answer-text')).not.toContainText(/https?:\/\//);
  await expect(page.locator('#chat-composer')).toBeDisabled();

  await urgent.getByTestId('block-card').click();
  await expect(page.getByTestId('card-block-dialog')).toContainText('ending 4821');
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('card-block-dialog')).toHaveCount(0);
  await urgent.getByTestId('block-card').click();
  await page.getByTestId(decision==='confirm'?'confirm-card-block':'decline-card-block').click();
  const outcome=page.getByTestId('card-block-outcome');
  if(decision==='confirm') {
    await expect(page.getByTestId('card-block-receipt')).toBeVisible({timeout:60000});
    await expect(outcome).toContainText('Your card ending 4821 is temporarily blocked.');
  } else await expect(outcome).toContainText('it stays active',{timeout:60000});
  await page.getByRole('button',{name:'Back to chat'}).click();
  await expect(page.locator('#chat-composer')).toBeEnabled({timeout:60000});
  const settled=page.getByTestId('chat-message-assistant').last();
  await expect(settled.getByTestId('answer-decision')).toContainText(decision==='confirm'?'Your card ending 4821 is temporarily blocked.':'official fraud line');
  if(decision==='confirm')await expect(settled.getByTestId('answer-decision')).toContainText('replacement card');
  await expect(settled.locator('section.answer-section')).toHaveCount(3);
  await expect(settled.getByTestId('block-card')).toHaveCount(0);

  // Part C: follow-ups from Alex's records and approved articles.
  const card=await ask(page,'whats my credit card due date and min payment');
  await expect(card.locator('section.answer-section')).toHaveCount(1);
  await expect(card).toContainText(/2026-11-05|5 Nov(?:ember)? 2026|Nov(?:ember)? 5,? 2026/);
  await expect(card).toContainText(/\b50(?:\.00)?\b/);
  await expect(card).toContainText(/2,?464\.20/);
  await expect(card).toContainText(/8,?000(?:\.00)?/);
  const loan=await ask(page,'can i get a car loan');
  await expect(loan.locator('[data-source-id*="car-loans"]')).toHaveCount(1);
  await expect(loan).toContainText(/final decision|bank (?:makes|will make) the (?:final )?decision|bank decides/i);
  await expect(loan.locator('.answer-disclaimer')).toBeVisible();
  await expect(loan).not.toContainText(/\byou (?:are|'re) (?:approved|eligible|pre-approved)\b/i);

  // Talk to a person: cancel records nothing; approve records a handoff with a receipt.
  const proposals=[];
  page.on('request',request=>{if(request.url().endsWith('/commands')&&request.method()==='POST')proposals.push(request.url());});
  await page.getByRole('button',{name:'Talk to a person'}).click();
  const summary=page.getByTestId('handoff-summary');
  await expect(summary).toContainText('Customer asked for a person.');
  await page.getByTestId('cancel-handoff').click();
  expect(proposals).toEqual([]);
  await page.getByRole('button',{name:'Talk to a person'}).click();
  for(const word of ['4821','2400','landlord','recognise','locked'])await expect(summary).not.toContainText(word);
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
