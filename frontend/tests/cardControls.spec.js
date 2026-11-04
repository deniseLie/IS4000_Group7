import {test,expect} from '@playwright/test';
import {seedSession} from './nativeSession';

// Actual customer confirmation, receipts and bank-owned locks over the native API.
test('card self-service freezes unfreezes and cancels without changing bank-only decisions',async({page})=>{
  await seedSession(page);await page.goto('/#overview');
  await page.getByTestId('card-manage').first().click();
  const controls=page.locator('[data-testid="card-controls"][data-card="4821"]');
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    await expect(controls.getByTestId('card-freeze')).toBeEnabled();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  }
  await controls.getByTestId('card-freeze').click();
  await controls.getByTestId('card-confirm').click();
  await expect(controls.getByTestId('card-unfreeze')).toBeEnabled();
  await page.reload();await page.getByTestId('card-manage').first().click();
  await expect(controls.getByTestId('card-unfreeze')).toBeEnabled();
  await controls.getByTestId('card-unfreeze').click();await controls.getByTestId('card-confirm').click();
  await expect(controls.getByTestId('card-freeze')).toBeEnabled();
  await controls.getByTestId('card-freeze').click();await controls.getByTestId('card-cancel').click();
  await expect(controls.getByTestId('card-freeze')).toBeEnabled();
  await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/card-controls-mobile.png`,fullPage:true});
});
