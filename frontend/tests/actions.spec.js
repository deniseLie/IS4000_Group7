import {openMenu,openMenuItem} from './menu.js';
import {test,expect} from '@playwright/test';
import {fixture,seedSession} from './nativeSession';

test('recorded action review confirms, rejects, expires and preserves real receipts',async({page})=>{
  await seedSession(page);
  await page.goto('/');
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    await openMenuItem(page,'open-actions');
    const dialog=page.locator('dialog[aria-label="Actions"]');
    await expect(dialog.locator('[data-command-id]')).toHaveCount(5);
    const handoff=page.locator(`[data-command-id="${fixture.handoff_command_id}"]`);
    await expect(handoff.getByTestId('command-state')).toHaveText('succeeded');
    await expect(handoff.getByTestId('command-receipt')).toBeVisible();
    await expect(handoff.getByTestId('review-command')).toHaveCount(0);
    expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/action-review-${width}.png`,fullPage:true});
    await page.getByRole('button',{name:'Close actions',exact:true}).click();
    await expect(page.getByTestId('open-account-menu')).toBeFocused();
  }
  await openMenuItem(page,'open-actions');
  const rejected=page.locator(`[data-command-id="${fixture.reject_command_id}"]`);
  await rejected.getByTestId('review-command').click();
  await expect(rejected.getByTestId('confirm-command')).toBeDisabled();
  await rejected.getByTestId('reject-command').click();
  await expect(rejected.getByTestId('command-state')).toHaveText('rejected');
  await expect(rejected.getByTestId('command-receipt')).toHaveCount(0);
  const expired=page.locator(`[data-command-id="${fixture.expired_command_id}"]`);
  await expired.getByTestId('review-command').click();
  await expired.getByTestId('acknowledge-command').check();
  await expired.getByTestId('confirm-command').click();
  await expect(page.getByTestId('actions-error')).toBeVisible();
  await expect(expired.getByTestId('confirm-command')).toBeDisabled();
  await page.getByTestId('reload-actions').click();
  await expect(expired.getByTestId('command-state')).toHaveText('expired');
  const approved=page.locator(`[data-command-id="${fixture.confirm_command_id}"]`);
  await approved.getByTestId('review-command').click();
  await expect(approved.getByTestId('confirm-command')).toBeDisabled();
  await approved.getByTestId('acknowledge-command').check();
  const response=page.waitForResponse(reply=>reply.url().endsWith(`/commands/${fixture.confirm_command_id}/confirm`));
  await approved.getByTestId('confirm-command').click();
  const saved=await (await response).json();
  expect(saved.receipt.outcome).toBe('succeeded');
  await expect(approved.getByTestId('command-state')).toHaveText('succeeded');
  await expect(approved.getByTestId('command-receipt')).toContainText(saved.receipt.receipt_id);
  await page.reload();
  await openMenuItem(page,'open-actions');
  await expect(approved.getByTestId('command-receipt')).toContainText(saved.receipt.receipt_id);
  const changed=page.locator(`[data-command-id="${fixture.failed_command_id}"]`);
  await changed.getByTestId('review-command').click();
  await changed.getByTestId('acknowledge-command').check();
  await changed.getByTestId('confirm-command').click();
  await expect(changed.getByTestId('command-state')).toHaveText('failed');
  await expect(changed.getByTestId('command-receipt')).toBeVisible();
});

test('recorded action empty scope and guest access stay separate',async({page,browser})=>{
  await seedSession(page);
  await page.goto('/');
  await openMenuItem(page,'open-actions');
  await expect(page.getByTestId('actions-empty')).toBeVisible();
  const guest=await browser.newContext();
  try {
    const publicPage=await guest.newPage();
    await publicPage.goto('/');
    await expect(publicPage.getByTestId('open-actions')).toHaveCount(0);
  } finally {await guest.close();}
});
