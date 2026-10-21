import {test,expect} from '@playwright/test';
import {fixture,seedSession} from './nativeSession';

test.beforeEach(async({page})=>seedSession(page));

test('relationship records use real scoped requests and available times',async({page})=>{
  const chatCalls=[];
  page.on('request',request=>{if(request.url().endsWith('/chat'))chatCalls.push(request.url());});
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    await page.goto('/');
    await page.getByTestId('open-data-sharing').click();
    const loaded=Promise.all(['/relationship/requests','/relationship/slots'].map(path=>page.waitForResponse(response=>response.url().endsWith(path))));
    await page.getByTestId('open-relationship-details').click();
    expect((await loaded).map(response=>response.status())).toEqual([200,200]);
    if(fixture.request_count)await expect(page.getByTestId('relationship-request').first()).toBeVisible();
    else await expect(page.getByTestId('requests-empty')).toBeVisible();
    await expect(page.getByTestId('relationship-request')).toHaveCount(fixture.request_count);
    if(fixture.expects_slots)await expect(page.getByTestId('relationship-slot').first()).toBeVisible();
    else await expect(page.getByTestId('slots-empty')).toBeVisible();
    await expect(page.getByTestId('relationship-error')).toHaveCount(0);
    const dialog=page.locator('dialog[aria-label="Data sharing"]');
    expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/relationship-${width}.png`,fullPage:true});
    if(width===320) {
      const lastResult=fixture.expects_slots?page.getByTestId('relationship-slot').last():page.getByTestId('slots-empty');
      await lastResult.scrollIntoViewIfNeeded();
      await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/relationship-bottom-${width}.png`,fullPage:true});
    }
    await page.getByTestId('slots-to').fill('2020-01-01');
    const invalid=page.waitForResponse(response=>response.url().endsWith('/relationship/slots'));
    await page.getByTestId('refresh-relationship').click();
    expect((await invalid).status()).toBe(422);
    await expect(page.getByTestId('relationship-error')).toBeVisible();
    await expect(page.getByTestId('relationship-slots')).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
  }
  expect(chatCalls).toEqual([]);
});

test('unavailable appointment registry shows an explicit relationship error',async({page})=>{
  await page.goto('/');
  await page.getByTestId('open-data-sharing').click();
  const loaded=page.waitForResponse(response=>response.url().endsWith('/relationship/slots'));
  await page.getByTestId('open-relationship-details').click();
  expect((await loaded).status()).toBe(503);
  await expect(page.getByTestId('relationship-error')).toBeVisible();
  await expect(page.getByTestId('relationship-slots')).toHaveCount(0);
});
