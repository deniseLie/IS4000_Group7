import {test,expect} from '@playwright/test';
import {fixture,seedSession} from './nativeSession';

// Workflow 3 (David) without a model call: introduction from records, request intake and status, handover note with
// consent and sharing, and booking. Each change is confirmed in chat and shown only from its server receipt.
const shot=(page,name)=>page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/workflow3-${name}.png`,fullPage:true});
async function confirmDialog(page,path) {
  const dialog=page.getByTestId('rm-dialog');
  await expect(dialog).toBeVisible();
  const done=page.waitForResponse(response=>response.url().includes(path)&&response.request().method()==='POST');
  await page.getByTestId('rm-dialog-confirm').click();
  expect((await done).status()).toBe(200);
  await expect(dialog).toBeHidden();
}

test('workflow 3 introduces the new RM, tracks requests, shares a note and books a meeting',async({page})=>{
  test.setTimeout(120000);
  await seedSession(page);
  const chatCalls=[];
  page.on('request',request=>{if(request.url().endsWith('/chat'))chatCalls.push(request.url());});
  await page.setViewportSize({width:1280,height:900});
  await page.goto('/');
  const intro=page.getByTestId('rm-intro');
  await expect(intro).toContainText('You have a new relationship manager');
  await expect(page.getByTestId('rm-name')).toHaveText('Marcus Chua');
  await expect(page.getByTestId('rm-card')).toContainText('+65 6000 0000');
  await expect(page.getByTestId('rm-notice')).toContainText('reorganised its private client advisory teams');
  await expect(page.getByTestId('rm-notice')).not.toContainText('team_restructure');
  await shot(page,'1-introduction');

  // RC4: a typed request gets a reply (AI, withheld or unavailable) and always one hand-off button to the request step.
  await page.getByLabel('Your message').fill('can someone send me my annual portfolio statement');
  await page.getByTestId('send-message').click();
  const handoff=page.getByTestId('rm-handoff');
  await expect(handoff).toContainText('Send this request to Marcus',{timeout:30000});
  await shot(page,'1b-chat-handoff');
  await handoff.click();
  await expect(page.getByTestId('relationship-page')).toBeVisible();  // hybrid: the chat hands off to the task page
  await expect(page.getByTestId('rm-nav-request')).toHaveAttribute('aria-current','step');
  await expect(page.getByTestId('rm-request-what')).toHaveValue('can someone send me my annual portfolio statement');
  await page.getByTestId('rm-urgency-normal').check();
  await page.getByTestId('rm-request-deadline').fill(fixture.deadline);
  await shot(page,'2a-request-form');
  await page.getByTestId('rm-request-review').click();
  await expect(page.getByTestId('rm-review-what')).toHaveText('can someone send me my annual portfolio statement');
  await shot(page,'2-request-review');
  await confirmDialog(page,'/confirm');
  await expect(page.getByTestId('rm-request-receipt')).toContainText("Marcus Chua (DEMO)'s request queue");
  await expect(page.getByTestId('rm-request-receipt')).toContainText('Sent');
  await expect(page.getByTestId('rm-nav-request')).toHaveClass(/done/);

  await page.getByTestId('rm-request-view-status').click();
  await expect(page.getByTestId('rm-step-status')).toBeVisible();
  await expect(page.getByTestId('rm-request')).toHaveCount(2);
  const old=page.locator('[data-testid="rm-request"][data-status="seen"]');
  await expect(old).toContainText('Your action: Confirm the new quarterly transfer amount');
  await expect(old).toContainText('not recorded as transferred');
  await expect(page.locator('[data-testid="rm-request"][data-status="sent"]')).toContainText('Nothing is needed from you');
  await shot(page,'3-request-status');

  // RC2: drafted from records, topics organised into an agenda, whole note editable, shared only after consent.
  await page.getByTestId('rm-nav-note').click();
  await expect(page.getByTestId('rm-note-lines')).toContainText('Preference: please communicate with me in English');
  await page.getByTestId('rm-note-topics').fill('estate planning, review of fees, how often we meet');
  await page.getByTestId('rm-note-add').click();
  await expect(page.getByTestId('rm-note-agenda')).toContainText('1. Estate planning');
  await expect(page.getByTestId('rm-note-agenda')).toContainText('3. How often we meet');
  await page.getByTestId('rm-note-edit').click();
  await expect(page.getByTestId('rm-note-editor')).toHaveValue(/Agenda item 2: Review of fees/);
  await page.getByTestId('rm-note-edit').click();
  await page.getByTestId('rm-note-share').click();
  await expect(page.getByTestId('rm-share-recipient')).toContainText('Marcus Chua (DEMO)');
  await expect(page.getByTestId('rm-share-preview')).toContainText('Agenda item 1: Estate planning');
  await expect(page.getByTestId('rm-dialog-confirm')).toBeDisabled();
  await page.getByTestId('rm-dialog-acknowledge').check();
  await shot(page,'4-share-consent');
  await page.getByTestId('rm-dialog-confirm').click();
  await expect(page.getByTestId('rm-note-shared')).toContainText('Shared.',{timeout:20000});
  await expect(page.getByTestId('rm-nav-note')).toHaveClass(/done/);
  await page.getByTestId('rm-nav-request').click();
  await expect(page.getByTestId('rm-request-receipt')).toContainText('Sent');  // earlier steps keep their receipts

  // RC3: slots from the RM's calendar, ten-minute hold, booking shown only after the system confirms it.
  await page.getByTestId('rm-nav-meeting').click();
  await expect(page.getByTestId('rm-slot').first()).toBeVisible();
  await page.getByTestId('rm-meeting-format').selectOption('video');
  await page.getByTestId('rm-slot').first().click();
  await expect(page.getByTestId('rm-hold-timer')).toContainText('It is not booked until you confirm');
  await page.getByTestId('rm-booking-review').click();
  await expect(page.getByTestId('rm-dialog')).toContainText('DEMO-MC-ROOM-1');
  await confirmDialog(page,'/confirm');
  await expect(page.getByTestId('rm-booking-confirmed')).toContainText('Booked.');
  await expect(page.getByTestId('rm-booking-joining')).toContainText('DEMO-MC-ROOM-1');
  await expect(page.getByTestId('rm-nav-meeting')).toHaveClass(/done/);
  await shot(page,'5-booking-confirmed');
  await page.getByTestId('rm-mode-all').click();
  await expect(page.getByTestId('relationship-request')).toHaveCount(2);  // the full manager view lists the same records
  await page.getByTestId('rm-mode-guided').click();
  expect(chatCalls.length).toBe(1);  // only the typed request; every step above ran without the model

  for(const width of [390,320]) {
    await page.setViewportSize({width,height:900});
    await page.goto('/');
    await expect(page.getByTestId('rm-intro')).toBeVisible();
    await page.getByTestId('rm-open-status').click();
    await expect(page.getByTestId('rm-request')).toHaveCount(2);
    await page.getByTestId('rm-back-to-chat').click();
    await expect(page.getByTestId('rm-intro')).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1)).toBe(true);
    await shot(page,`6-mobile-${width}`);
  }
});
