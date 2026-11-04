import {test,expect} from '@playwright/test';
import {seedSession} from './nativeSession';

// v5 offers in the normal UI (build plan phase 6): one Confirm and one Cancel per reply, a typed yes, expiry and a group of two.
// Native: backend/tests/test_offers_browser.py (signed local session, native JWT/RLS data; the lost card is a fixed route,
// the two-offer reply starts after the planner with a fixed safety-origin Security output, so no model answer is simulated).

async function ask(page,text) {
  await page.locator('#chat-composer').fill(text);
  await page.getByTestId('send-message').click();
  await expect(page.locator('#chat-composer')).toBeEnabled({timeout:60000});
}
async function lostCard(page) {
  await seedSession(page);
  await page.goto('/');
  await ask(page,'I lost my card');
  const offer=page.getByTestId('offer-block');
  await expect(offer).toHaveAttribute('data-state','open');
  await expect(offer.getByTestId('offer-expiry')).toContainText('Expires in');
  return offer;
}
const latest=page=>page.getByTestId('chat-message-assistant').last();

test('offer button confirm records the report once',async({page})=>{
  const offer=await lostCard(page);
  await offer.getByTestId('offer-confirm').click();
  await expect(page.getByTestId('chat-message-user').last()).toHaveText('Yes, confirm');
  await expect(latest(page)).toContainText(/TCK-\d+/);
  await expect(page.getByTestId('offer-block')).toHaveCount(0);  // answered: the buttons go
  await page.reload();
  await expect(page.getByTestId('offer-block')).toHaveCount(0);
});

test('offer typed yes confirms the open offer',async({page})=>{
  await lostCard(page);
  await ask(page,'yes');
  await expect(latest(page)).toContainText(/TCK-\d+/);
  await expect(page.getByTestId('offer-block')).toHaveCount(0);
});

test('offer cancel records nothing',async({page})=>{
  const offer=await lostCard(page);
  await offer.getByTestId('offer-cancel').click();
  await expect(page.getByTestId('chat-message-user').last()).toHaveText('No, cancel');
  await expect(latest(page)).toContainText('haven’t reported it');
  await expect(latest(page)).not.toContainText(/TCK-\d+/);
});

test('offer expired disables the buttons',async({page})=>{
  await page.clock.install();
  const offer=await lostCard(page);
  await page.clock.fastForward('31:00');
  await expect(offer).toHaveAttribute('data-state','expired');
  await expect(offer.getByTestId('offer-confirm')).toBeDisabled();
  await expect(offer.getByTestId('offer-cancel')).toBeDisabled();
  await expect(offer.getByTestId('offer-expiry')).toHaveText('Ask again if you still want this.');
});

test('offer two in one reply confirm together by button',async({page})=>{
  await seedSession(page);
  await page.goto('/');
  await ask(page,'I don’t recognise a charge on my card');
  const offer=page.getByTestId('offer-block');
  await expect(offer.getByTestId('offer-item')).toHaveCount(2);
  for(const width of [1280,390,320]) {
    await page.setViewportSize({width,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
  await offer.getByTestId('offer-confirm').click();
  await expect(latest(page)).toContainText(/TCK-\d+/);
  await expect(page.getByTestId('offer-block')).toHaveCount(0);
});
