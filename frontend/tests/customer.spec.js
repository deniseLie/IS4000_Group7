import {test,expect} from '@playwright/test';
import {fixture,seedSession} from './nativeSession';

test.beforeEach(async({page})=>seedSession(page));

test('customer records use real scoped audited services at all viewport sizes',async({page})=>{
  const chatCalls=[];
  page.on('request',request=>{if(request.url().endsWith('/chat'))chatCalls.push(request.url());});
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    await page.goto('/');
    const loaded=page.waitForResponse(response=>response.url().endsWith('/customer/transactions'));
    const advisorLoaded=page.waitForResponse(response=>response.url().endsWith('/customer/advisor'));
    await page.getByTestId('open-records').click();
    expect((await loaded).status()).toBe(200);
    await expect(page.getByTestId('records-name')).toHaveText(fixture.name);
    const advisor=(await (await advisorLoaded).json()).data?.current;
    if(advisor)await expect(page.getByTestId('records-advisor-contact')).toHaveText(`Email: ${advisor.contact.email||'Not recorded'} · Phone: ${advisor.contact.phone||'Not recorded'}`);
    else await expect(page.getByTestId('records-advisor-contact')).toHaveCount(0);
    await expect(page.getByTestId('records-documents-empty')).toBeVisible();
    for(const [type,count] of [['account',fixture.account_count],['card',fixture.card_count],['loan',fixture.loan_count],['transaction',fixture.transaction_count]]) {
      await expect(page.getByTestId(`records-${type}`)).toHaveCount(count);
    }
    const dialog=page.getByTestId('records-content').locator('xpath=ancestor::dialog');
    expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/customer-${width}.png`,fullPage:true});
    await page.getByTestId('records-from').fill('2040-01-01');
    const filtered=page.waitForResponse(response=>response.url().endsWith('/customer/transactions'));
    await page.getByTestId('reload-records').click();
    expect((await filtered).status()).toBe(200);
    await expect(page.getByTestId('records-transactions-empty')).toBeVisible();
    await page.getByTestId('records-to').fill('2039-01-01');
    const invalid=page.waitForResponse(response=>response.url().endsWith('/customer/transactions'));
    await page.getByTestId('reload-records').click();
    expect((await invalid).status()).toBe(422);
    await expect(page.getByTestId('records-error')).toBeVisible();
    await expect(page.getByTestId('records-content')).toHaveCount(0);
    await page.getByTestId('records-to').fill('2041-01-01');
    const recovered=page.waitForResponse(response=>response.url().endsWith('/customer/transactions'));
    await page.getByTestId('reload-records').click();
    expect((await recovered).status()).toBe(200);
    await expect(page.getByTestId('records-name')).toHaveText(fixture.name);
    await page.getByTestId('records-content').locator('section').last().scrollIntoViewIfNeeded();
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/customer-bottom-${width}.png`,fullPage:true});
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(page.getByTestId('open-records')).toBeFocused();
  }
  expect(chatCalls).toEqual([]);
});

test('unavailable customer records show an explicit error without stale data',async({page})=>{
  await page.goto('/');
  const failed=page.waitForResponse(response=>response.url().endsWith('/customer/accounts'));
  await page.getByTestId('open-records').click();
  expect((await failed).status()).toBe(503);
  await expect(page.getByTestId('records-error')).toBeVisible();
  await expect(page.getByTestId('records-content')).toHaveCount(0);
});
