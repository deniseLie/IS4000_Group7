import {test,expect} from '@playwright/test';
import {seedSession} from './nativeSession';

test('current client receives owned account and relationship evidence and can reopen it',async({page})=>{
 test.setTimeout(540000);await seedSession(page);await page.goto('/#chat');
 const ask=async text=>{
  const before=await page.getByTestId('chat-message-assistant').count();
  await page.locator('#chat-composer').fill(text);await page.getByTestId('send-message').click();
  await expect(page.getByTestId('chat-message-assistant')).toHaveCount(before+1,{timeout:170000});
  await expect(page.locator('#chat-composer')).toBeEnabled({timeout:170000});
  return page.getByTestId('chat-message-assistant').last();
 };
 const balance=await ask('What is my account balance?');
 await expect(balance).toContainText(/SGD\s*250,?000(?:\.00)?/);
 await expect(balance.locator('[data-source-id="retail-accounts"]')).toHaveCount(1);
 const transfer=await ask('My FAST transfer to a supplier is still pending. Is it late and what should I do?');
 await expect(transfer).toHaveAttribute('data-response-origin','ai');
 await expect(transfer).toContainText(/pending/i);
 const relationship=await ask('Who is my relationship manager?');
 await expect(relationship).toContainText('Sophie');
 await expect(relationship.locator('[data-source-id="relationship-assignment"]')).toHaveCount(1);
 await page.reload();await expect(page.getByTestId('chat-message-assistant')).toHaveCount(3);
 await expect(page.getByTestId('chat-message-assistant').last()).toContainText('Sophie');
 await page.setViewportSize({width:390,height:900});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/current-client-mobile.png`,fullPage:true});
});
