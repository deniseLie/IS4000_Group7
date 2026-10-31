# v10: Client Assistant — inline request review (31 Oct 2026)

This is based on the v9 merge-overwrite source. Only the client UI and shared request-intake helper changed. The RM prototype and banking backend are untouched.

## What changed

1. Removed the permanent **Raise or review a request** button above the Assistant composer.
2. A ready card-limit draft now appears **inside the chat reply** rather than opening a side drawer. If mandatory details are missing, the client can click **Review request details** to finish them in the chat. A client can also explicitly ask to raise a new request and choose any customer-raisable category from the backend.
3. The review is editable in chat. When a chat-extracted card-limit detail changes, the auto-generated short summary is updated to avoid sending an old amount or date. Clients can also manually edit the summary.
4. Both the Assistant and the separate **My Requests > Raise a request** page call the same `prepareTicketAction` helper. This uses existing `createActionCase` and `proposeAction` endpoints. No ticket is created at draft/proposal time.
5. After the backend returns a validated immutable proposal, the inline card shows the **server's confirmed category, details, owner and summary** and requires an explicit checkbox and **Confirm & submit**. It then calls existing `confirmAction`. A receipt/reference appears only when a `succeeded` receipt is returned. The bank/specialist team decides approval.
6. Edit or cancel after proposal sends `rejectAction` before changing the draft. A new chat turn after a pending proposal attempts to cancel it first; if that fails, the turn is blocked so an old proposal is not accidentally left submit-ready.
7. **My Requests > Raise a request** remains a separate manual path. Fraud reporting, compliance, and meeting bookings still use specialist workflows.

## Important limitations

- The inline card is a **live unsaved draft** until the backend receives a proposal. Its UI is not yet persisted/reconstructed across reloading saved chats; proposals/confirmed actions remain traceable in Actions.
- Card-limit auto-extraction is implemented for the tested workflow. The generic chat intake offers the backend's supported categories, but fields in other categories are entered through the inline form rather than universally extracted by the LLM.
- The client must verify the affected card's last four digits; selecting “Travel Card” in chat is not sufficient to assume a particular card ID.
- No live Supabase, OpenRouter or browser end-to-end test was possible in the build environment. No change was made to the backend verification or permissions.

## Browser regression tests (signed-in synthetic demo account)

| Test | Input/action | Expected |
| --- | --- | --- |
| 1 | Open Assistant | White composer **Raise or review a request** button is absent; My Requests still has **Raise a request** |
| 2 | Card limit flow: temporary SGD 12,000 Travel Card until 20 December 2028 | Review card appears directly in chat, showing the correct amount and date, no drawer |
| 3 | Click **Edit details** | Fields appear inside the card; card last 4 can be entered/verified |
| 4 | Change SGD 12,000 to SGD 10,000 | Editable summary updates; review/proposal does not silently retain SGD 12,000 |
| 5 | Try confirmation with last 4 missing | Cannot proceed; missing field message is shown |
| 6 | Complete required fields and **Continue to confirmation** | Server proposal displayed in chat, but no ticket/reference yet |
| 7 | Without checkbox, click **Confirm & submit** | Disabled |
| 8 | Check acknowledgement and click **Confirm & submit** | One backend ticket/reference only on successful receipt; responsible team is correct |
| 9 | Create proposal then click **Edit details** | Existing proposal rejected, editable draft reopened; old proposal cannot be confirmed |
| 10 | Create proposal then send a chat correction | Old proposal rejected before new answer; failed cancellation blocks the turn |
| 11 | Click **Dismiss** on a draft | Card closes without creating a ticket |
| 12 | Say “I want to raise a new request” | A chat option allows client to choose a supported request category inline |
| 13 | Ask for older certified statement | Inline request review opens for statement copy, no generic request drawer |
| 14 | Ask “Has the bank approved my limit increase?” | Read-only status enquiry, **no new application review** |
| 15 | Ask “What does a temporary limit mean?” | Explanation only, **no draft card** |
| 16 | Fraud, lost card, compliance review, booking | Their existing specialist flows remain, not generic ticket creation |
| 17 | My Requests > Raise a request | Existing manual intake, shared validation, and Actions confirmation still work |
| 18 | Sign out / expired session / API unavailable | No submission; clear authentication/error message |

## Local verification

- `cd frontend && npm test`: 121/121 Node unit tests passed, including six new shared-intake tests.
- TypeScript parser (`tsc --jsx preserve --allowJs --noResolve --noEmit`, empty typeRoots) reported no JSX syntax errors for the changed JSX files.
- `npm run build` could not execute because the `vite` package was not installed in this runner. Reinstall dependencies with `npm ci` and run `npm run build` on the developer machine.
- Live end-to-end and accessibility testing remain required before demo.

## Apply

Back up the current project. From the **parent** folder containing `IS4000_Group7`:

```bash
unzip -o IS4000_Group7_Client_Inline_v10_Patch.zip
```

Restart frontend (backend does not need redeploy for these changes, but both may be restarted). No `.env`, secrets, dependencies, migrations or database records are bundled into the patch.
