import {openMenu,openMenuItem} from './menu.js';
import {test,expect} from '@playwright/test';
import {seedSession} from './nativeSession';

async function openDrafts(page) {
  await openMenuItem(page,'open-data-sharing');
  await expect(page.getByTestId('sharing-recipient')).toBeVisible();
  const read=page.waitForResponse(response=>response.url().endsWith('/relationship/agendas'));
  await page.getByTestId('open-agenda-drafts').click();
  return read;
}
async function saveDraft(page) {
  const saved=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
  await page.getByTestId('save-agenda').click();
  const response=await saved;expect(response.status()).toBe(200);
  const action=await response.json();expect(action.receipt.outcome).toBe('succeeded');
  await expect(page.getByTestId('agenda-save-result')).toContainText(action.receipt.receipt_id);
  const item=page.locator(`[data-agenda-id="${action.command.target.agenda_id}"]`);
  await expect(item.getByTestId('agenda-content')).toHaveText(action.command.target.content);
  await expect(item.getByTestId('agenda-revision')).toHaveText(String(action.receipt.result.revision));
  return {action,item};
}
test('private agenda drafts save and edit through real action receipts',async({page})=>{
  await seedSession(page);const calls=[];
  page.on('request',request=>{if(request.url().endsWith('/chat'))calls.push(request.url());});
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});await page.goto('/');
    expect((await openDrafts(page)).status()).toBe(200);
    await page.getByTestId('new-agenda').click();
    await expect(page.getByTestId('save-agenda')).toBeDisabled();
    await page.getByTestId('agenda-topics').fill('x'.repeat(301));
    await expect(page.getByTestId('save-agenda')).toBeDisabled();
    await page.getByTestId('agenda-topics').fill(`Discuss my retirement\nContact agenda@example.invalid\n${'💬'.repeat(300)}`);
    await page.getByTestId('agenda-field-name').check();
    const first=await saveDraft(page);
    expect(first.action.command.target.categories).toEqual(['agenda','profile']);
    expect(first.action.command.target.content).toContain('David Lee (DEMO)');
    expect(first.action.command.target.content).not.toContain('agenda@example.invalid');
    await first.item.getByTestId('edit-agenda').click();
    await page.getByTestId('agenda-topics').fill('Review savings plans');
    await page.getByTestId('agenda-field-name').uncheck();
    const second=await saveDraft(page);
    expect(second.action.receipt.result.revision).toBe(2);
    expect(second.action.command.target.categories).toEqual(['agenda']);
    expect(second.action.command.target.content).toBe('Topics:\n- Review savings plans');
    await page.getByTestId('review-agenda-actions').click();
    const command=page.locator(`[data-command-id="${second.action.command.command_id}"]`);
    await expect(command.getByTestId('command-agenda')).toContainText(second.action.command.target.content);
    await expect(command.getByTestId('command-state')).toHaveText('succeeded');
    await expect(command.getByTestId('command-receipt')).toContainText(second.action.receipt.receipt_id);
    await page.keyboard.press('Escape');expect((await openDrafts(page)).status()).toBe(200);
    const persisted=page.locator(`[data-agenda-id="${second.action.command.target.agenda_id}"]`);
    await expect(persisted.getByTestId('agenda-revision')).toHaveText('2');
    await persisted.scrollIntoViewIfNeeded();
    const dialog=page.getByTestId('sharing-page');
    expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/agenda-draft-${width}.png`,fullPage:true});
    await persisted.getByTestId('edit-agenda').click();
    await expect(page.getByTestId('agenda-topics')).toHaveValue('Review savings plans');
    await page.getByTestId('reload-agendas').click();
    await expect(page.getByTestId('agenda-editor')).toHaveCount(0);
    await page.getByTestId('new-agenda').click();
    await page.getByTestId('agenda-topics').fill('A later draft');
    await page.getByTestId('agenda-field-employment_status').check();
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/agenda-editor-${width}.png`,fullPage:true});
    await page.keyboard.press('Escape');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  }
  expect(calls).toEqual([]);
});

test('failed agenda reads block editing and display a clear error',async({page})=>{
  await seedSession(page);await page.goto('/');
  expect((await openDrafts(page)).status()).toBe(503);
  await expect(page.getByTestId('agenda-error')).toBeVisible();
  await expect(page.getByTestId('new-agenda')).toHaveCount(0);
  await expect(page.getByTestId('agenda-count')).toHaveCount(0);
});
