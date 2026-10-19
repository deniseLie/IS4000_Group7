import {test,expect} from '@playwright/test';
import fs from 'node:fs';

const fixture=JSON.parse(fs.readFileSync(process.env.NATIVE_BROWSER_FIXTURE,'utf8'));
const claims=JSON.parse(Buffer.from(fixture.access_token.split('.')[1],'base64url').toString('utf8'));
const key=`sb-${new URL(fixture.data_api_url).hostname.split('.')[0]}-auth-token`;
const session={access_token:fixture.access_token,refresh_token:'native-refresh-token-not-used',token_type:'bearer',
  expires_in:3600,expires_at:claims.exp,user:{id:fixture.user_id,aud:'authenticated',role:'authenticated',
    email:'native.user@example.invalid',app_metadata:{provider:'email'},user_metadata:{},created_at:new Date().toISOString()}};

test.beforeEach(async({page})=>{
  // A locally issued, unexpired session; the real API and PostgREST verify its signature and enforce RLS.
  // Password login and the hosted Auth server remain outside this local integration suite.
  await page.addInitScript(({key,session})=>localStorage.setItem(key,JSON.stringify(session)),{key,session});
});

test('consent status uses real assigned manager and registry at desktop and mobile sizes',async({page})=>{
  const chatCalls=[];
  page.on('request',request=>{if(request.url().endsWith('/chat'))chatCalls.push(request.url());});
  for(const width of [1280,768,390,320]) {
    await page.setViewportSize({width,height:900});
    await page.goto('/');
    await page.getByTestId('open-data-sharing').click();
    const dialog=page.locator('dialog[aria-label="Data sharing"]');
    await expect(dialog).toBeVisible();
    await expect(page.getByTestId('sharing-recipient')).toHaveText(fixture.manager);
    await expect(page.getByTestId('sharing-status')).toHaveText(fixture.granted?
      'You have active consent for this profile sharing.':'There is no active consent for this profile sharing.');
    await expect(page.getByTestId('sharing-error')).toHaveCount(0);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({path:`${process.env.NATIVE_SCREENSHOT_DIR}/sharing-${width}.png`,fullPage:true});
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(page.getByTestId('open-data-sharing')).toBeFocused();
  }
  expect(chatCalls).toEqual([]);
});

test('unavailable registry is an explicit UI error without a consent status',async({page})=>{
  await page.goto('/');
  await page.getByTestId('open-data-sharing').click();
  await expect(page.getByTestId('sharing-error')).toBeVisible();
  await expect(page.getByTestId('sharing-status')).toHaveCount(0);
  await expect(page.getByTestId('sharing-no-manager')).toHaveCount(0);
});
