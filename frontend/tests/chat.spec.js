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
 await expect(page.getByRole('heading',{name:'Hi, how can I help?'})).toBeVisible();
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
 await expect(page.getByTestId('chat-greeting')).toHaveText("I'm the UBS Connect AI assistant. I give general information, not advice.");
});
test('language scope is explained and safety guidance stays labelled',async({page})=>{
 await page.getByLabel('Answer language',{exact:true}).selectOption('Chinese');
 await expect(page.locator('label.pill-select')).toHaveAttribute('title','The language applies to AI answers only');
 const sent=page.waitForRequest(r=>r.url().endsWith('/chat'));
 await ask(page,'I lost my card');
 expect((await sent).postDataJSON().language).toBe('Chinese');
 await expect(page.locator('.bank-chat-message.assistant')).toContainText('Safety guidance');
 await expect(page.locator('.bank-chat-message.assistant')).toContainText('immediately');
 await expect(page.getByText('AI answers can contain errors',{exact:false})).toContainText('AI answers can contain errors');
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
 await page.route('**/api/knowledge/sections/lost-card/4/main',async route=>{entered();await gate;await route.continue();});
 await page.getByText('Sources',{exact:true}).click();
 await page.getByRole('button',{name:'Lost or stolen card guidance'}).click();await intercepted;
 await page.getByRole('button',{name:'New chat',exact:true}).click();
 const received=page.waitForResponse(r=>r.url().endsWith('/knowledge/sections/lost-card/4/main'));release();
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
test('guests are not offered Talk to a person (it needs a signed-in client and her relationship manager)',async({page})=>{
 await ask(page,'I lost my card');
 await expect(page.getByRole('button',{name:'Talk to a person'})).toHaveCount(0);
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
  await expect(page.getByRole('heading',{name:'Hi, how can I help?'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  const welcome=page.locator('.bank-chat-welcome');
  await welcome.getByRole('tab',{name:'Cards and payments'}).click();
  await welcome.getByRole('button',{name:/minimum on my credit card/}).click();
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
test('a bank guide source opens in place under its row and closes again',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await ask(page,'I lost my card');await page.getByText('Sources',{exact:true}).click();
 const trigger=page.getByRole('button',{name:'Lost or stolen card guidance'});
 await trigger.click();
 const source=page.getByRole('region',{name:'Source details'});
 await expect(source).toBeVisible();await expect(trigger).toHaveAttribute('aria-expanded','true');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
 await page.screenshot({path:'test-results/design-source-mobile.png',fullPage:true});
 await source.getByRole('button',{name:'Close source'}).click();await expect(source).toHaveCount(0);
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
