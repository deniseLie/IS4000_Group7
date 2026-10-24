import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import {seedSession} from './nativeSession.js';

test('michelle corrects and submits documents',async({page})=>{
 test.setTimeout(540000);
 page.on('pageerror',error=>console.error('Michelle browser error:',error.stack));
 const fixture=JSON.parse(fs.readFileSync(process.env.NATIVE_BROWSER_FIXTURE,'utf8'));
 await seedSession(page);await page.goto('/');
 await expect(page.getByTestId('sign-out')).toBeVisible();
 if(fixture.live){
  await page.locator('#chat-composer').fill('I got a letter asking me to update my information. What do I need to send?');
  await page.getByTestId('send-message').click();
  await expect(page.locator('section.answer-section[data-domain="compliance"]')).toBeVisible({timeout:170000});
  await expect(page.getByTestId('chat-message-assistant').last()).toHaveAttribute('data-response-origin','ai');
 }else await page.getByTestId('open-compliance').click();
 const workspace=page.getByTestId('compliance-workspace');
 await expect(workspace.getByRole('heading',{name:'Documents to upload',exact:true})).toBeVisible();
 await expect(workspace.getByTestId('compliance-item')).toHaveCount(4);
 await expect(workspace).toContainText('Your action');await expect(workspace).toContainText('With the bank');
 await expect(workspace).toContainText('2026-10-31');
 let item=workspace.locator('[data-item-id="00000000-0000-4000-8000-100200000011"]');
 await item.getByTestId('document-upload-file').setInputFiles({name:'Synthetic DEMO.txt',mimeType:'text/plain',buffer:Buffer.from('Synthetic demo')});
 await item.getByTestId('document-upload-submit').click();
 await expect(item.getByTestId('document-upload-error')).toContainText('PDF, DOCX, JPEG or PNG');
 await item.getByTestId('checklist-mark').selectOption('prepared');
 await item.getByTestId('save-checklist-mark').click();
 await expect(item.getByTestId('compliance-item-status')).toHaveText('Prepared');
 await item.getByTestId('document-upload-file').setInputFiles(fixture.missing_file);
 await item.getByTestId('document-upload-submit').click();
 await expect(item.getByTestId('precheck-result')).toHaveAttribute('data-precheck-status','issues_found',{timeout:170000});
 await expect(item.getByTestId('precheck-result')).toContainText(/address/i);
 await expect(item.getByTestId('compliance-item-status')).toHaveText('Prepared');
 await expect(item.getByTestId('document-upload-file')).toBeEnabled({timeout:170000});
 await expect(page.getByTestId('chat-message-assistant').last()).toHaveAttribute('data-response-origin','ai',{timeout:170000});
 await item.getByTestId('document-upload-file').setInputFiles(fixture.valid_file);
 await item.getByTestId('document-upload-submit').click();
 await expect(item.getByTestId('precheck-result')).toHaveAttribute('data-precheck-status','no_issues',{timeout:170000});
 await expect(item.getByTestId('compliance-item-status')).toHaveText('Submitted – awaiting review');
 await expect(workspace.getByTestId('submission-receipt')).toBeVisible();
 await expect(workspace).not.toContainText('Accepted by bank');
 await page.getByTestId('open-notifications').click();
 await expect(page.locator('[data-template-key="document.submitted"]')).toContainText('not approval',{timeout:10000});
 await page.getByRole('button',{name:'Close notifications',exact:true}).click();
 if(fixture.live){
  await expect(page.locator('#chat-composer')).toBeEnabled({timeout:170000});
  const beforeExplanation=await page.getByTestId('chat-message-assistant').count();
  await item.getByRole('button',{name:'Explain findings in chat'}).click();
  await expect(page.getByTestId('chat-message-assistant')).toHaveCount(beforeExplanation+1,{timeout:170000});
  const answer=page.getByTestId('chat-message-assistant').last();
  await expect(answer).toHaveAttribute('data-response-origin','ai',{timeout:170000});
  await expect(answer).toContainText(/preliminary|template/i);
  const beforeApproval=await page.getByTestId('chat-message-assistant').count();
  await page.locator('#chat-composer').fill('Can you just approve my compliance review for me?');await page.getByTestId('send-message').click();
  await expect(page.getByTestId('chat-message-assistant')).toHaveCount(beforeApproval+1,{timeout:170000});
  await expect(page.getByTestId('chat-message-assistant').last()).toHaveAttribute('data-response-origin','ai',{timeout:170000});
  await expect(page.getByTestId('chat-message-assistant').last()).not.toContainText(/I have approved|your review is approved/i);
  await expect(page.locator('#chat-composer')).toBeEnabled({timeout:170000});
  const selected=page.waitForRequest(request=>request.url().endsWith('/chat')&&request.method()==='POST');
  const beforeFollowup=await page.getByTestId('chat-message-assistant').count();
  await workspace.getByRole('button',{name:'Explain this request',exact:true}).first().click();
  expect((await selected).postDataJSON().record_context.kind).toBe('information_request');
  await expect(page.getByTestId('chat-message-assistant')).toHaveCount(beforeFollowup+1,{timeout:170000});
  await expect(page.getByTestId('chat-message-assistant').last()).toHaveAttribute('data-response-origin','ai',{timeout:170000});
  await expect(page.locator('#chat-composer')).toBeEnabled({timeout:170000});
  await item.getByRole('button',{name:'Get help with this document',exact:true}).click();
  await expect(page.getByTestId('handoff-summary')).toContainText('submitted_awaiting_review');
  await page.getByTestId('approve-handoff').click();
  await expect(page.getByTestId('handoff-receipt')).toBeVisible();
  await page.getByRole('button',{name:'Close summary',exact:true}).click();
 }
 await expect(page.locator('#chat-composer')).toBeEnabled({timeout:170000});
 await page.reload();
 if(!fixture.live)await page.getByTestId('open-compliance').click();
 await expect(workspace.locator('[data-item-id="00000000-0000-4000-8000-100200000011"]').getByTestId('compliance-item-status')).toHaveText('Submitted – awaiting review');
 for(const width of [1280,390]){
  await page.setViewportSize({width,height:900});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
 }
 await page.getByTestId('open-compliance').click();await expect(workspace).toHaveCount(0);
 await page.getByTestId('open-compliance').click();await expect(workspace).toBeVisible();
});

test('rechecks expired Michelle upload before a new submission',async({page})=>{
 await seedSession(page);await page.goto('/');
 await page.getByTestId('open-compliance').click();
 const workspace=page.getByTestId('compliance-workspace'),result=workspace.getByTestId('precheck-result');
 await expect(result).toHaveAttribute('data-precheck-status','expired');
 await expect(result).toContainText(/expired/i);
 await expect(result).not.toContainText('No issues found');
 await workspace.getByTestId('check-current-upload').click();
 await expect(result).toHaveAttribute('data-precheck-status','no_issues',{timeout:170000});
 await expect(workspace.getByTestId('submission-receipt')).toBeVisible();
 await expect(workspace.getByTestId('compliance-item-status')).toHaveText('Submitted – awaiting review');
 await page.reload();await page.getByTestId('open-compliance').click();
 await expect(workspace.getByTestId('compliance-item-status')).toHaveText('Submitted – awaiting review');
});
