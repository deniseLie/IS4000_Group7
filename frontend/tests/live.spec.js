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
 await page.getByRole('button',{name:'Close source',exact:true}).click();
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
 expect(answer.answer).toMatch(/unpaid|remaining balance|outstanding balance|balance.*(?:remains|left)|still.*(?:charged|incur|pay).*interest/i);
 expect(answer.answer).not.toMatch(/our (card )?(terms|policy)|avoid interest charges entirely|avoid all interest|full statement balance|pay.*in full/i);
 await page.getByText('Sources',{exact:true}).click();
 await page.getByRole('button',{name:'Singapore credit card payments'}).click();
 const panel=page.getByRole('region',{name:'Source details'});
 await expect(panel).toContainText('Publisher: MoneySense');
 await expect(panel.getByRole('link',{name:'Read the original source'})).toHaveAttribute('href','https://www.moneysense.gov.sg/understanding-credit-cards/');
 await page.screenshot({path:'test-results/real-minimum-payment.png',fullPage:true});
});
test('real AI understands a typo then follows a corrected topic',async({page},testInfo)=>{
 await page.goto('/');
 const typo=await ask(page,'My crad isnt working. What do you need to know?',testInfo);
 expect(typo.answer).toMatch(/ATM|online|shop|error|happen/i);
 const corrected=await ask(page,'Sorry, I meant I need to download my bank statement. How can I do that?',testInfo);
 expect(corrected.sources.map(s=>s.id)).toContain('statements');
 expect(corrected.answer).toMatch(/statement/i);
 expect(corrected.answer).not.toMatch(/what error|ATM withdrawal|shop payment/i);
 await page.screenshot({path:'test-results/real-topic-correction.png',fullPage:true});
});
test('real AI admits unavailable fees and separates external education from bank policy',async({page},testInfo)=>{
 await page.goto('/');
 const fee=await ask(page,'What is the exact annual fee for the DemoBank Platinum card?',testInfo);
 expect(fee.sources.map(s=>s.id)).toContain('cards');
 expect(fee.answer).not.toMatch(/\d/);
 expect(fee.answer).toMatch(/unavailable|cannot be confirmed|not (?:available|provided|supplied)|no .*record|don.t have/i);
 await page.getByRole('button',{name:'New chat',exact:true}).click();
 const policy=await ask(page,"Is the MoneySense credit card guidance DemoBank's own credit card policy?",testInfo);
 expect(policy.sources.map(s=>s.id)).toContain('sg-card-payments');
 expect(policy.answer).toMatch(/MoneySense/);
 expect(policy.answer).toMatch(/\bnot\b|\bno\b/i);
 await page.getByText('Sources',{exact:true}).click();
 await page.getByRole('button',{name:'Singapore credit card payments'}).click();
 await expect(page.getByRole('region',{name:'Source details'})).toContainText('Not a DemoBank product policy');
 await page.screenshot({path:'test-results/real-source-scope.png',fullPage:true});
});
for(const scenario of [
 {language:'Chinese',question:'什么是抵押贷款和无抵押贷款？',script:/[\u3400-\u9fff]/},
 {language:'Malay',question:'Apakah perbezaan pinjaman bercagar dan pinjaman tidak bercagar?',script:/pinjaman|cagaran/i},
 {language:'Tamil',question:'பிணையுள்ள கடனுக்கும் பிணையில்லாத கடனுக்கும் என்ன வித்தியாசம்?',script:/[\u0b80-\u0bff]/},
])test(`real AI language sample: ${scenario.language}`,async({page},testInfo)=>{
 await page.goto('/');await page.getByLabel('Answer language',{exact:true}).selectOption(scenario.language);
 const answer=await ask(page,scenario.question,testInfo);
 expect(answer.sources.map(s=>s.id)).toContain('sg-loan-types');
 expect(answer.answer).toMatch(scenario.script);
 // Script and source assertions are smoke checks. Review the attached full
 // answer for meaning, scope and language before claiming language quality.
 await page.screenshot({path:`test-results/real-language-${scenario.language}.png`,fullPage:true});
});
test('real unlinked synthetic login cannot access banking records',async({page})=>{
 test.skip(!process.env.TEST_UNLINKED_FIXTURE_FILE,'Requires a temporary unlinked synthetic test login');
 const [user]=JSON.parse(fs.readFileSync(process.env.TEST_UNLINKED_FIXTURE_FILE,'utf8'));
 await page.goto('/');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.getByLabel('Email address').fill(user.email);await page.getByLabel('Password',{exact:true}).fill(user.password);await page.getByTestId('submit-login').click();
 await expect(page.getByRole('button',{name:'Sign out'})).toBeVisible({timeout:20000});
 await page.getByRole('textbox',{name:'Your message'}).fill('What is my balance?');await page.getByRole('button',{name:'Send',exact:true}).click();
 await expect(page.locator('.bank-chat-bubble.error')).toContainText('No demo customer profile',{timeout:30000});
 await page.getByRole('radiogroup',{name:'Information'}).getByRole('radio',{name:'General help'}).click();
 await page.getByRole('textbox',{name:'Your message'}).fill('I lost my card');await page.getByRole('button',{name:'Send',exact:true}).click();
 await expect(page.locator('.bank-chat-message.assistant')).toContainText('immediately',{timeout:30000});
 await page.getByRole('button',{name:'Sign out'}).click();await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeVisible();
 await expect(page.locator('.bank-chat-message')).toHaveCount(0);
});
