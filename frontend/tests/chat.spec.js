import {test,expect} from '@playwright/test';
async function ask(page,q){await page.getByRole('textbox',{name:'Your message'}).fill(q);await page.getByRole('button',{name:'Send',exact:true}).click();await expect(page.getByRole('button',{name:'Send',exact:true})).toBeVisible();}
test.beforeEach(async({page})=>{await page.goto('/');});
test('sixteenth and later questions work; new chat clears context',async({page})=>{
 for(let i=0;i<18;i++)await ask(page,'What is my balance? '+i);
 await expect(page.locator('.bank-chat-message.assistant')).toHaveCount(18);
 await expect(page.locator('.bank-chat-message.assistant .error')).toHaveCount(0);
 await expect(page.getByRole('status')).toContainText('Older messages');
 await page.getByRole('button',{name:'New chat',exact:true}).click();
 await expect(page.locator('.bank-chat-message')).toHaveCount(0);
 await expect(page.getByText('How can I help you today?')).toBeVisible();
});
test('privacy input is withheld before any request',async({page})=>{
 let calls=0;page.on('request',r=>{if(r.url().endsWith('/chat'))calls++;});
 await ask(page,'My PIN is 123456');await expect(page.getByRole('status')).toContainText('Private details were removed');expect(calls).toBe(0);
});
test('lost card and unauthenticated personal enquiry',async({page})=>{
 await ask(page,'I lost my card');await expect(page.locator('.bank-chat-message.assistant').last()).toContainText('immediately');
 await ask(page,'What is my balance?');await expect(page.locator('.bank-chat-message.assistant').last()).toContainText('Sign in');
});
test('summary requires review and makes no handoff claim',async({page,context})=>{
 await context.grantPermissions(['clipboard-read','clipboard-write']);
 await ask(page,'I lost my card');await page.getByRole('button',{name:'Talk to a person'}).click();
 await expect(page.getByRole('button',{name:'Copy summary'})).toBeDisabled();
 await page.getByRole('checkbox').check();await page.getByRole('button',{name:'Copy summary'}).click();
 await expect(page.getByText('Copied. No ticket was sent.')).toBeVisible();
 expect(await page.evaluate(()=>navigator.clipboard.readText())).toContain('I lost my card');
});
test('network failure is shown and next request still works',async({page})=>{
 await page.route('**/api/chat',route=>route.abort('failed'),{times:1});
 await ask(page,'I lost my card');await expect(page.locator('.bank-chat-bubble.error')).toBeVisible();
 await ask(page,'I lost my card');await expect(page.locator('.bank-chat-message.assistant').last()).toContainText('immediately');
});
test('mobile layout fits and controls remain usable',async({page})=>{
 await page.setViewportSize({width:390,height:844});await ask(page,'I lost my card');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
 await page.screenshot({path:'test-results/mobile-chat.png',fullPage:true});
});
