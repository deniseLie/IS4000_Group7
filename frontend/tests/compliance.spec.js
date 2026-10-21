import {test,expect} from '@playwright/test';
import {seedSession} from './nativeSession.js';

test.beforeEach(async({page})=>seedSession(page));

test('real personal compliance answer persists with owned case evidence',async({page})=>{
  test.setTimeout(90000);
  await page.goto('/');await page.locator('#chat-composer').fill('What is my compliance review status?');
  const received=page.waitForResponse(response=>response.url().endsWith('/chat')&&response.request().method()==='POST');
  await page.getByTestId('send-message').click();expect((await received).status()).toBe(200);
  const section=page.getByTestId('answer-sections').locator('section[data-domain="compliance"]');
  await expect(section).toHaveAttribute('data-response-origin','ai',{timeout:75000});
  await expect(section).toHaveAttribute('data-prompt-version',/^v2\+/);
  await expect(section).toContainText(/awaiting.*documents|waiting.*documents|outstanding.*documents/i);
  await section.getByTestId('answer-sources').locator('summary').click();
  await expect(section.getByTestId('answer-sources')).toContainText('Authenticated synthetic review case');
  const answer=await section.locator('.answer-text').innerText();
  await page.getByTestId('reload-saved-chat').click();await expect(section.locator('.answer-text')).toHaveText(answer);
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/compliance-${width}.png`,fullPage:true});
  }
});
