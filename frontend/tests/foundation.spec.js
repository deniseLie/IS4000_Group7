import {test,expect} from '@playwright/test';

async function ask(page,question) {
  await page.locator('#chat-composer').fill(question);
  await page.getByTestId('send-message').click();
  await expect(page.locator('#chat-composer')).toBeEnabled({timeout:150000});
}

test('v2 keeps fixed safety guidance outside agent execution',async({page})=>{
  await page.goto('/');
  await ask(page,'I lost my card');
  await expect(page.locator('[data-response-origin="safety"]')).toHaveCount(1);
  await expect(page.getByTestId('answer-sections')).toHaveCount(0);
});

test('real v2 agent answer renders a sourced section and opens its source',async({page})=>{
  await page.goto('/');
  await ask(page,'What happens if I withdraw my fixed deposit early?');
  const section=page.locator('.answer-section[data-domain="retail"]');
  await expect(section).toHaveAttribute('data-response-origin','ai');
  await expect(section.locator('.answer-text')).toContainText(/interest/i);
  await section.locator('summary').click();
  await section.locator('[data-source-id="sg-accounts"]').click();
  await expect(page.locator('dialog[aria-label="Source details"]')).toBeVisible();
});

test('real v2 planner orders fraud security and banking sections',async({page})=>{
  await page.goto('/');
  await ask(page,"What is the difference between secured and unsecured loans? Also, I cannot log in. There is an unfamiliar card charge.");
  const sections=page.locator('.answer-section');
  await expect(sections).toHaveCount(3);
  await expect(sections.nth(0)).toHaveAttribute('data-domain','fraud');
  await expect(sections.nth(0)).toHaveAttribute('data-priority','P0');
  await expect(sections.nth(0)).toHaveAttribute('data-response-origin','ai');
  await expect(sections.nth(1)).toHaveAttribute('data-domain','retail');
  await expect(sections.nth(1)).toHaveAttribute('data-priority','P1');
  await expect(sections.nth(1)).toHaveAttribute('data-response-origin','ai');
  await expect(sections.nth(2)).toHaveAttribute('data-domain','retail');
  await expect(sections.nth(2)).toHaveAttribute('data-priority','P3');
  await expect(sections.nth(2)).toHaveAttribute('data-response-origin','ai');
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
});
