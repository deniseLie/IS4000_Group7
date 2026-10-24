import {openMenu,openMenuItem} from './menu.js';
import {test,expect} from '@playwright/test';
import {seedSession} from './nativeSession.js';

test.beforeEach(async({page})=>seedSession(page));
async function openRequests(page) {
  await openMenuItem(page,'open-data-sharing');await page.getByTestId('open-relationship-details').click();
  await expect(page.getByTestId('request-intake')).toBeVisible();
}
async function proposal(page,details) {
  await page.getByTestId('request-details').fill(details);
  await page.getByTestId('request-country').fill('SG');await page.getByTestId('request-currency').fill('USD');
  const proposed=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
  await page.getByTestId('prepare-request').click();const response=await proposed;expect(response.status()).toBe(200);
  const saved=await response.json();expect(saved.receipt).toBeNull();
  await page.getByTestId('review-request-actions').click();
  const command=page.locator(`[data-command-id="${saved.command.command_id}"]`);
  await command.getByTestId('review-command').click();await expect(command.getByTestId('confirm-command')).toBeDisabled();
  return {saved,command};
}

test('request intake reviews confirms and reloads the exact stored request',async({page})=>{
  await page.goto('/');
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});await openRequests(page);
    const {saved,command}=await proposal(page,`Synthetic relationship planning at width ${width}`);
    await expect(command.getByTestId('command-request')).toContainText(saved.command.target.what);
    await command.getByTestId('acknowledge-command').check();
    const confirmed=page.waitForResponse(response=>response.url().endsWith(`/commands/${saved.command.command_id}/confirm`));
    await command.getByTestId('confirm-command').click();const response=await confirmed;expect(response.status()).toBe(200);
    const result=await response.json();expect(result.receipt.result.status).toBe('sent');
    await expect(command.getByTestId('command-receipt')).toContainText(result.receipt.receipt_id);
    await page.keyboard.press('Escape');await openRequests(page);
    const request=page.locator(`[data-request-id="${result.receipt.result.request_id}"]`);
    await expect(request).toContainText(saved.command.target.what);await expect(request).toContainText('Currency reference: USD');
    await request.scrollIntoViewIfNeeded();
    const dialog=page.locator('dialog[aria-label="Data sharing"]');expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/request-intake-${width}.png`,fullPage:true});
    await page.keyboard.press('Escape');
  }
});

test('request rejection stores no intake record',async({page})=>{
  await page.goto('/');await openRequests(page);const {saved,command}=await proposal(page,'Synthetic request to reject');
  const rejected=page.waitForResponse(response=>response.url().endsWith(`/commands/${saved.command.command_id}/reject`));
  await command.getByTestId('reject-command').click();expect((await rejected).status()).toBe(200);
  await expect(command.getByTestId('command-state')).toHaveText('rejected');
});

test('unassigned customers cannot prepare relationship intake',async({page})=>{
  await page.goto('/');await openRequests(page);await expect(page.getByTestId('request-details')).toBeDisabled();
  await expect(page.getByTestId('prepare-request')).toBeDisabled();
});
