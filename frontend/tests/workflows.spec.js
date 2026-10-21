import {test,expect} from '@playwright/test';
import {fixture,seedSession} from './nativeSession';

async function open(page) {
  await seedSession(page);
  await page.addInitScript(({user_id,session_id})=>localStorage.setItem(`demobank.active.${user_id}`,JSON.stringify({session_id,guest_token:null})),fixture);
  await page.goto('/');
  await expect(page.getByTestId('review-pending-workflow')).toBeVisible();
  await expect(page.locator('#chat-composer')).toBeDisabled();
}
for(const decision of ['confirmation','rejection'])test(`durable action workflow ${decision} resumes the saved reply`,async({page})=>{
  await open(page);
  await page.getByTestId('review-pending-workflow').click();
  const command=page.locator(`[data-command-id="${fixture.command_id}"]`);
  await command.getByTestId('review-command').click();
  if(decision==='confirmation') {
    await expect(command.getByTestId('confirm-command')).toBeDisabled();
    await command.getByTestId('acknowledge-command').check();
    await command.getByTestId('confirm-command').click();
  } else await command.getByTestId('reject-command').click();
  await expect(command.getByTestId('command-state')).toHaveText(decision==='confirmation'?'succeeded':'rejected');
  await expect(command.getByTestId('command-workflow')).toContainText('completed');
  await page.getByRole('button',{name:'Close actions',exact:true}).click();
  await expect(page.locator('#chat-composer')).toBeEnabled();
  const answer=page.getByTestId('chat-message-assistant');
  await expect(answer).toHaveAttribute('data-response-origin','safety');
  await expect(answer).toContainText(decision==='confirmation'?'temporarily blocked the synthetic card':'You rejected the temporary block');
  await page.reload();
  await expect(answer).toHaveCount(1);
  await expect(page.getByTestId('review-pending-workflow')).toHaveCount(0);
  for(const width of [1280,390,320]) {
    await page.setViewportSize({width,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/workflow-${decision}-${width}.png`,fullPage:true});
  }
});
test('durable action workflow cancellation rejects the pending proposal',async({page})=>{
  await open(page);
  await page.getByTestId('cancel-pending-reply').click();
  await expect(page.locator('#chat-composer')).toBeEnabled();
  await page.getByTestId('open-actions').click();
  const command=page.locator(`[data-command-id="${fixture.command_id}"]`);
  await expect(command.getByTestId('command-state')).toHaveText('rejected');
  await expect(command.getByTestId('command-receipt')).toHaveCount(0);
  await expect(command.getByTestId('command-workflow')).toContainText('cancelled');
});

test('real fraud proposal uses reviewed confirmation and persists the AI answer with receipts',async({page})=>{
  test.skip(fixture.fraud_live!==true,'Requires the real configured Fraud model');
  test.setTimeout(110000);
  await seedSession(page);await page.goto('/');
  await page.locator('#chat-composer').fill('This txn_100111 is an unfamiliar charge on my card ending 4821. Please propose a temporary block for review.');
  const received=page.waitForResponse(response=>response.url().endsWith('/chat')&&response.request().method()==='POST');
  await page.getByTestId('send-message').click();
  const response=await received;expect(response.status()).toBe(200);
  // Chromium does not expose the completed SSE body reliably; inspect the actual UI outcome.
  const finalReply=page.locator('[data-testid="chat-message-assistant"][data-response-origin], [data-testid="chat-message-assistant"] .bank-chat-bubble.error');
  await expect(page.getByTestId('commands-list').or(finalReply)).toBeVisible({timeout:75000});
  await expect(page.getByTestId('commands-list')).toBeVisible();
  await expect(page.getByTestId('chat-message-assistant')).toHaveCount(0);
  const command=page.locator('[data-command-id]');
  await expect(command).toHaveCount(1);
  await expect(command.getByTestId('command-state')).toHaveText('proposed');
  await page.getByRole('button',{name:'Close actions',exact:true}).click();
  await expect(page.locator('#chat-composer')).toBeDisabled();
  await page.getByTestId('open-records').click();
  await expect(page.getByTestId('records-card').locator('dd').first()).toHaveText('active');
  await page.getByRole('button',{name:'Close records',exact:true}).click();
  await page.getByTestId('review-pending-workflow').click();
  await command.getByTestId('review-command').click();
  await expect(command.getByTestId('confirm-command')).toBeDisabled();
  await command.getByTestId('acknowledge-command').check();
  await command.getByTestId('confirm-command').click();
  await expect(page.getByTestId('command-state')).toHaveCount(2);
  await expect(page.getByTestId('command-state')).toHaveText(['succeeded','succeeded']);
  await expect(page.getByTestId('command-receipt')).toHaveCount(2);
  await page.getByRole('button',{name:'Close actions',exact:true}).click();
  const section=page.getByTestId('answer-sections').locator('section[data-domain="fraud"][data-response-origin="ai"]');
  await expect(section).toBeVisible();
  await section.getByTestId('answer-sources').locator('summary').click();
  await expect(section.locator('[data-source-id="customer-records"]')).toBeVisible();
  const answer=await section.locator('.answer-text').innerText();
  await expect(page.getByTestId('chat-message-assistant')).toContainText('temporarily blocked the synthetic card ending 4821');
  const reloaded=page.waitForResponse(response=>/\/conversations\/[^/]+$/.test(new URL(response.url()).pathname)&&response.request().method()==='GET');
  await page.getByTestId('reload-saved-chat').click();
  expect((await reloaded).status()).toBe(200);
  await expect(page.getByTestId('reload-saved-chat')).toBeEnabled();
  await expect(section.locator('.answer-text')).toHaveText(answer);
  await page.getByTestId('open-records').click();
  await expect(page.getByTestId('records-card')).toContainText('temporarily blocked');
  await page.getByRole('button',{name:'Close records',exact:true}).click();
  for(const width of [1280,390,320]) {
    await page.setViewportSize({width,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/fraud-live-${width}.png`,fullPage:true});
  }
});
