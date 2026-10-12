import {test,expect} from '@playwright/test';
import fs from 'node:fs';
test('real model answers through browser and backend with a source',async({page})=>{
 await page.goto('/');await page.getByRole('textbox',{name:'Your message'}).fill('How do I download a statement?');await page.getByRole('button',{name:'Send',exact:true}).click();
 await expect(page.getByRole('button',{name:'Send',exact:true})).toBeVisible({timeout:80000});
 await expect(page.locator('.bank-chat-message.assistant')).toContainText('statement');
 await expect(page.getByText('Sources',{exact:true})).toBeVisible();
 await page.screenshot({path:'test-results/live-desktop-chat.png',fullPage:true});
});
test('real unlinked synthetic login cannot access banking records',async({page})=>{
 test.skip(!process.env.TEST_UNLINKED_FIXTURE_FILE,'Requires a temporary unlinked synthetic test login');
 const [user]=JSON.parse(fs.readFileSync(process.env.TEST_UNLINKED_FIXTURE_FILE,'utf8'));
 await page.goto('/');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.getByLabel('Email address').fill(user.email);await page.getByLabel('Password',{exact:true}).fill(user.password);await page.getByRole('button',{name:'Sign In →',exact:true}).click();
 await expect(page.getByRole('button',{name:'Sign out'})).toBeVisible({timeout:20000});
 await page.getByRole('textbox',{name:'Your message'}).fill('What is my balance?');await page.getByRole('button',{name:'Send',exact:true}).click();
 await expect(page.locator('.bank-chat-bubble.error')).toContainText('No demo customer profile',{timeout:30000});
 await page.getByLabel('Information',{exact:true}).selectOption('public');
 await page.getByRole('textbox',{name:'Your message'}).fill('I lost my card');await page.getByRole('button',{name:'Send',exact:true}).click();
 await expect(page.locator('.bank-chat-message.assistant')).toContainText('immediately',{timeout:30000});
 await page.getByRole('button',{name:'Sign out'}).click();await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeVisible();
 await expect(page.locator('.bank-chat-message')).toHaveCount(0);
});
