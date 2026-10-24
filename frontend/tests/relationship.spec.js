import {openMenu,openMenuItem} from './menu.js';
import {test,expect} from '@playwright/test';
import {fixture,seedSession} from './nativeSession';

test.beforeEach(async({page})=>seedSession(page));

test('relationship records use real scoped requests and available times',async({page})=>{
  const chatCalls=[];
  page.on('request',request=>{if(request.url().endsWith('/chat'))chatCalls.push(request.url());});
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    await page.goto('/');
    await openMenuItem(page,'open-data-sharing');
    const loaded=Promise.all(['/relationship/requests','/relationship/slots'].map(path=>page.waitForResponse(response=>response.url().endsWith(path))));
    await page.getByTestId('open-relationship-details').click();
    expect((await loaded).map(response=>response.status())).toEqual([200,200]);
    if(fixture.request_count)await expect(page.getByTestId('relationship-request').first()).toBeVisible();
    else await expect(page.getByTestId('requests-empty')).toBeVisible();
    await expect(page.getByTestId('relationship-request')).toHaveCount(fixture.request_count);
    if(fixture.expects_slots)await expect(page.getByTestId('relationship-slot').first()).toBeVisible();
    else await expect(page.getByTestId('slots-empty')).toBeVisible();
    await expect(page.getByTestId('relationship-error')).toHaveCount(0);
    const dialog=page.getByTestId('relationship-page');
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
    await page.goBack();
    await expect(dialog).toHaveCount(0);
  }
  expect(chatCalls).toEqual([]);
});

test('unavailable appointment registry shows an explicit relationship error',async({page})=>{
  await page.goto('/');
  await openMenuItem(page,'open-data-sharing');
  const loaded=page.waitForResponse(response=>response.url().endsWith('/relationship/slots'));
  await page.getByTestId('open-relationship-details').click();
  expect((await loaded).status()).toBe(503);
  await expect(page.getByTestId('relationship-error')).toBeVisible();
  await expect(page.getByTestId('relationship-slots')).toHaveCount(0);
});


test('real personal relationship answer persists with owned advisor evidence',async({page})=>{
  test.skip(fixture.relationship_live!==true,'Requires approval for the real relationship model check');
  test.setTimeout(90000);
  await page.goto('/');await page.locator('#chat-composer').fill('Who is my relationship manager?');
  const received=page.waitForResponse(response=>response.url().endsWith('/chat')&&response.request().method()==='POST');
  await page.getByTestId('send-message').click();expect((await received).status()).toBe(200);
  const section=page.getByTestId('answer-sections').locator('section[data-domain="relationship"]');
  await expect(section).toHaveAttribute('data-response-origin','ai',{timeout:75000});
  await expect(section).toHaveAttribute('data-prompt-version',/^v2\+/);
  await expect(section).toContainText('Marcus Chua');
  await section.getByTestId('answer-sources').locator('summary').click();
  await expect(section.locator('[data-source-id="relationship-assignment"]')).toBeVisible();
  const answer=await section.locator('.answer-text').innerText();
  await page.getByTestId('reload-saved-chat').click();await expect(section.locator('.answer-text')).toHaveText(answer);
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/relationship-agent-${width}.png`,fullPage:true});
  }
});
