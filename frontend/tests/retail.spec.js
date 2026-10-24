import {test,expect} from '@playwright/test';
import {seedSession} from './nativeSession.js';

test.beforeEach(async({page})=>seedSession(page));

test('real personal retail answer persists with owned evidence and responsive layout',async({page})=>{
  test.setTimeout(90000);
  await page.goto('/');
  await page.locator('#chat-composer').fill('What is my account balance?');
  const received=page.waitForResponse(response=>response.url().endsWith('/chat')&&response.request().method()==='POST');
  await page.getByTestId('send-message').click();const response=await received;expect(response.status()).toBe(200);
  const section=page.getByTestId('answer-sections').locator('section[data-domain="retail"]');
  await expect(section).toHaveAttribute('data-response-origin','ai',{timeout:75000});
  await expect(section).toHaveAttribute('data-prompt-version',/^v3\+/);
  await expect(section).toContainText(/SGD\s*5,?230\.45/);
  await section.getByTestId('answer-sources').locator('summary').click();
  await expect(section.getByTestId('answer-sources')).toContainText('Authenticated synthetic accounts');
  const answer=await section.locator('.answer-text').innerText();
  await page.getByTestId('reload-saved-chat').click();
  await expect(section.locator('.answer-text')).toHaveText(answer);
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/retail-${width}.png`,fullPage:true});
  }
});
