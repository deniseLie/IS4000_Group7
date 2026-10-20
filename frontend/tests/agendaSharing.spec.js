import {test,expect} from '@playwright/test';
import {seedSession} from './nativeSession';

async function openAgenda(page) {
  const consent=page.waitForResponse(response=>response.url().endsWith('/consent/records'));
  await page.getByTestId('open-data-sharing').click();
  const consentResponse=await consent;expect(consentResponse.status()).toBe(200);
  const grants=(await consentResponse.json()).data.grants;
  const loaded=page.waitForResponse(response=>response.url().endsWith('/relationship/agendas'));
  await page.getByTestId('open-agenda-drafts').click();
  const response=await loaded;expect(response.status()).toBe(200);
  const [agenda]=(await response.json()).data.agendas;
  return {agenda,grants,item:page.locator(`[data-agenda-id="${agenda.agenda_id}"]`)};
}
async function proposeSharing(page,record) {
  await record.item.getByTestId('prepare-agenda-sharing').click();
  await expect(page.getByTestId('agenda-sharing-preview')).toHaveText(record.agenda.content);
  await expect(page.getByTestId('propose-agenda-sharing')).toBeDisabled();
  const grant=record.grants.find(grant=>grant.active&&grant.categories.includes('agenda')&&grant.categories.includes('profile'));
  expect(grant).toBeDefined();
  await page.getByTestId('agenda-sharing-grant').selectOption(grant.grant_id);
  const proposed=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
  await page.getByTestId('propose-agenda-sharing').click();const response=await proposed;expect(response.status()).toBe(200);
  const action=await response.json();expect(action.receipt).toBeNull();expect(action.command.state).toBe('proposed');
  await page.getByTestId('review-agenda-sharing').click();
  const command=page.locator(`[data-command-id="${action.command.command_id}"]`);
  await expect(command.getByTestId('command-agenda-sharing')).toContainText(record.agenda.content);
  return {action,command};
}
async function confirmSharing(page,record) {
  await record.command.getByTestId('review-command').click();
  await expect(record.command.getByTestId('confirm-command')).toBeDisabled();
  await record.command.getByTestId('acknowledge-command').check();
  const confirmed=page.waitForResponse(response=>response.url().endsWith(`/commands/${record.action.command.command_id}/confirm`));
  await record.command.getByTestId('confirm-command').click();const response=await confirmed;expect(response.status()).toBe(200);
  return response.json();
}

test('agenda sharing reviews exact snapshots at all viewport sizes',async({page})=>{
  await seedSession(page);const snapshots=[];
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});await page.goto('/');
    const draft=await openAgenda(page),proposal=await proposeSharing(page,draft);
    const action=await confirmSharing(page,proposal);expect(action.receipt.outcome).toBe('succeeded');
    await expect(proposal.command.getByTestId('command-state')).toHaveText('succeeded');
    await expect(proposal.command.getByTestId('command-receipt')).toContainText(action.receipt.receipt_id);
    snapshots.push(proposal);
    const dialog=page.locator('dialog[aria-label="Actions"]');
    expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await proposal.command.scrollIntoViewIfNeeded();
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/agenda-sharing-${width}.png`,fullPage:true});
  }
  await page.keyboard.press('Escape');const draft=await openAgenda(page);
  await draft.item.getByTestId('edit-agenda').click();await page.getByTestId('agenda-topics').fill('Changed after sharing');
  const saved=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
  await page.getByTestId('save-agenda').click();expect((await saved).status()).toBe(200);
  await page.keyboard.press('Escape');await page.getByTestId('open-actions').click();
  const historical=page.locator(`[data-command-id="${snapshots[0].action.command.command_id}"]`);
  await expect(historical.getByTestId('command-agenda-sharing')).toContainText(draft.agenda.content);
  await expect(historical.getByTestId('command-agenda-sharing')).not.toContainText('Changed after sharing');
});

test('agenda sharing requires one grant covering every category',async({page})=>{
  await seedSession(page);await page.goto('/');const draft=await openAgenda(page);
  await draft.item.getByTestId('prepare-agenda-sharing').click();
  await expect(page.getByTestId('agenda-sharing-no-consent')).toBeVisible();
  await expect(page.getByTestId('propose-agenda-sharing')).toBeDisabled();
});

test('agenda edit after review produces a failed sharing receipt',async({page})=>{
  await seedSession(page);await page.goto('/');const draft=await openAgenda(page),proposal=await proposeSharing(page,draft);
  await page.keyboard.press('Escape');const current=await openAgenda(page);
  await current.item.getByTestId('edit-agenda').click();await page.getByTestId('agenda-topics').fill('Edited before sharing confirmation');
  const saved=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().method()==='POST');
  await page.getByTestId('save-agenda').click();expect((await saved).status()).toBe(200);
  await page.keyboard.press('Escape');await page.getByTestId('open-actions').click();
  const result=await confirmSharing(page,proposal);expect(result.receipt.outcome).toBe('failed');
  await expect(proposal.command.getByTestId('command-receipt')).toContainText('This snapshot was not shared');
});
