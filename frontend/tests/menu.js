// Records, Data sharing and Sign out via the account menu; Actions via the page nav (tab bar when narrow); Saved chats via Recent chats.
export async function openMenu(page) {
  const trigger=page.getByTestId('open-account-menu');
  if(await trigger.getAttribute('aria-expanded')!=='true')await trigger.click();
}
export async function openMenuItem(page,id) {
  if(id==='open-actions') {
    const narrow=page.viewportSize().width<=760;
    await page.getByTestId(narrow?'tab-actions':'nav-actions').click();return;
  }
  if(id==='open-saved-chats') {await page.getByTestId('open-recent-chats').click();return;}
  await openMenu(page);await page.getByTestId(id).click();
}
