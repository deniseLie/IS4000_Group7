import {openMenu,openMenuItem} from './menu.js';
import {test,expect} from '@playwright/test';
import {fixture,seedSession} from './nativeSession';

test('staff outcomes remain recorded in customer actions records and inbox',async({page})=>{
  await seedSession(page);
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    await page.goto('/');
    const loaded=page.waitForResponse(response=>response.url().endsWith('/commands'));
    await openMenuItem(page,'open-actions');
    expect((await loaded).status()).toBe(200);
    for(const id of fixture.staff_command_ids) {
      const command=page.locator(`[data-command-id="${id}"]`);
      await expect(command.getByTestId('command-state')).toHaveText('succeeded');
      await expect(command.getByTestId('command-receipt')).toContainText(`rcpt_${id.replaceAll('-','')}`);
      await expect(command.getByTestId('review-command')).toHaveCount(0);
    }
    const actions=page.locator('dialog[aria-label="Actions"]');
    expect(await actions.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/staff-receipts-${width}.png`,fullPage:true});
    await page.keyboard.press('Escape');
    const records=page.waitForResponse(response=>response.url().endsWith('/customer/transactions'));
    await openMenuItem(page,'open-records');
    expect((await records).status()).toBe(200);
    await expect(page.getByTestId('records-card')).toContainText('cancelled');
    await page.keyboard.press('Escape');
    const inbox=page.waitForResponse(response=>response.url().endsWith('/notifications'));
    await page.getByTestId('open-notifications').click();
    expect((await inbox).status()).toBe(200);
    for(const key of ['card.unblocked','card.cancelled','handoff.claimed','handoff.closed']) {
      await expect(page.locator(`[data-template-key="${key}"]`)).toHaveCount(1);
    }
    const dialog=page.getByTestId('inbox-page');
    expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/staff-inbox-${width}.png`,fullPage:true});
    await page.goBack();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  }
});
