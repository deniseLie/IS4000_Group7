import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';

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
 const documentsLoaded=page.waitForResponse(response=>response.url().endsWith('/customer/documents'));
 await page.getByTestId('open-records').click();expect((await loaded).status()).toBe(200);
 const documentsResponse=await documentsLoaded;expect(documentsResponse.status()).toBe(200);
 const documentRows=(await documentsResponse.json()).data;
 await expect(page.getByTestId('records-document')).toHaveCount(documentRows.length);
 expect(await page.getByTestId('records-document').evaluateAll(nodes=>nodes.map(node=>node.dataset.documentId).sort()))
  .toEqual(documentRows.map(row=>row.document_id).sort());
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

test('hosted worker delivery reaches the open inbox through Realtime',async({page,context})=>{
 test.skip(!fixture||process.env.TEST_HOSTED_WORKER!=='1','Requires existing synthetic logins and the restricted hosted worker');
 await signIn(page,'demo.david@example.com');await page.getByTestId('open-notifications').click();
 await expect(page.getByTestId('inbox-live-status')).toHaveText('Live inbox updates connected.',{timeout:30000});
 const before=await page.locator('[data-notification-id]').evaluateAll(nodes=>nodes.map(node=>node.dataset.notificationId));
 const editor=await context.newPage();
 try {
  await editor.goto('/');await editor.getByTestId('open-data-sharing').click();
  await editor.getByTestId('open-agenda-drafts').click();await editor.getByTestId('new-agenda').click();
  await editor.getByTestId('agenda-topics').fill('Hosted synthetic worker and Realtime delivery integration check');
  const recorded=editor.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
  await editor.getByTestId('save-agenda').click();const saved=await recorded;expect(saved.status()).toBe(200);
  expect((await saved.json()).receipt.outcome).toBe('succeeded');
  await expect.poll(async()=>page.locator('[data-notification-id][data-template-key="agenda.saved"]').evaluateAll(
    (nodes,previous)=>nodes.filter(node=>!previous.includes(node.dataset.notificationId)).map(node=>node.dataset.notificationId),before),
    {timeout:30000}).toHaveLength(1);
  const current=await page.locator('[data-notification-id][data-template-key="agenda.saved"]').evaluateAll(
    (nodes,previous)=>nodes.filter(node=>!previous.includes(node.dataset.notificationId)).map(node=>node.dataset.notificationId),before);
  const notification=page.locator(`[data-notification-id="${current[0]}"]`);
  const read=page.waitForResponse(response=>response.url().endsWith(`/notifications/${current[0]}/read`));
  await notification.getByTestId('mark-notification-read').click();expect((await read).status()).toBe(200);
  await expect(notification.getByTestId('notification-read-state')).toHaveText('Read');
  await page.getByTestId('reload-notifications').click();
  await expect(notification.getByTestId('notification-read-state')).toHaveText('Read');
 } finally {await editor.close();}
});

test('hosted checklist preparation persists without changing bank review status',async({page})=>{
 test.skip(!fixture,'Requires existing synthetic persona logins in a git-ignored fixture');
 await signIn(page,'demo.michelle@example.com');await page.getByTestId('open-records').click();
 const item=page.locator('[data-checklist-item-id="00000000-0000-4000-8000-100200000011"]');
 const previous=await item.getByTestId('checklist-mark').inputValue(),next=previous==='prepared'?'not_prepared':'prepared';
 const status=await item.textContent();
 await item.getByTestId('checklist-mark').selectOption(next);
 const submitted=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
 await item.getByTestId('save-checklist-mark').click();const response=await submitted;expect(response.status()).toBe(200);
 const saved=await response.json();expect(saved.receipt.outcome).toBe('succeeded');
 await expect(item.getByTestId('checklist-outcome')).toContainText(saved.receipt.receipt_id);
 await page.getByTestId('reload-records').click();await expect(item.getByTestId('checklist-mark')).toHaveValue(next);
 expect(status).toContain(saved.receipt.result.submission_status.replaceAll('_',' '));
 await item.getByTestId('checklist-mark').selectOption(previous);
 const restored=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
 await item.getByTestId('save-checklist-mark').click();const restore=await restored;expect(restore.status()).toBe(200);
 const result=await restore.json();expect(result.receipt.result.client_mark).toBe(previous);
 expect(result.receipt.result.submission_status).toBe(saved.receipt.result.submission_status);
 await page.getByTestId('reload-records').click();await expect(item.getByTestId('checklist-mark')).toHaveValue(previous);
 await page.keyboard.press('Escape');await page.getByTestId('open-actions').click();
 await expect(page.locator(`[data-command-id="${saved.command.command_id}"]`).getByTestId('command-receipt')).toContainText(saved.receipt.receipt_id);
 await page.keyboard.press('Escape');await page.getByTestId('sign-out').click();
 await expect(page.getByTestId('sign-in')).toBeVisible({timeout:30000});
});

test('hosted relationship intake persists only after reviewed confirmation',async({page})=>{
 test.skip(!fixture,'Requires existing synthetic persona logins in a git-ignored fixture');
 await signIn(page,'demo.david@example.com');await page.getByTestId('open-data-sharing').click();
 await page.getByTestId('open-relationship-details').click();
 await page.getByTestId('request-details').fill('Hosted synthetic relationship request integration check');
 await page.getByTestId('request-country').fill('SG');await page.getByTestId('request-currency').fill('USD');
 const proposed=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
 await page.getByTestId('prepare-request').click();const proposal=await proposed;expect(proposal.status()).toBe(200);
 const saved=await proposal.json();expect(saved.receipt).toBeNull();
 await page.getByTestId('review-request-actions').click();const result=await confirmRecorded(page,saved.command.command_id);
 expect(result.receipt.result.status).toBe('sent');expect(result.receipt.result.what).toBe(saved.command.target.what);
 await page.keyboard.press('Escape');await page.getByTestId('open-data-sharing').click();await page.getByTestId('open-relationship-details').click();
 const request=page.locator(`[data-request-id="${result.receipt.result.request_id}"]`);
 await expect(request).toContainText(saved.command.target.what);await expect(request).toContainText('Currency reference: USD');
 await page.keyboard.press('Escape');await page.getByTestId('sign-out').click();
 await expect(page.getByTestId('sign-in')).toBeVisible({timeout:30000});
});

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

test('hosted technical inspections read existing private fixture bytes without changing review status',async({page,browser})=>{
 test.skip(!fixture||process.env.TEST_HOSTED_INSPECTION!=='1','Requires existing synthetic fixture uploads in the approved hosted project');
 test.setTimeout(180000);
 const writes=[];page.on('request',request=>{
  if(request.method()==='POST'&&/\/(chat|commands|documents\/uploads\/prepare)$/.test(new URL(request.url()).pathname))writes.push(request.url());
 });
 await signIn(page,'demo.michelle@example.com');
 const loaded=page.waitForResponse(response=>response.url().endsWith('/customer/documents'));
 await page.getByTestId('open-records').click();const response=await loaded;expect(response.status()).toBe(200);
 const records=(await response.json()).data;
 await expect(page.getByTestId('document-upload')).toBeVisible({timeout:30000});
 const inspected=[];
 for(const extension of ['pdf','docx','png','jpg']) {
  const record=records.find(row=>row.file_name===`Synthetic DEMO.${extension}`);
  expect(record).toBeDefined();expect(record.precheck_status).toBe('not_checked');
  const row=page.locator(`[data-document-id="${record.document_id}"]`);
  const inspectedResponse=page.waitForResponse(reply=>reply.url().endsWith(`/documents/${record.document_id}/inspection`));
  await row.getByTestId('document-inspect').click();const reply=await inspectedResponse;expect(reply.status()).toBe(200);
  const result=(await reply.json()).data;
  const bytes=fs.readFileSync(new URL(`./fixtures/documents/Synthetic DEMO.${extension}`,import.meta.url));
  expect(result.sha256).toBe(createHash('sha256').update(bytes).digest('hex'));expect(result.size_bytes).toBe(bytes.length);
  expect(result.document_id).toBe(record.document_id);expect(result.scope).toBe('technical_only');
  expect(result.ocr).toBe('not_performed');expect(result.bank_rules).toBe('not_configured');
  for(const privateField of ['text','contents','storage_path','object_id'])expect(Object.hasOwn(result,privateField)).toBe(false);
  const status=['pdf','docx'].includes(extension)?'text_extracted':'image_decoded';
  expect(result.status).toBe(status);expect(result.page_count).toBe(extension==='pdf'?1:null);
  if(status==='image_decoded')expect([result.width,result.height,result.extracted_characters,result.issue]).toEqual([32,32,0,'ocr_unavailable']);
  else expect(result.extracted_characters).toBeGreaterThan(0);
  await expect(row.getByTestId('document-inspection-result')).toHaveAttribute('data-inspection-status',status);
  await expect(row.getByTestId('document-precheck-status')).toHaveText('not checked');inspected.push(record.document_id);
 }
 await page.setViewportSize({width:320,height:900});
 expect(await page.getByTestId('records-content').locator('xpath=ancestor::dialog').evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
 const reloaded=page.waitForResponse(reply=>reply.url().endsWith('/customer/documents'));
 await page.getByTestId('reload-records').click();
 expect((await (await reloaded).json()).data.filter(row=>inspected.includes(row.document_id))).toEqual(records.filter(row=>inspected.includes(row.document_id)));
 await expect(page.getByTestId('document-inspection-result')).toHaveCount(0);expect(writes).toEqual([]);
 for(const email of ['demo.alex@example.com','demo.david@example.com']) {
  const foreign=await browser.newContext();
  try {
   const visitor=await foreign.newPage();await signIn(visitor,email);
   const owned=visitor.waitForResponse(reply=>reply.url().endsWith('/customer/documents'));
   await visitor.getByTestId('open-records').click();const own=await owned;expect(own.status()).toBe(200);
   for(const documentId of inspected) {
    await expect(visitor.locator(`[data-document-id="${documentId}"]`)).toHaveCount(0);
    const denied=await foreign.request.get(own.url().replace('/customer/documents',`/documents/${documentId}/inspection`),{
     headers:{Authorization:own.request().headers().authorization}});
    expect(denied.status()).toBe(404);
   }
  } finally {await foreign.close();}
 }
});

test('hosted private documents upload, download, isolate and notify through real Storage',async({page,context,browser})=>{
 test.skip(!fixture||process.env.TEST_HOSTED_DOCUMENTS!=='1','Requires approval for the synthetic file fixtures and hosted private bucket');
 test.setTimeout(300000);
 await signIn(page,'demo.michelle@example.com');await page.getByTestId('open-records').click();
 await expect(page.getByTestId('document-upload')).toBeVisible({timeout:30000});
 const saved=[];let storageHeaders,storageOrigin;
 for(const extension of ['pdf','docx','jpg','png']) {
  const file=new URL(`./fixtures/documents/Synthetic DEMO.${extension}`,import.meta.url);
  await page.getByTestId('document-upload-file').setInputFiles(fileURLToPath(file));
  const prepared=page.waitForResponse(response=>response.url().endsWith('/documents/uploads/prepare'));
  const stored=page.waitForResponse(response=>response.url().includes('/storage/v1/object/demobank-compliance-private/')&&response.request().method()==='POST');
  const recorded=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
  await page.getByTestId('document-upload-submit').click();
  const preparation=await prepared;expect(preparation.status()).toBe(200);
  const upload=await stored;expect(upload.status()).toBe(200);
  expect(upload.request().headers()['x-upsert']).toBe('false');
  storageHeaders=upload.request().headers();storageOrigin=new URL(upload.url()).origin;
  expect(storageOrigin).toBe('https://ypztnwvadxmjaveewubm.supabase.co');
  const response=await recorded;expect(response.status()).toBe(200);
  const result=await response.json();expect(result.receipt?.outcome).toBe('succeeded');
  expect(result.command.target.storage_path).toBe((await preparation.json()).data.storage_path);
  const replay=await context.request.post(response.url(),{headers:{Authorization:response.request().headers().authorization},data:response.request().postDataJSON()});
  expect(replay.status()).toBe(200);expect(await replay.json()).toEqual(result);
  await expect(page.getByTestId('document-upload-outcome')).toContainText(result.receipt.receipt_id);
  await page.getByTestId('reload-records').click();
  const row=page.locator(`[data-document-id="${result.command.target.document_id}"]`);
  await expect(row).toContainText(`Synthetic DEMO.${extension}`);
  await expect(row.getByTestId('document-precheck-status')).toHaveText('not checked');
  const downloaded=page.waitForEvent('download');await row.getByTestId('document-download').click();
  const download=await downloaded;expect(download.suggestedFilename()).toBe(`Synthetic DEMO.${extension}`);
  const path=await download.path();expect(fs.readFileSync(path)).toEqual(fs.readFileSync(file));
  const publicRead=await context.request.get(`${storageOrigin}/storage/v1/object/public/demobank-compliance-private/${result.command.target.storage_path}`);
  expect(publicRead.ok()).toBe(false);
  const overwritten=await context.request.put(`${storageOrigin}/storage/v1/object/demobank-compliance-private/${result.command.target.storage_path}`,{
   headers:{Authorization:storageHeaders.authorization,apikey:storageHeaders.apikey,'Content-Type':result.command.target.mime_type},data:fs.readFileSync(file)});
  expect(overwritten.ok()).toBe(false);
  saved.push(result);
 }
 await page.setViewportSize({width:320,height:900});
 const dialog=page.getByTestId('records-content').locator('xpath=ancestor::dialog');
 expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
 await page.keyboard.press('Escape');await page.getByTestId('open-actions').click();
 for(const result of saved)await expect(page.locator(`[data-command-id="${result.command.command_id}"]`).getByTestId('command-receipt')).toContainText(result.receipt.receipt_id);
 await page.keyboard.press('Escape');await page.getByTestId('open-notifications').click();
 await expect.poll(async()=>page.locator('[data-template-key="document.uploaded"]').count(),{timeout:45000}).toBeGreaterThanOrEqual(4);
 await page.keyboard.press('Escape');
 const foreign=await browser.newContext();
 try {
  const visitor=await foreign.newPage();await signIn(visitor,'demo.alex@example.com');
  const owned=visitor.waitForResponse(response=>response.url().endsWith('/customer/documents'));
  await visitor.getByTestId('open-records').click();const loaded=await owned;expect(loaded.status()).toBe(200);
  const auth=loaded.request().headers().authorization;
  for(const result of saved) {
   await expect(visitor.locator(`[data-document-id="${result.command.target.document_id}"]`)).toHaveCount(0);
   const location=await foreign.request.get(loaded.url().replace('/customer/documents',`/documents/${result.command.target.document_id}/location`),{headers:{Authorization:auth}});
   expect(location.status()).toBe(404);
   const signed=await foreign.request.post(`${storageOrigin}/storage/v1/object/sign/demobank-compliance-private/${result.command.target.storage_path}`,{
    headers:{Authorization:auth,apikey:storageHeaders.apikey},data:{expiresIn:60}});
   expect(signed.ok()).toBe(false);
  }
 } finally {await foreign.close();}
 await page.getByTestId('sign-out').click();await expect(page.getByTestId('sign-in')).toBeVisible();
});

test('hosted upload acknowledgement loss can record the same real object without another copy',async({page,context})=>{
 test.skip(!fixture||process.env.TEST_HOSTED_DOCUMENTS!=='1','Requires approval for synthetic hosted upload fault injection');
 test.setTimeout(180000);
 await signIn(page,'demo.michelle@example.com');await page.getByTestId('open-records').click();
 await expect(page.getByTestId('document-upload')).toBeVisible({timeout:30000});
 const pattern='**/storage/v1/object/demobank-compliance-private/**';
 let received=false;
 await page.route(pattern,async route=>{
  expect(route.request().method()).toBe('POST');
  if(received){await route.continue();return;}
  // Forward the actual file bytes, then discard the browser's acknowledgement. Intercepted multipart data is not a reliable file-byte source.
  const headers=route.request().headers();
  const response=await context.request.post(route.request().url(),{headers:{Authorization:headers.authorization,apikey:headers.apikey,'x-upsert':'false'},
   multipart:{cacheControl:'3600','':{name:'Synthetic DEMO.png',mimeType:'image/png',buffer:fs.readFileSync(new URL('./fixtures/documents/Synthetic DEMO.png',import.meta.url))}}});
  expect(response.status(),await response.text()).toBe(200);received=true;
  await route.abort('failed');
 });
 const prepared=page.waitForResponse(response=>response.url().endsWith('/documents/uploads/prepare'));
 await page.getByTestId('document-upload-file').setInputFiles(fileURLToPath(new URL('./fixtures/documents/Synthetic DEMO.png',import.meta.url)));
 await page.getByTestId('document-upload-submit').click();const response=await prepared;expect(response.status()).toBe(200);
 const target=(await response.json()).data;
 await expect(page.getByTestId('document-upload-retry')).toBeVisible({timeout:90000});expect(received).toBe(true);
 await expect(page.getByTestId('document-upload-error')).toBeVisible();
 const recorded=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
 await page.getByTestId('document-upload-retry').click();const command=await recorded;expect(command.status()).toBe(200);
 const saved=await command.json();expect(saved.receipt?.outcome).toBe('succeeded');
 expect(saved.command.target.document_id).toBe(target.document_id);
 await expect(page.getByTestId('document-upload-outcome')).toContainText(saved.receipt.receipt_id);
 await page.getByTestId('reload-records').click();
 const row=page.locator(`[data-document-id="${target.document_id}"]`);await expect(row).toHaveCount(1);
 const downloaded=page.waitForEvent('download');await row.getByTestId('document-download').click();
 const download=await downloaded;expect(fs.readFileSync(await download.path())).toEqual(fs.readFileSync(new URL('./fixtures/documents/Synthetic DEMO.png',import.meta.url)));
 await page.keyboard.press('Escape');await page.getByTestId('sign-out').click();
 await expect(page.getByTestId('sign-in')).toBeVisible();
});
