import {openMenu,openMenuItem} from './menu.js';
import {test,expect} from '@playwright/test';
import {fixture,seedSession} from './nativeSession';

async function openAppointments(page) {
  await openMenuItem(page,'open-data-sharing');
  const loaded=page.waitForResponse(response=>response.url().endsWith('/relationship/appointments'));
  await page.getByTestId('open-relationship-details').click();
  expect((await loaded).status()).toBe(200);
  await expect(page.getByTestId('refresh-relationship')).toBeEnabled();
}
async function confirmRecordedProposal(page,operation) {
  await page.getByTestId('review-booking-actions').click();
  const commands=page.locator('[data-command-id]');
  const proposal=commands.filter({has:page.getByTestId('command-state').filter({hasText:/^proposed$/})});
  await expect(proposal).toHaveCount(1);
  await proposal.getByTestId('review-command').click();
  await expect(proposal.getByTestId('confirm-command')).toBeDisabled();
  await proposal.getByTestId('acknowledge-command').check();
  const completed=page.waitForResponse(response=>response.url().endsWith('/confirm')&&response.request().method()==='POST');
  await proposal.getByTestId('confirm-command').click();
  const response=await completed;
  expect(response.status()).toBe(200);
  const result=await response.json();
  expect(result.command.type).toBe(operation);
  expect(result.receipt.outcome).toBe('succeeded');
  const recorded=page.locator(`[data-command-id="${result.command.command_id}"]`);
  await expect(recorded.getByTestId('command-state')).toHaveText('succeeded');
  await expect(recorded.getByTestId('command-receipt')).toBeVisible();
  return result.receipt.result.appointment_id;
}

test('appointment form holds confirms and cancels through real action receipts',async({page})=>{
  await seedSession(page);
  const chatCalls=[];
  page.on('request',request=>{if(request.url().endsWith('/chat'))chatCalls.push(request.url());});
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    await page.goto('/');
    await openAppointments(page);
    await page.getByTestId('slots-from').fill(fixture.appointment_date);
    await page.getByTestId('slots-to').fill(fixture.appointment_date);
    const refreshed=page.waitForResponse(response=>response.url().endsWith('/relationship/slots'));
    await page.getByTestId('refresh-relationship').click();
    expect((await refreshed).status()).toBe(200);
    await expect(page.getByTestId('refresh-relationship')).toBeEnabled();
    await page.getByTestId('appointment-format').selectOption('phone');
    const held=page.waitForResponse(response=>response.url().endsWith('/relationship/holds'));
    await page.getByTestId('hold-appointment').first().click();
    const holdResponse=await held;
    expect(holdResponse.status()).toBe(200);
    const hold=await holdResponse.json();
    const appointment=page.locator(`[data-appointment-id="${hold.receipt.result.appointment_id}"]`);
    await expect(appointment.getByTestId('appointment-status')).toHaveText('held');
    await expect(page.getByTestId('booking-outcome')).toContainText(hold.receipt.receipt_id);
    await appointment.getByTestId('prepare-booking-confirm').click();
    const id=await confirmRecordedProposal(page,'appointment.confirm');
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/booking-receipt-${width}.png`,fullPage:true});
    await page.keyboard.press('Escape');
    await openAppointments(page);
    const confirmed=page.locator(`[data-appointment-id="${id}"]`);
    await expect(confirmed.getByTestId('appointment-status')).toHaveText('confirmed');
    await expect(confirmed).toContainText('phone');
    const dialog=page.locator('dialog[aria-label="Data sharing"]');
    expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    await confirmed.scrollIntoViewIfNeeded();
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/booking-current-${width}.png`,fullPage:true});
    await confirmed.getByTestId('prepare-booking-cancel').click();
    expect(await confirmRecordedProposal(page,'appointment.cancel')).toBe(id);
    await page.keyboard.press('Escape');
    await openAppointments(page);
    await expect(page.locator(`[data-appointment-id="${id}"]`).getByTestId('appointment-status')).toHaveText('cancelled');
    await page.keyboard.press('Escape');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  }
  expect(chatCalls).toEqual([]);
});
