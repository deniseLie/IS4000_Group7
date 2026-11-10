import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import {seedSession} from './nativeSession.js';

test('michelle corrects and submits documents',async({page})=>{
 test.setTimeout(540000);
 page.on('pageerror',error=>console.error('Michelle browser error:',error.stack));
 const fixture=JSON.parse(fs.readFileSync(process.env.NATIVE_BROWSER_FIXTURE,'utf8'));
 const network=[];page.on('response',response=>{const url=new URL(response.url());if(url.pathname.startsWith('/api/'))network.push({method:response.request().method(),path:url.pathname,status:response.status()});});
 await seedSession(page);await page.goto('/');
 await expect(page.getByTestId('open-account-menu')).toBeVisible();
 if(fixture.live){
  await page.locator('#chat-composer').fill('I got a letter asking me to update my information. What do I need to send?');
  await page.getByTestId('send-message').click();
  await expect(page.locator('section.answer-section[data-domain="compliance"]')).toBeVisible({timeout:170000});
  await expect(page.getByTestId('chat-message-assistant').last()).toHaveAttribute('data-response-origin','ai',{timeout:170000});
 }else await page.getByTestId('open-compliance').click();
 await page.getByTestId('open-compliance').click();
 await expect(page.getByTestId('compliance-page')).toBeVisible();
 const workspace=page.getByTestId('compliance-workspace');
 await expect(workspace).toBeVisible();
 await expect(workspace.getByTestId('compliance-item')).toHaveCount(4);
 await expect(workspace.getByTestId('compliance-next-step')).toBeVisible();await expect(workspace).toContainText('With the bank');
 let item=workspace.locator('[data-item-id="00000000-0000-4000-8000-100200000011"]');
 await item.getByTestId('document-upload-file').setInputFiles({name:'Synthetic DEMO.txt',mimeType:'text/plain',buffer:Buffer.from('Synthetic demo')});
 await item.getByTestId('document-upload-submit').click();
 await expect(item.getByTestId('document-upload-error')).toContainText('PDF, DOCX, JPEG or PNG');
 await item.getByTestId('checklist-toggle').click();
 await item.getByTestId('checklist-mark').selectOption('prepared');
 await item.getByTestId('save-checklist-mark').click();
 await expect(item.getByTestId('compliance-item-status')).toHaveText('Ready to send');
 await item.getByTestId('document-upload-file').setInputFiles(fixture.missing_file);
 await item.getByTestId('document-upload-submit').click();
 await expect(item.getByTestId('precheck-result')).toHaveAttribute('data-precheck-status','issues_found',{timeout:170000});
 await expect(item.getByTestId('precheck-result')).toContainText(/address/i);
 await expect(item.getByTestId('compliance-item-status')).toHaveText('Ready to send');
 await expect(item.getByTestId('document-upload-file')).toBeEnabled({timeout:170000});
 if(fixture.live){
  const selected=page.waitForRequest(request=>request.url().endsWith('/chat')&&request.method()==='POST');
  const before=await page.getByTestId('chat-message-assistant').count();
  await item.getByTestId('explain-document-check').click();
  expect((await selected).postDataJSON().record_context.kind).toBe('document');
  await expect(page.getByTestId('chat-message-assistant')).toHaveCount(before+1,{timeout:170000});
  await expect(page.getByTestId('chat-message-assistant').last()).toHaveAttribute('data-response-origin','ai',{timeout:170000});
  await expect(page.getByTestId('chat-message-assistant').last()).toContainText(/preliminary|template/i);
  await expect(page.locator('#chat-composer')).toBeEnabled({timeout:170000});
 }
 await item.getByTestId('document-upload-file').setInputFiles(fixture.valid_file);
 await item.getByTestId('document-upload-submit').click();
 await expect(item.getByTestId('item-sent')).toBeVisible({timeout:170000});
 await expect(item.getByTestId('compliance-item-status')).toHaveText('With the bank – awaiting review');
 await expect(workspace.getByTestId('submission-receipt')).toBeVisible();
 await expect(workspace).not.toContainText('Accepted by bank');
 await page.getByTestId('open-notifications').click();
 await expect(page.locator('[data-template-key="document.submitted"]')).toContainText('not approval',{timeout:10000});
 await page.getByTestId('open-chat').click();
 if(fixture.live){
  await expect(page.locator('#chat-composer')).toBeEnabled({timeout:170000});
  const beforeApproval=await page.getByTestId('chat-message-assistant').count();
  await page.locator('#chat-composer').fill('Can you just approve my compliance review for me?');await page.getByTestId('send-message').click();
  await expect(page.getByTestId('chat-message-assistant')).toHaveCount(beforeApproval+1,{timeout:170000});
  await expect(page.getByTestId('chat-message-assistant').last()).toHaveAttribute('data-response-origin','ai',{timeout:170000});
  await expect(page.getByTestId('chat-message-assistant').last()).not.toContainText(/I have approved|your review is approved/i);
  await expect(page.locator('#chat-composer')).toBeEnabled({timeout:170000});
  const selected=page.waitForRequest(request=>request.url().endsWith('/chat')&&request.method()==='POST');
  const beforeFollowup=await page.getByTestId('chat-message-assistant').count();
  await workspace.getByTestId('explain-bank-request').first().click();
  expect((await selected).postDataJSON().record_context.kind).toBe('information_request');
  await expect(page.getByTestId('chat-message-assistant')).toHaveCount(beforeFollowup+1,{timeout:170000});
  await expect(page.getByTestId('chat-message-assistant').last()).toHaveAttribute('data-response-origin','ai',{timeout:170000});
  await expect(page.locator('#chat-composer')).toBeEnabled({timeout:170000});
  await item.getByTestId('compliance-support').click();
  await expect(page.getByTestId('handoff-summary')).toContainText('submitted_awaiting_review');
  await page.getByTestId('approve-handoff').click();
  await expect(page.getByTestId('handoff-receipt')).toBeVisible();
  await page.getByTestId('handoff-open-requests').click();
  await page.getByTestId('open-chat').click();
 }
 await expect(page.locator('#chat-composer')).toBeEnabled({timeout:170000});
 await page.reload();
 await page.getByTestId('open-compliance').click();
 await expect(workspace.locator('[data-item-id="00000000-0000-4000-8000-100200000011"]').getByTestId('compliance-item-status')).toHaveText('With the bank – awaiting review');
 for(const width of [1280,390]){
  await page.setViewportSize({width,height:900});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/compliance-submitted-${width}.png`,fullPage:true});
 }
 await page.getByTestId(page.viewportSize().width<=760?'tab-chat':'open-chat').click();
 await page.getByTestId(page.viewportSize().width<=760?'tab-compliance':'open-compliance').click();await expect(workspace).toBeVisible();
 fs.writeFileSync(`${process.env.NATIVE_SCREENSHOT_DIR}/network.json`,JSON.stringify(network,null,2));
});

test('rechecks expired Michelle upload before a new submission',async({page})=>{
 test.setTimeout(240000);
 await seedSession(page);await page.goto('/');
 await page.getByTestId('open-compliance').click();
 const workspace=page.getByTestId('compliance-workspace');
 // Historical findings remain visible, explicitly expired for new submissions.
 await expect(workspace.getByTestId('precheck-result')).toContainText('check it again before sending');
 await workspace.getByTestId('check-current-upload').click();
 await expect(workspace.getByTestId('item-sent')).toBeVisible({timeout:170000});
 await expect(workspace.getByTestId('submission-receipt')).toBeVisible();
 await expect(workspace.getByTestId('compliance-item-status')).toHaveText('With the bank – awaiting review');
 await page.reload();await page.getByTestId('open-compliance').click();
 await expect(workspace.getByTestId('compliance-item-status')).toHaveText('With the bank – awaiting review');
});
