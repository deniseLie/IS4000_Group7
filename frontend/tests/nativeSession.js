import fs from 'node:fs';

export const fixture=JSON.parse(fs.readFileSync(process.env.NATIVE_BROWSER_FIXTURE,'utf8'));
const claims=JSON.parse(Buffer.from(fixture.access_token.split('.')[1],'base64url').toString('utf8'));
const key=`sb-${new URL(fixture.data_api_url).hostname.split('.')[0]}-auth-token`;
const session={access_token:fixture.access_token,refresh_token:'native-refresh-token-not-used',token_type:'bearer',
  expires_in:3600,expires_at:claims.exp,user:{id:fixture.user_id,aud:'authenticated',role:'authenticated',
    email:'native.user@example.invalid',app_metadata:{provider:'email'},user_metadata:{},created_at:new Date().toISOString()}};

export async function seedSession(page) {
  await page.addInitScript(({key,session})=>localStorage.setItem(key,JSON.stringify(session)),{key,session});
}
