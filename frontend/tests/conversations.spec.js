import {openMenu,openMenuItem} from './menu.js';
import {test,expect} from '@playwright/test';
import {fixture,seedSession} from './nativeSession';

async function askSafety(page,question) {
  await page.getByLabel('Your message',{exact:true}).fill(question);
  const received=page.waitForResponse(response=>response.url().endsWith('/chat'));
  await page.getByTestId('send-message').click();
  expect((await received).status()).toBe(200);
  await expect(page.getByTestId('chat-message-assistant').last()).toHaveAttribute('data-response-origin','safety');
  await expect(page.getByLabel('Your message',{exact:true})).toBeEnabled();
}

test('saved customer chats reopen with real stored replies and sources',async({page})=>{
  await seedSession(page);
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    await page.goto('/');
    await page.getByTestId('new-chat').click();
    const created=page.waitForResponse(response=>response.url().endsWith('/conversations')&&response.request().method()==='POST');
    await askSafety(page,'I lost my card');
    const saved=await (await created).json();
    await page.reload();
    await expect(page.getByTestId('chat-message-assistant')).toHaveCount(1);
    await expect(page.getByLabel('Your message',{exact:true})).toBeEnabled();
    await page.getByTestId('new-chat').click();
    await expect(page.getByTestId('chat-message-user')).toHaveCount(0);
    await openMenuItem(page,'open-saved-chats');
    const entry=page.locator(`[data-conversation-id="${saved.session_id}"]`);
    await expect(entry).toBeVisible();
    expect(await page.locator('aside[aria-label="Saved chats"]').evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/saved-chats-${width}.png`,fullPage:true});
    await entry.click();
    await expect(page.locator('aside[aria-label="Saved chats"]')).toHaveCount(0);
    await expect(page.getByTestId('chat-message-user')).toHaveCount(1);
    await page.getByTestId('answer-sources').first().locator('summary').click();
    await page.locator('[data-source-id="lost-card"]').click();
    await expect(page.locator('dialog[aria-label="Source details"]')).toBeVisible();
    await page.getByRole('button',{name:'Close source',exact:true}).click();
    await askSafety(page,'I want to talk to a person');
    await page.reload();
    await expect(page.getByTestId('chat-message-assistant')).toHaveCount(2);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
});

test('saved guest chat survives refresh and new chat clears its browser access',async({page})=>{
  await page.setViewportSize({width:390,height:900});
  await page.goto('/');
  await askSafety(page,'I lost my card');
  await page.reload();
  await expect(page.getByTestId('chat-message-assistant')).toHaveCount(1);
  await expect(page.getByTestId('open-saved-chats')).toHaveCount(0);
  await expect(page.getByLabel('Your message',{exact:true})).toBeEnabled();
  await page.getByTestId('new-chat').click();
  await page.reload();
  await expect(page.getByTestId('chat-message-user')).toHaveCount(0);
  await askSafety(page,'I lost my card');
});

test('saved pending reply can be cancelled and the real chat remains usable',async({page})=>{
  await seedSession(page);
  await page.goto('/');
  await openMenuItem(page,'open-saved-chats');
  await page.locator(`[data-conversation-id="${fixture.pending_session_id}"]`).click();
  await expect(page.getByTestId('cancel-pending-reply')).toBeVisible();
  await expect(page.getByLabel('Your message',{exact:true})).toBeDisabled();
  await page.getByTestId('cancel-pending-reply').click();
  await expect(page.getByTestId('cancel-pending-reply')).toHaveCount(0);
  await expect(page.getByLabel('Your message',{exact:true})).toBeEnabled();
  await expect(page.getByTestId('chat-message-assistant')).not.toHaveAttribute('data-response-origin','ai');
  await askSafety(page,'I lost my card');
  await expect(page.getByTestId('chat-message-assistant')).toHaveCount(2);
});

test('expired guest chat requires an explicit new chat before sending',async({page})=>{
  await page.addInitScript(access=>localStorage.setItem('demobank.active.guest',JSON.stringify(access)),{
    session_id:fixture.expired_session_id,guest_token:fixture.guest_token});
  await page.goto('/');
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByLabel('Your message',{exact:true})).toBeDisabled();
  await page.getByTestId('new-chat').click();
  await expect(page.getByLabel('Your message',{exact:true})).toBeEnabled();
  await askSafety(page,'I lost my card');
});

async function unavailableChat(page) {
  await page.setViewportSize({width:390,height:900});
  await page.goto('/');
  await page.getByLabel('Your message',{exact:true}).fill('Hello!');
  await page.getByTestId('send-message').click();
  await expect(page.getByTestId('chat-message-assistant')).toHaveAttribute('data-response-origin','validation');
  await expect(page.getByTestId('answer-sections').locator('[data-domain="smalltalk"][data-response-origin="validation"]')).toHaveAttribute('data-response-origin','validation');
  await expect(page.getByLabel('Your message',{exact:true})).toBeEnabled();
  await page.reload();
  await expect(page.getByTestId('chat-message-assistant')).toHaveAttribute('data-response-origin','validation');
  await expect(page.getByTestId('answer-sections')).toHaveCount(1);
  await askSafety(page,'I lost my card');
  await expect(page.getByTestId('chat-message-assistant')).toHaveCount(2);
  await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/saved-v2-withheld-mobile.png`,fullPage:true});
}

test('saved customer v2 unavailable section stays withheld after refresh and the next safety turn works',async({page})=>{
  await seedSession(page);
  await unavailableChat(page);
  const handoff=page.getByTestId('answer-sections').locator('section[data-response-origin="safety"]');
  await expect(handoff).toContainText('passed it to your relationship manager');
  await openMenuItem(page,'open-actions');
  await expect(page.getByTestId('command-state')).toHaveText('succeeded');
  await expect(page.getByTestId('command-receipt')).toHaveCount(1);
  await expect(page.getByTestId('review-command')).toHaveCount(0);
  await page.getByRole('button',{name:'Close actions',exact:true}).click();
  await page.reload();
  await expect(handoff).toContainText('There is no staffed queue');
});

test('saved guest v2 unavailable section stays withheld after refresh and the next safety turn works',async({page})=>{
  await unavailableChat(page);
  await expect(page.getByTestId('answer-sections').locator('section[data-response-origin="safety"]')).toHaveCount(0);
});
