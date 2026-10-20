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

async function confirmRecorded(page,commandId) {
 const command=page.locator(`[data-command-id="${commandId}"]`);
 await command.getByTestId('review-command').click();
 await command.getByTestId('acknowledge-command').check();
 const recorded=page.waitForResponse(response=>response.url().endsWith(`/commands/${commandId}/confirm`));
 await command.getByTestId('confirm-command').click();const response=await recorded;expect(response.status()).toBe(200);
 const result=await response.json();expect(result.receipt.outcome).toBe('succeeded');return result;
}

test('hosted consent, sharing receipt and revocation keep the reviewed snapshot',async({page})=>{
 test.skip(!fixture,'Requires existing synthetic persona logins in a git-ignored fixture');
 test.setTimeout(240000);
 await signIn(page,'demo.david@example.com');await page.getByTestId('open-data-sharing').click();
 await page.getByTestId('consent-category-agenda').check();
 await page.getByTestId('consent-duration').selectOption('1');
 const request=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
 await page.getByTestId('request-consent').click();const requested=await (await request).json();
 const requestId=requested.receipt.result.request_id;
 const prepareGrant=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
 await page.locator(`[data-consent-request-id="${requestId}"]`).getByTestId('prepare-consent-grant').click();
 const grantProposal=await (await prepareGrant).json();
 await page.getByTestId('review-consent-actions').click();
 const granted=await confirmRecorded(page,grantProposal.command.command_id),grantId=granted.receipt.result.grant_id;
 await page.keyboard.press('Escape');await page.getByTestId('open-data-sharing').click();
 await page.getByTestId('open-agenda-drafts').click();await page.getByTestId('new-agenda').click();
 await page.getByTestId('agenda-topics').fill('Hosted consent and sharing integration check');await page.getByTestId('agenda-field-name').check();
 const save=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
 await page.getByTestId('save-agenda').click();const saved=await (await save).json();
 const draft=page.locator(`[data-agenda-id="${saved.command.target.agenda_id}"]`);
 await draft.getByTestId('prepare-agenda-sharing').click();await page.getByTestId('agenda-sharing-grant').selectOption(grantId);
 const propose=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
 await page.getByTestId('propose-agenda-sharing').click();const proposed=await (await propose).json();
 expect(proposed.receipt).toBeNull();
 await page.getByTestId('review-agenda-sharing').click();const shared=await confirmRecorded(page,proposed.command.command_id);
 expect(shared.receipt.result.content).toBe(saved.command.target.content);
 await page.keyboard.press('Escape');await page.getByTestId('open-data-sharing').click();
 const revoke=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
 await page.locator(`[data-consent-grant-id="${grantId}"]`).getByTestId('prepare-consent-revoke').click();
 const revocation=await (await revoke).json();await page.getByTestId('review-consent-actions').click();
 await confirmRecorded(page,revocation.command.command_id);
 const original=page.locator(`[data-command-id="${proposed.command.command_id}"]`);
 await expect(original.getByTestId('command-receipt')).toContainText(shared.receipt.receipt_id);
 await expect(original.getByTestId('command-agenda-sharing')).toContainText(saved.command.target.content);
 await page.keyboard.press('Escape');await page.getByTestId('sign-out').click();
 await expect(page.getByTestId('sign-in')).toBeVisible({timeout:30000});
});
