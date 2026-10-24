// Records, Data sharing, Saved chats, Actions and Sign out live in the account menu.
export async function openMenu(page) {
  const trigger=page.getByTestId('open-account-menu');
  if(await trigger.getAttribute('aria-expanded')!=='true')await trigger.click();
}
export async function openMenuItem(page,id) {await openMenu(page);await page.getByTestId(id).click();}
