import {test,expect} from '@playwright/test';
import fs from 'node:fs';
async function ask(page,q){await page.getByRole('textbox',{name:'Your message'}).fill(q);await page.getByRole('button',{name:'Send',exact:true}).click();await expect(page.getByRole('button',{name:'Send',exact:true})).toBeVisible();}
test.beforeEach(async({page})=>{await page.goto('/');});
test('sixteenth and later questions work; new chat clears context',async({page})=>{
 const sent=[];page.on('request',r=>{if(r.url().endsWith('/chat'))sent.push(r.postDataJSON());});
 for(let i=0;i<18;i++)await ask(page,'What is my balance? '+i);
 await expect(page.locator('.bank-chat-message.assistant')).toHaveCount(18);
 await expect(page.locator('.bank-chat-message.assistant .error')).toHaveCount(0);
 await expect(page.getByRole('status')).toContainText('Older messages');
 await page.getByRole('button',{name:'New chat',exact:true}).click();
 await expect(page.locator('.bank-chat-message')).toHaveCount(0);
 await expect(page.getByRole('heading',{name:'Banking questions. Clearly answered.'})).toBeVisible();
 expect(sent.at(-1).messages.length).toBeLessThanOrEqual(29);
 expect(sent.at(-1).messages.at(-1).content).toBe('What is my balance? 17');
 await ask(page,'I lost my card');
 expect(sent.at(-1).messages).toEqual([{role:'user',content:'I lost my card'}]);
});
test('refresh clears conversation and sends no previous context',async({page})=>{
 await ask(page,'What is my balance?');await page.reload();
 await expect(page.locator('.bank-chat-message')).toHaveCount(0);
 const sent=page.waitForRequest(r=>r.url().endsWith('/chat'));
 await ask(page,'I lost my card');
 expect((await sent).postDataJSON().messages).toEqual([{role:'user',content:'I lost my card'}]);
});
test('chat opens by saying it is an AI assistant giving general information',async({page})=>{
 await expect(page.getByTestId('chat-greeting')).toHaveText("I'm DemoBank's AI assistant. I give general information, not advice.");
});
test('language scope is explained and safety guidance stays labelled',async({page})=>{
 await page.getByLabel('Answer language',{exact:true}).selectOption('Chinese');
 await expect(page.locator('label.pill-select')).toHaveAttribute('title','The language applies to AI answers only');
 const sent=page.waitForRequest(r=>r.url().endsWith('/chat'));
 await ask(page,'I lost my card');
 expect((await sent).postDataJSON().language).toBe('Chinese');
 await expect(page.locator('.bank-chat-message.assistant')).toContainText('Safety guidance');
 await expect(page.locator('.bank-chat-message.assistant')).toContainText('immediately');
 await expect(page.getByText('Demo AI assistant.',{exact:false})).toContainText('AI answers can contain errors');
});
test('new chat discards an in-flight request and remains usable',async({page})=>{
 let release;const gate=new Promise(resolve=>{release=resolve;});
 let entered;const intercepted=new Promise(resolve=>{entered=resolve;});
 await page.route('**/api/chat',async route=>{entered();await gate;await route.continue().catch(()=>{});},{times:1});
 await page.getByRole('textbox',{name:'Your message'}).fill('I lost my card');
 await page.getByRole('button',{name:'Send',exact:true}).click();await intercepted;
 await page.getByRole('button',{name:'New chat',exact:true}).click();release();
 await expect(page.locator('.bank-chat-message')).toHaveCount(0);
 await ask(page,'What is my balance?');
 await expect(page.locator('.bank-chat-message.assistant')).toHaveCount(1);
 await expect(page.locator('.bank-chat-message.assistant')).toContainText('Sign in');
});
test('reset does not reopen a source whose request was still loading',async({page})=>{
 await ask(page,'I lost my card');
 let release;const gate=new Promise(resolve=>{release=resolve;});
 let entered;const intercepted=new Promise(resolve=>{entered=resolve;});
 await page.route('**/api/knowledge/lost-card',async route=>{entered();await gate;await route.continue();});
 await page.getByText('Sources',{exact:true}).click();
 await page.getByRole('button',{name:'Lost or stolen card guidance'}).click();await intercepted;
 await page.getByRole('button',{name:'New chat',exact:true}).click();
 const received=page.waitForResponse(r=>r.url().endsWith('/knowledge/lost-card'));release();
 await (await received).finished();
 await ask(page,'What is my balance?');
 await expect(page.getByRole('region',{name:'Source details'})).toHaveCount(0);
});
test('privacy input is withheld before any request',async({page})=>{
 let calls=0;page.on('request',r=>{if(r.url().endsWith('/chat'))calls++;});
 const cases=JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/privacy-cases.json',import.meta.url),'utf8'));
 for(const c of cases.filter(c=>c.secret)) {
  await ask(page,c.text);await expect(page.getByRole('status')).toContainText('Private details were removed');
  await expect(page.locator('.bank-chat-message')).toHaveCount(0);
 }
 expect(calls).toBe(0);
 await ask(page,'I lost my card and my PIN is 1 2 3 4');
 await expect(page.getByRole('status')).toContainText('contact your bank immediately');expect(calls).toBe(0);
});
test('lost card and unauthenticated personal enquiry',async({page})=>{
 for(const q of ['I lost my card','Someone stole my debit card',"I can't find my card"]) {
  await ask(page,q);const answer=page.locator('.bank-chat-message.assistant').last();
  await expect(answer).toContainText('immediately');await expect(answer).toHaveAttribute('data-response-origin','safety');
  await expect(answer).toContainText('Safety guidance');
 }
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

test('responsive home, topic drafts and sign-in navigation work at desktop and mobile sizes',async({page})=>{
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 for(const width of [1440,768,390,320]) {
  await page.setViewportSize({width,height:900});await page.goto('/');
  await expect(page.getByRole('heading',{name:'Banking questions. Clearly answered.'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  const welcome=page.locator('.bank-chat-welcome');
  await welcome.getByRole('button',{name:/Cards & payments/}).click();
  await expect(page.getByRole('textbox',{name:'Your message'})).toBeFocused();
  await expect(page.getByRole('textbox',{name:'Your message'})).toHaveValue('Does paying only the minimum on my credit card avoid interest?');
  await expect(page.locator('.bank-chat-message')).toHaveCount(0);
  await page.getByRole('button',{name:'Sign in',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Welcome back'})).toBeVisible();
  await page.getByLabel('Password',{exact:true}).fill('synthetic-ui-only');
  await page.getByRole('button',{name:'Show',exact:true}).click();
  await expect(page.getByLabel('Password',{exact:true})).toHaveAttribute('type','text');
  await page.getByRole('button',{name:'Hide',exact:true}).click();
  await expect(page.getByLabel('Password',{exact:true})).toHaveAttribute('type','password');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  await page.screenshot({path:`test-results/design-login-${width}.png`,fullPage:true});
  await page.getByRole('button',{name:'Back to general help',exact:false}).click();
  await expect(page.getByRole('textbox',{name:'Your message'})).toBeVisible();
  await page.screenshot({path:`test-results/design-home-${width}.png`,fullPage:true});
 }
 expect(errors).toEqual([]);
});
test('source and support dialogs keep keyboard focus and close on Escape',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await ask(page,'I lost my card');await page.getByText('Sources',{exact:true}).click();
 const trigger=page.getByRole('button',{name:'Lost or stolen card guidance'});
 await trigger.click();
 const source=page.getByRole('dialog',{name:'Source details'});
 await expect(source).toBeVisible();
 await expect(source.getByRole('button',{name:'Close source'})).toBeFocused();
 await page.keyboard.press('Shift+Tab');
 expect(await source.evaluate(node=>node.contains(document.activeElement))).toBe(true);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
 await page.screenshot({path:'test-results/design-source-mobile.png',fullPage:true});
 await page.keyboard.press('Escape');await expect(source).toHaveCount(0);await expect(trigger).toBeFocused();
 const support=page.getByRole('button',{name:'Talk to a person'});await support.click();
 const dialog=page.getByRole('dialog',{name:'Human support'});await expect(dialog).toBeVisible();
 await page.getByRole('checkbox').check();await expect(page.getByRole('button',{name:'Copy summary'})).toBeEnabled();
 await page.getByRole('textbox',{name:'Support summary'}).fill('Edited synthetic support note');
 await expect(page.getByRole('button',{name:'Copy summary'})).toBeDisabled();
 await page.screenshot({path:'test-results/design-support-mobile.png',fullPage:true});
 await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);await expect(support).toBeFocused();
});
test('Up and Down in an empty message box recall sent questions',async({page})=>{
 await ask(page,'What is my balance?');await ask(page,'I lost my card');
 const box=page.getByRole('textbox',{name:'Your message'});await box.focus();
 await box.press('ArrowUp');await expect(box).toHaveValue('I lost my card');
 await box.press('ArrowUp');await expect(box).toHaveValue('What is my balance?');
 await box.press('ArrowUp');await expect(box).toHaveValue('What is my balance?');
 await box.press('ArrowDown');await expect(box).toHaveValue('I lost my card');
 await box.press('ArrowDown');await expect(box).toHaveValue('');
 await box.fill('My own draft');await box.press('ArrowUp');await expect(box).toHaveValue('My own draft');
});
