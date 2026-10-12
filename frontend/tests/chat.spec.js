import {test,expect} from '@playwright/test';
async function ask(page,q){await page.getByRole('textbox',{name:'Your message'}).fill(q);await page.getByRole('button',{name:'Send',exact:true}).click();await expect(page.getByRole('button',{name:'Send',exact:true})).toBeVisible();}
test.beforeEach(async({page})=>{await page.goto('/');});
test('guest answer comes from backend and source opens',async({page})=>{
 await ask(page,'How do I download a statement?');
 await expect(page.locator('.bank-chat-message.assistant')).toContainText('cannot open or download');
 await page.getByText('Sources',{exact:true}).click();await page.getByRole('button',{name:'Finding a statement'}).click();
 await expect(page.getByRole('region',{name:'Source details'})).toContainText('Fictional demo guidance');
});
test('sixteenth and later questions work; new chat clears context',async({page})=>{
 for(let i=0;i<18;i++)await ask(page,'Explain loan eligibility '+i);
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
 await ask(page,'My card was declined');await page.getByRole('button',{name:'Talk to a person'}).click();
 await expect(page.getByRole('button',{name:'Copy summary'})).toBeDisabled();
 await page.getByRole('checkbox').check();await page.getByRole('button',{name:'Copy summary'}).click();
 await expect(page.getByText('Copied. No ticket was sent.')).toBeVisible();
 expect(await page.evaluate(()=>navigator.clipboard.readText())).toContain('My card was declined');
});
test('provider errors recover on next question',async({page})=>{
 await ask(page,'loan simulate failure');await expect(page.locator('.bank-chat-bubble.error')).toContainText('temporarily unavailable');
 await ask(page,'loan eligibility');await expect(page.locator('.bank-chat-message.assistant').last()).toContainText('preliminary');
});
test('reset during a pending response prevents stale answer',async({page})=>{
 await page.getByRole('textbox',{name:'Your message'}).fill('loan slow response');await page.getByRole('button',{name:'Send',exact:true}).click();
 await page.getByRole('button',{name:'New chat',exact:true}).click();await page.waitForTimeout(2500);
 await expect(page.locator('.bank-chat-message')).toHaveCount(0);
});
test('mobile layout fits and controls remain usable',async({page})=>{
 await page.setViewportSize({width:390,height:844});await ask(page,'I lost my card');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
 await page.screenshot({path:'test-results/mobile-chat.png',fullPage:true});
});
