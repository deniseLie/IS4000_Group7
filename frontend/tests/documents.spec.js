import {test,expect} from '@playwright/test';
import {fixture,seedSession} from './nativeSession';

test.beforeEach(async({page})=>seedSession(page));

test('owned document metadata stays scoped and reloads at mobile widths',async({page})=>{
  const chatCalls=[];
  page.on('request',request=>{if(request.url().endsWith('/chat'))chatCalls.push(request.url());});
  await page.goto('/');
  const loaded=page.waitForResponse(response=>response.url().endsWith('/customer/documents'));
  await page.getByTestId('open-records').click();
  const response=await loaded;expect(response.status()).toBe(200);
  const snapshot=await response.json();
  const documents=page.getByTestId('records-document');
  await expect(documents).toHaveCount(2);
  expect(await documents.evaluateAll(nodes=>nodes.map(node=>node.dataset.documentId).sort())).toEqual([...fixture.document_ids].sort());
  await expect(documents).toContainText(['1002-','1002-']);
  await expect(page.getByTestId('records-documents')).not.toContainText('PRIVATE PRECHECK NOTES');
  const reloaded=page.waitForResponse(response=>response.url().endsWith('/customer/documents'));
  await page.getByTestId('reload-records').click();
  const next=await reloaded;expect(next.status()).toBe(200);
  expect((await next.json()).call_id).not.toBe(snapshot.call_id);
  await expect(documents).toHaveCount(2);
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    await page.getByTestId('records-documents').scrollIntoViewIfNeeded();
    const dialog=page.locator('dialog[aria-label="Synthetic customer records"]');
    expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/documents-${width}.png`,fullPage:true});
  }
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('open-records')).toBeFocused();
  expect(chatCalls).toEqual([]);
});
