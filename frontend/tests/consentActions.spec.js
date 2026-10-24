import {openMenu,openMenuItem} from './menu.js';
import {test,expect} from '@playwright/test';
import {seedSession} from './nativeSession';

async function openSharing(page) {
  const loaded=page.waitForResponse(response=>response.url().endsWith('/consent/records'));
  await openMenuItem(page,'open-data-sharing');
  expect((await loaded).status()).toBe(200);
  await expect(page.getByTestId('refresh-consent-records')).toBeEnabled();
}
async function reviewConsent(page,commandId) {
  await page.getByTestId('review-consent-actions').click();
  const command=page.locator(`[data-command-id="${commandId}"]`);
  await expect(command.getByTestId('command-state')).toHaveText('proposed');
  await expect(command.getByTestId('command-consent-scope')).toBeVisible();
  await command.getByTestId('review-command').click();
  await expect(command.getByTestId('confirm-command')).toBeDisabled();
  await command.getByTestId('acknowledge-command').check();
  const completed=page.waitForResponse(response=>response.url().endsWith(`/commands/${commandId}/confirm`));
  await command.getByTestId('confirm-command').click();
  const response=await completed;
  expect(response.status()).toBe(200);
  const result=await response.json();
  expect(result.receipt.outcome).toBe('succeeded');
  await expect(command.getByTestId('command-state')).toHaveText('succeeded');
  await expect(command.getByTestId('command-receipt')).toContainText(result.receipt.receipt_id);
  return result;
}

test('consent form requests grants and revokes through real reviewed receipts',async({page})=>{
  await seedSession(page);
  const chatCalls=[];
  page.on('request',request=>{if(request.url().endsWith('/chat'))chatCalls.push(request.url());});
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    await page.goto('/');await openSharing(page);
    await expect(page.getByTestId('sharing-status')).toHaveText('There is no active consent for this profile sharing.');
    await page.getByTestId('consent-category-profile').uncheck();
    await expect(page.getByTestId('request-consent')).toBeDisabled();
    await page.getByTestId('consent-category-profile').check();
    await page.getByTestId('consent-category-agenda').check();
    await page.getByTestId('consent-duration').selectOption('7');
    const requested=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
    await page.getByTestId('request-consent').click();
    const requestResponse=await requested;expect(requestResponse.status()).toBe(200);
    const request=await requestResponse.json();
    expect(request.receipt.result.status).toBe('pending');
    const item=page.locator(`[data-consent-request-id="${request.receipt.result.request_id}"]`);
    await expect(item.getByTestId('consent-request-status')).toHaveText('pending');
    const proposed=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
    await item.getByTestId('prepare-consent-grant').click();
    const grantResponse=await proposed;expect(grantResponse.status()).toBe(200);
    const grantProposal=await grantResponse.json();
    expect(grantProposal.command.target.categories).toEqual(['agenda','profile']);
    expect(Date.parse(grantProposal.command.target.expires_at)).toBe(Date.parse(request.receipt.result.expires_at));
    const granted=await reviewConsent(page,grantProposal.command.command_id);
    expect(granted.receipt.result.status).toBe('active');
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/consent-receipt-${width}.png`,fullPage:true});
    await page.keyboard.press('Escape');await openSharing(page);
    await expect(page.getByTestId('sharing-status')).toHaveText('You have active consent for this profile sharing.');
    const saved=page.locator(`[data-consent-grant-id="${granted.receipt.result.grant_id}"]`);
    await expect(saved.getByTestId('consent-grant-status')).toHaveText('active');
    await saved.scrollIntoViewIfNeeded();
    const dialog=page.getByTestId('sharing-page');
    expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/consent-current-${width}.png`,fullPage:true});
    const revocation=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
    await saved.getByTestId('prepare-consent-revoke').click();
    const revokeResponse=await revocation;expect(revokeResponse.status()).toBe(200);
    const revokeProposal=await revokeResponse.json();
    const revoked=await reviewConsent(page,revokeProposal.command.command_id);
    expect(revoked.receipt.result.status).toBe('revoked');
    await page.keyboard.press('Escape');await openSharing(page);
    await expect(page.getByTestId('sharing-status')).toHaveText('There is no active consent for this profile sharing.');
    await expect(page.locator(`[data-consent-grant-id="${granted.receipt.result.grant_id}"]`).getByTestId('consent-grant-status')).toHaveText('revoked');
    await page.keyboard.press('Escape');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  }
  expect(chatCalls).toEqual([]);
});

test('failed consent records do not display an apparently active grant',async({page})=>{
  await seedSession(page);await page.goto('/');
  const failed=page.waitForResponse(response=>response.url().endsWith('/consent/records'));
  await openMenuItem(page,'open-data-sharing');expect((await failed).status()).toBe(503);
  await expect(page.getByTestId('sharing-error')).toBeVisible();
  await expect(page.getByTestId('sharing-status')).toHaveCount(0);
  await expect(page.getByTestId('consent-grants')).toHaveCount(0);
  await expect(page.getByTestId('request-consent')).toHaveCount(0);
});
