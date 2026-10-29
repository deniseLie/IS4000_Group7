import {openMenuItem} from './menu.js';
import {test,expect} from '@playwright/test';
import {seedSession} from './nativeSession';

// Customer card self-service through the normal UI (Records -> Actions); no model is involved.
// Native: backend/tests/test_card_changes_browser.py starts with Alex's card temporarily blocked.
async function records(page) {
  await openMenuItem(page,'open-records');
  const card=page.getByTestId('records-card').filter({hasText:'4821'});
  await expect(card).toBeVisible();
  return card;
}
// The newest proposal with this title, pinned by its id so a re-sorted list cannot swap it.
async function proposal(page,title) {
  const pending=page.locator('[data-command-id]').filter({hasText:title}).filter({has:page.getByTestId('review-command')}).first();
  await expect(pending).toBeVisible();
  return page.locator(`[data-command-id="${await pending.getAttribute('data-command-id')}"]`);
}
async function decide(page,title,confirm) {
  const command=await proposal(page,title);
  await expect(command.getByTestId('command-state')).toHaveText('proposed');
  await command.getByTestId('review-command').click();
  await expect(command.getByTestId('confirm-command')).toBeDisabled();
  if(confirm) {
    await command.getByTestId('acknowledge-command').check();
    await command.getByTestId('confirm-command').click();
  } else await command.getByTestId('reject-command').click();
  await expect(command.getByTestId('command-state')).toHaveText(confirm?'succeeded':'rejected');
  return command;
}

test('card self-service unblock, then a declined and a confirmed permanent cancellation',async({page})=>{
  test.setTimeout(180000);
  await seedSession(page);await page.goto('/');
  let card=await records(page);
  await expect(card).toContainText('temporarily blocked');
  await expect(card.getByTestId('unblock-card')).toBeVisible();
  await card.getByTestId('unblock-card').click();
  const unblock=await decide(page,'Unblock card ending 4821',true);
  await expect(unblock.getByTestId('command-receipt')).toHaveCount(1);
  await page.getByTestId('open-chat').first().click();

  card=await records(page);
  await expect(card).toContainText('active');
  await expect(card.getByTestId('unblock-card')).toHaveCount(0);
  await card.getByTestId('cancel-card').click();
  await expect(card.getByTestId('cancel-card-warning')).toContainText('This cannot be undone.');
  await card.getByRole('button',{name:'Keep my card'}).click();
  await expect(card.getByTestId('cancel-card-warning')).toHaveCount(0);
  await card.getByTestId('cancel-card').click();
  await card.getByTestId('continue-cancel-card').click();
  const declined=await decide(page,'Cancel card ending 4821 permanently',false);
  await expect(declined).toContainText('No proposed change was executed');
  await page.getByTestId('open-chat').first().click();

  card=await records(page);
  await expect(card).toContainText('active');
  await card.getByTestId('cancel-card').click();
  await card.getByTestId('continue-cancel-card').click();
  const command=await proposal(page,'Cancel card ending 4821 permanently');
  await command.getByTestId('review-command').click();
  await expect(command.getByTestId('command-review')).toContainText('This cannot be undone');
  await command.getByTestId('acknowledge-command').check();
  await command.getByTestId('confirm-command').click();
  await expect(command.getByTestId('command-state')).toHaveText('succeeded');
  await page.getByTestId('open-chat').first().click();

  card=await records(page);
  await expect(card).toContainText('cancelled');
  await expect(card.getByTestId('manage-card')).toHaveCount(0);
  for(const width of [1280,390]) {
    await page.setViewportSize({width,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/card-changes-${width}.png`,fullPage:true});
  }
});
