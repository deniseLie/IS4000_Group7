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
  await expect(answer).toContainText(decision==='confirmation'?'Your card ending 4821 is temporarily blocked.':'You chose not to block the card ending 4821; it stays active.');
  await page.reload();
  await expect(answer).toHaveCount(1);
  await expect(page.getByTestId('review-pending-workflow')).toHaveCount(0);
  for(const width of [1280,390,320]) {
    await page.setViewportSize({width,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/workflow-${decision}-${width}.png`,fullPage:true});
  }
});
test('durable action workflow dialog confirmation updates the urgent section in place',async({page})=>{
  await open(page);
  const urgent=page.locator('section.answer-section[data-priority="P0"]');
  await expect(urgent.getByTestId('block-card')).toBeVisible();
  await urgent.getByTestId('block-card').click();
  const dialog=page.getByTestId('card-block-dialog');
  await expect(dialog).toContainText('ending 4821');
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(urgent.getByTestId('block-card')).toBeVisible();
  await urgent.getByTestId('block-card').click();
  await page.getByTestId('confirm-card-block').click();
  await expect(page.getByTestId('card-block-receipt')).toBeVisible();
  await expect(page.getByTestId('card-block-outcome')).toContainText('Your card ending 4821 is temporarily blocked.');
  await page.getByRole('button',{name:'Back to chat'}).click();
  await expect(page.locator('#chat-composer')).toBeEnabled();
  await expect(page.getByTestId('answer-decision')).toContainText('Your card ending 4821 is temporarily blocked.');
  await expect(page.getByTestId('block-card')).toHaveCount(0);
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
