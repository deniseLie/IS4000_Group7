import {test,expect} from '@playwright/test';
import {seedSession} from './nativeSession.js';

test.beforeEach(async({page})=>seedSession(page));
const itemId='00000000-0000-4000-8000-100200000011';

test('checklist preparation saves an audited mark and preserves bank status',async({page})=>{
  await page.goto('/');
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    await page.getByTestId('open-records').click();
    const item=page.locator(`[data-checklist-item-id="${itemId}"]`);
    await expect(item.getByTestId('checklist-mark')).toHaveValue('not_prepared');
    await expect(item.getByTestId('save-checklist-mark')).toBeDisabled();
    await item.getByTestId('checklist-mark').selectOption('prepared');
    const submitted=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
    await item.getByTestId('save-checklist-mark').click();const response=await submitted;expect(response.status()).toBe(200);
    const saved=await response.json();expect(saved.receipt.result.submission_status).toBe('not_submitted');
    await expect(item.getByTestId('checklist-outcome')).toContainText(saved.receipt.receipt_id);
    await item.getByTestId('checklist-outcome').scrollIntoViewIfNeeded();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/checklist-${width}.png`,fullPage:true});
    await page.getByTestId('reload-records').click();
    await expect(item.getByTestId('checklist-mark')).toHaveValue('prepared');
    await item.getByTestId('checklist-mark').selectOption('not_prepared');
    await item.getByTestId('save-checklist-mark').click();
    await expect(item.getByTestId('checklist-outcome')).toContainText('Recorded preparation: not prepared.');
    await page.keyboard.press('Escape');
    await page.getByTestId('open-actions').click();
    const action=page.locator(`[data-command-id="${saved.command.command_id}"]`);
    await expect(action.getByTestId('command-receipt')).toContainText(saved.receipt.receipt_id);
    await expect(action.getByTestId('command-checklist')).toBeVisible();
    await page.keyboard.press('Escape');
  }
});

test('closed checklist review blocks preparation changes',async({page})=>{
  await page.goto('/');await page.getByTestId('open-records').click();
  await expect(page.getByTestId('records-case')).toBeVisible();
  await expect(page.getByTestId('save-checklist-mark')).toHaveCount(0);
});
