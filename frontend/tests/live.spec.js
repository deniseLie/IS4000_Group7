import {test,expect} from '@playwright/test';
import fs from 'node:fs';
async function ask(page,question,testInfo) {
 const received=page.waitForResponse(r=>r.url().endsWith('/chat')&&r.request().method()==='POST');
 await page.getByRole('textbox',{name:'Your message'}).fill(question);
 await page.getByRole('button',{name:'Send',exact:true}).click();
 const response=await received;
 expect(response.status()).toBe(200);
 await expect(page.getByRole('button',{name:'Send',exact:true})).toBeVisible({timeout:80000});
 const bubble=page.locator('.bank-chat-message.assistant').last();
 await expect(bubble).toHaveAttribute('data-response-origin','ai');
 await expect(bubble).toHaveAttribute('data-prompt-version',/^v2\+/);
 await expect(bubble).toContainText('AI-generated');
 const result={answer:await bubble.locator('.answer-text').innerText(),
   sources:await bubble.locator('[data-source-id]').evaluateAll(nodes=>nodes.map(n=>({id:n.dataset.sourceId}))),
   prompt_version:await bubble.getAttribute('data-prompt-version'),response_origin:await bubble.getAttribute('data-response-origin')};
 await testInfo.attach('real-answer',{body:JSON.stringify({question,...result},null,2),contentType:'application/json'});
 return result;
}
test('real AI handles a banking question then abuse without repeating the article',async({page},testInfo)=>{
 await page.goto('/');
 const card=await ask(page,'My card is not working.',testInfo);
 expect(card.answer).toMatch(/ATM|online|shop|error|happen/i);
 expect(card.answer).not.toMatch(/Use the matching|ask whether|Do not invent/);
 const insult=await ask(page,'fuck you',testInfo);
 expect(insult.sources).toEqual([]);
 expect(insult.answer).not.toMatch(/Use the matching|credit card product record|could not verify/i);
 expect(insult.answer).not.toBe(card.answer);
 expect(insult.answer).not.toMatch(/ATM|online payment|shop payment|product record/i);
 const thanks=await ask(page,'Sorry about that. Thanks for helping.',testInfo);
 expect(thanks.sources).toEqual([]);
 await page.screenshot({path:'test-results/real-conversation.png',fullPage:true});
});
test('real AI gives sourced banking guidance and understands a short follow-up',async({page},testInfo)=>{
 await page.goto('/');
 const first=await ask(page,'What happens if I withdraw my fixed deposit before maturity?',testInfo);
 expect(first.sources.map(s=>s.id)).toContain('sg-accounts');
 expect(first.answer).toMatch(/interest/i);
 await page.getByText('Sources',{exact:true}).click();
 await page.getByRole('button',{name:'Singapore bank account basics'}).click();
 const panel=page.getByRole('region',{name:'Source details'});
 await expect(panel).toContainText('Publisher: MoneySense');
 await expect(panel.getByRole('link',{name:'Read the original source'})).toHaveAttribute('href','https://www.moneysense.gov.sg/understanding-bank-accounts/');
 const follow=await ask(page,'Explain that in one short sentence using very simple English.',testInfo);
 expect(follow.sources.map(s=>s.id)).toContain('sg-accounts');
 expect(follow.answer).toMatch(/interest/i);
 expect(follow.answer.length).toBeLessThan(first.answer.length);
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
 await page.screenshot({path:'test-results/real-banking-mobile.png',fullPage:true});
});
test('real AI handles a greeting and off-topic request in a fresh chat',async({page},testInfo)=>{
 await page.goto('/');
 const greeting=await ask(page,'Hello there!',testInfo);
 expect(greeting.sources).toEqual([]);
 const off=await ask(page,'Can you write a cake recipe?',testInfo);
 expect(off.sources).toEqual([]);
 expect(off.answer).toMatch(/bank|financial/i);
 expect(off.answer).not.toMatch(/could not verify|Use the matching/i);
 await page.getByRole('button',{name:'New chat',exact:true}).click();
 await expect(page.locator('.bank-chat-message')).toHaveCount(0);
});
test('frustration with a banking question still gets sourced help',async({page},testInfo)=>{
 await page.goto('/');
 const answer=await ask(page,'This is so annoying. Does paying only the minimum on my credit card avoid interest?',testInfo);
 expect(answer.sources.map(s=>s.id)).toContain('sg-card-payments');
 expect(answer.answer).toMatch(/interest/i);
 expect(answer.answer).not.toMatch(/our (card )?(terms|policy)|avoid interest charges entirely|avoid all interest|full statement balance|pay.*in full/i);
});
test('real unlinked synthetic login cannot access banking records',async({page})=>{
 test.skip(!process.env.TEST_UNLINKED_FIXTURE_FILE,'Requires a temporary unlinked synthetic test login');
 const [user]=JSON.parse(fs.readFileSync(process.env.TEST_UNLINKED_FIXTURE_FILE,'utf8'));
 await page.goto('/');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.getByLabel('Email address').fill(user.email);await page.getByLabel('Password',{exact:true}).fill(user.password);await page.getByRole('button',{name:'Sign In →',exact:true}).click();
 await expect(page.getByRole('button',{name:'Sign out'})).toBeVisible({timeout:20000});
 await page.getByRole('textbox',{name:'Your message'}).fill('What is my balance?');await page.getByRole('button',{name:'Send',exact:true}).click();
 await expect(page.locator('.bank-chat-bubble.error')).toContainText('No demo customer profile',{timeout:30000});
 await page.getByLabel('Information',{exact:true}).selectOption('public');
 await page.getByRole('textbox',{name:'Your message'}).fill('I lost my card');await page.getByRole('button',{name:'Send',exact:true}).click();
 await expect(page.locator('.bank-chat-message.assistant')).toContainText('immediately',{timeout:30000});
 await page.getByRole('button',{name:'Sign out'}).click();await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeVisible();
 await expect(page.locator('.bank-chat-message')).toHaveCount(0);
});
