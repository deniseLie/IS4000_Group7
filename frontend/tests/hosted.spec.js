import {test,expect} from '@playwright/test';
import fs from 'node:fs';

const fixture=process.env.TEST_PERSONA_FIXTURE_FILE;
const personas=fixture?JSON.parse(fs.readFileSync(fixture,'utf8')):[];
const expected=[['demo.alex@example.com','Alex Tan (DEMO)',4],
 ['demo.michelle@example.com','Michelle Ng (DEMO)',1],['demo.david@example.com','David Lee (DEMO)',0]];

async function signIn(page,email) {
 const user=personas.find(item=>item.email===email);
 if(!user||typeof user.password!=='string')throw new Error(`Missing authorised persona login for ${email}`);
 await page.goto('/');
 await page.getByTestId('sign-in').click();
 await page.locator('#email').fill(user.email);await page.locator('#password').fill(user.password);
 await page.getByTestId('submit-login').click();
 await expect(page.getByTestId('sign-out')).toBeVisible({timeout:30000});
 await expect(page.getByTestId('send-message')).toBeDisabled();
 await expect(page.getByTestId('open-saved-chats')).toBeVisible();
}

for(const [email,name,count] of expected)test(`hosted login, records and saved safety reply: ${name}`,async({page})=>{
 test.skip(!fixture,'Requires existing synthetic persona logins in a git-ignored fixture');
 await signIn(page,email);
 const loaded=page.waitForResponse(response=>response.url().endsWith('/customer/transactions'));
 await page.getByTestId('open-records').click();expect((await loaded).status()).toBe(200);
 await expect(page.getByTestId('records-name')).toHaveText(name);
 await expect(page.getByTestId('records-account')).toHaveCount(1);
 await expect(page.getByTestId('records-transaction')).toHaveCount(count);
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.keyboard.press('Escape');
 await page.getByTestId('new-chat').click();
 await page.locator('#chat-composer').fill('I lost my card');
 const answered=page.waitForResponse(response=>response.url().endsWith('/chat'));
 await page.getByTestId('send-message').click();expect((await answered).status()).toBe(200);
 await expect(page.getByTestId('chat-message-assistant')).toHaveAttribute('data-response-origin','safety',{timeout:30000});
 await page.reload();
 await expect(page.getByTestId('chat-message-user')).toHaveText('I lost my card',{timeout:30000});
 await expect(page.getByTestId('chat-message-assistant')).toHaveAttribute('data-response-origin','safety');
 await page.getByTestId('sign-out').click();await expect(page.getByTestId('sign-in')).toBeVisible({timeout:30000});
 await expect(page.getByTestId('chat-message-user')).toHaveCount(0);
});

test('hosted private agenda is persisted with a receipt and a new revision',async({page})=>{
 test.skip(!fixture,'Requires existing synthetic persona logins in a git-ignored fixture');
 await signIn(page,'demo.david@example.com');
 await page.getByTestId('open-data-sharing').click();
 await expect(page.getByTestId('sharing-recipient')).toBeVisible({timeout:30000});
 await page.getByTestId('open-agenda-drafts').click();
 await page.getByTestId('new-agenda').click();
 await page.getByTestId('agenda-topics').fill('Hosted integration check: discuss savings goals');
 const saved=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
 await page.getByTestId('save-agenda').click();const response=await saved;expect(response.status()).toBe(200);
 const action=await response.json();expect(action.receipt.outcome).toBe('succeeded');
 await expect(page.getByTestId('agenda-save-result')).toContainText(action.receipt.receipt_id);
 await page.getByTestId('reload-agendas').click();
 const item=page.locator(`[data-agenda-id="${action.command.target.agenda_id}"]`);
 await expect(item.getByTestId('agenda-revision')).toHaveText('1');
 await expect(item.getByTestId('agenda-content')).toHaveText(action.command.target.content);
 await page.keyboard.press('Escape');await page.getByTestId('sign-out').click();
 await expect(page.getByTestId('sign-in')).toBeVisible({timeout:30000});
});

test('hosted knowledge reaches the real model and opens an immutable source',async({page})=>{
 test.skip(process.env.TEST_HOSTED_AI!=='1','Opt in to a real configured provider call');
 await page.goto('/');
 await page.locator('#chat-composer').fill('What happens if I withdraw my fixed deposit before maturity?');
 await page.getByTestId('send-message').click();
 const reply=page.getByTestId('chat-message-assistant');
 await expect(reply).toHaveAttribute('data-response-origin','ai',{timeout:120000});
 await reply.getByTestId('answer-sources').locator('summary').click();
 await expect(reply.locator('[data-source-id="kb:sg-accounts@v1#main"]')).toBeVisible();
 await reply.locator('[data-source-id="kb:sg-accounts@v1#main"]').click();
 const panel=page.locator('dialog[aria-label="Source details"]');
 await expect(panel).toBeVisible();
 await expect(panel).toContainText('MoneySense');
});
