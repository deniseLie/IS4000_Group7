import {openMenu,openMenuItem} from './menu.js';
import {test,expect} from '@playwright/test';
import {fixture,seedSession} from './nativeSession';

test('recorded notification delivery follows a confirmed action and preserves read state',async({page})=>{
  await seedSession(page);
  await page.goto('/');
  await openMenuItem(page,'open-actions');
  const command=page.locator(`[data-command-id="${fixture.command_id}"]`);
  await command.getByTestId('review-command').click();
  await command.getByTestId('acknowledge-command').check();
  const confirmation=page.waitForResponse(response=>response.url().endsWith(`/commands/${fixture.command_id}/confirm`));
  await command.getByTestId('confirm-command').click();
  const confirmed=await confirmation;
  expect(confirmed.status()).toBe(200);
  expect((await confirmed.json()).receipt.outcome).toBe('succeeded');
  await expect(command.getByTestId('command-state')).toHaveText('succeeded');
  await page.getByRole('button',{name:'Close actions',exact:true}).click();
  const delivery=page.waitForResponse(response=>response.url().endsWith('/notifications')&&response.request().method()==='GET');
  await page.getByTestId('open-notifications').click();
  expect((await delivery).status()).toBe(200);
  const item=page.locator('[data-template-key="card.blocked"]');
  // Native PostgreSQL has no Realtime; delivery may finish after the first inbox read.
  await expect(async()=>{
    await page.getByRole('button',{name:'Reload notifications',exact:true}).click();
    await expect(item).toHaveCount(1,{timeout:1000});
  }).toPass({timeout:10000});
  await expect(item).toContainText('4821');
  await expect(item.getByTestId('notification-read-state')).toHaveText('Unread');
  const read=page.waitForResponse(response=>response.url().endsWith('/read')&&response.request().method()==='POST');
  await item.getByTestId('mark-notification-read').click();
  expect((await read).status()).toBe(200);
  await expect(item.getByTestId('notification-read-state')).toHaveText('Read');
  await page.reload();
  await page.getByTestId('open-notifications').click();
  await expect(item.getByTestId('notification-read-state')).toHaveText('Read');
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    const dialog=page.getByTestId('inbox-page');
    expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/inbox-${width}.png`,fullPage:true});
  }
});
test('recorded notification empty inbox and guest access remain separate',async({page,browser})=>{
  await seedSession(page);
  await page.goto('/');
  await page.getByTestId('open-notifications').click();
  await expect(page.getByTestId('inbox-empty')).toBeVisible();
  const guest=await browser.newContext();
  try {
    const publicPage=await guest.newPage();
    await publicPage.goto('/');
    await expect(publicPage.getByTestId('open-notifications')).toHaveCount(0);
  } finally {await guest.close();}
});
