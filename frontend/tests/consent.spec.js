import {openMenu,openMenuItem} from './menu.js';
import {test,expect} from '@playwright/test';
import {fixture,seedSession} from './nativeSession';

test.beforeEach(async({page})=>seedSession(page));

test('consent status uses real assigned manager and registry at desktop and mobile sizes',async({page})=>{
  const chatCalls=[];
  page.on('request',request=>{if(request.url().endsWith('/chat'))chatCalls.push(request.url());});
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    await page.goto('/');
    const loaded=page.waitForResponse(response=>response.url().endsWith('/consent/rm'));
    await openMenuItem(page,'open-data-sharing');
    expect((await loaded).status()).toBe(200);
    const dialog=page.getByTestId('sharing-page');
    await expect(dialog).toBeVisible();
    await expect(page.getByTestId('sharing-recipient')).toHaveText(fixture.manager.replace(/\s*\(DEMO\)\s*$/,''));  // the page shows "Demo" once, not after names
    await expect(page.getByTestId('sharing-status')).toHaveText(fixture.granted?
      'You have active consent for this profile sharing.':'There is no active consent for this profile sharing.');
    await expect(page.getByTestId('sharing-error')).toHaveCount(0);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/sharing-${width}.png`,fullPage:true});
    await page.goBack();
    await expect(dialog).toHaveCount(0);
    await expect(page.locator('#chat-composer')).toBeVisible();
  }
  expect(chatCalls).toEqual([]);
});

test('unavailable registry is an explicit UI error without a consent status',async({page})=>{
  await page.goto('/');
  const loaded=page.waitForResponse(response=>response.url().endsWith('/consent/rm'));
  await openMenuItem(page,'open-data-sharing');
  expect((await loaded).status()).toBe(503);
  await expect(page.getByTestId('sharing-error')).toBeVisible();
  await expect(page.getByTestId('sharing-status')).toHaveCount(0);
  await expect(page.getByTestId('sharing-no-manager')).toHaveCount(0);
});
