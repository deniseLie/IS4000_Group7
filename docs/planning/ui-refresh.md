# DemoBank interface refresh — 14 October 2026

The approved UBS-inspired visual direction is integrated into the working React
app. It uses DemoBank text branding, with no UBS logo or external font/image assets.
The design has white and warm neutral surfaces, charcoal typography, red accents,
a desktop topic sidebar, compact mobile topic cards and a simpler composer.
Sign-in and source/support panels share the same styling.

Topic buttons only put a suggested question into the composer and focus it. The
customer sends it through the existing backend. AI replies still use the real
configured provider. Safety responses, redaction, source validation and session
reset behaviour remain in place. No sample-answer preview logic was copied into
the application. No backend, provider or database settings were changed for this
UI refresh.

## What was checked

- 21 frontend utility tests passed; the production build passed again after the
  final UI changes.
- All 12 non-model browser workflows have passing evidence. These include the
  previous 10 safety/session workflows, responsive home and sign-in navigation
  at 1440, 768, 390 and 320 pixels, and source/support dialog keyboard behaviour.
- Two real Luna browser scenarios passed: sourced deposit guidance with a
  follow-up, and credit-card minimum-payment guidance with source details.
- The first combined run had 13 passes and one keyboard-focus failure. Shift+Tab
  could leave a source panel. An explicit focus loop fixed it; the dialog and
  support-copy tests both passed on rerun. These are 14 distinct passing workflows
  across runs, not a single clean 14-test run.
- Desktop, tablet and mobile screenshots were visually inspected for home,
  conversation, sign-in and detail panels. No horizontal overflow was found at
  the four tested widths. The narrowest header wraps intentionally.
- A separate walkthrough in the visible app on port 5173 sent a minimum-payment
  question through Luna and received a sourced AI answer explaining that interest
  remains payable. This also exercised the mobile composer and source control.

The sign-in checks cover navigation, fields and show/hide password only. No login
was submitted and no authentication fixture or customer record was created.
Two-customer Supabase isolation and broad complex-query reliability remain deferred.
OpenRouter was not re-tested. These browser checks used Chromium; they do not
establish coverage of every browser or physical mobile keyboard.

## Files

`frontend/src/Chat.jsx`, `Login.jsx`, `App.jsx`, `index.css`,
`components/Icon.jsx`, `components/Panel.jsx`, and `frontend/index.html`.
Browser checks are in `frontend/tests/chat.spec.js` and `live.spec.js`.

Changes remain local and unpushed.
