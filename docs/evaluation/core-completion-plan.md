# Core chatbot completion checks

Scope: backlog 6, 13, 14, 15, 16, 17 and 18. Work through each row in order.
This is a completion checklist, not a claim that all checks have passed.

Latest update — 14 October 2026: the focused implementation and verification
round is complete using real GPT-5.6 Luna responses. This is not acceptance of
every original backlog requirement. See [the reviewed results](core-results-review.md)
and [provider setup](../setup/codex-local-provider.md). OpenRouter needs its own showcase
recheck. The two deferred checks remain deferred. Changes are local and uncommitted.

| Step | Acceptance check | Current evidence / next action |
|---|---|---|
| 1. Minimum payment (17, 18) | A real browser answer explains that minimum payments leave interest-bearing debt, cites sg-card-payments, and adds no unsupported payment advice. Review the complete answer, not just keywords. | Done for the focused check. Real backend and browser replies reviewed against the source; correct unpaid-balance explanation and no invented rate. |
| 2. Sources (18) | Reject unknown citations, unsupported figures, changed explicit currencies/percentages and obvious attribution of external guidance as the bank's own policy. Accept supported examples. Review meaning manually in live browser answers. | Done for the agreed checks. Twenty unit regressions pass; real answers admit unknown fees and distinguish publisher scope. Source details verified in browser. General semantic correctness is not guaranteed. |
| 3. Natural conversation (6) | A small real-model browser set covers a typo, clarification, a topic change and frustration; replies address the latest message without article dumps. | Done for the focused set: real replies reviewed for typo, clarification, topic correction, frustration, abuse and greetings. Broad complex-query reliability stays deferred. |
| 4. Session memory (13) | Follow-ups and corrections use recent context. New chat and refresh clear it. Long histories retain the latest question and show the limit honestly. Retain the existing sign-out reset. | Session fixes and guest/browser checks pass; real follow-up and correction answers reviewed. Sign-out reset remains keyed to the user in App.jsx; its optional live login fixture was not rerun. Saved conversations remain outside this goal. |
| 5. Privacy and lost cards (14, 16) | Shared synthetic secrets are removed before transmission and from model output; combined secret/lost-card inputs still give urgent help. Emergency wording variants take the fixed safety path. Ordinary questions are not incorrectly blocked. | Fifteen shared privacy cases, twenty safety-pattern cases and expanded real browser checks pass. Detection now covers spaced codes, quoted phrases and selected Unicode variants. Not comprehensive protection. Two-customer live isolation remains deferred. |
| 6. Transparency and languages (15) | Preserve AI/safety/withheld labels, explain language scope and assess focused generated samples. | Notice and AI/safety labels browser-tested; withheld branch retained. Chinese, Malay and Tamil samples reviewed against the supplied English source. Chinese first timed out, then passed. This is sample evidence, not full multilingual acceptance. |
| 7. Evidence and planning (17) | Run relevant checks, inspect browser workflows, and update docs/planning/plan.docx with dated results, failures and deferred work. | Evaluation metadata fixed; publisher validation parity restored. Current 151 backend and 19 offline checks pass. Prior 21 frontend tests/build pass. Nineteen distinct browser workflows have passing evidence across runs. Raw evidence and reviewed results saved; plan updated. |

No fake AI clients or answer-generating substitutes may be used. Fixed safety
responses remain intentional. Provider failures are failures, never sample answers.

The two linked Supabase customers and broad complex-query reliability remain
deferred. UBS UI integration, saved conversations and multi-capability architecture
are outside this goal. Real staffed support and bank-specific product data are
also separate work.

## Local source-check work — 13 October 2026

Before: ten new rejection cases failed. Matching digits allowed USD to become
SGD, an amount to become a percentage, and unrelated record numbers to support a
currency amount. External guidance could also be described as the bank's policy.

After: all twenty new source regressions pass, including supported answers and
the connection to the normal answer validator. Currency is read from the same
JSON record, not borrowed from another account. No prompt version was edited.

Verification: 99 backend tests, 6 frontend utility tests, 17 offline validation
cases and the frontend production build pass. All six browser safety workflows
also pass against the normal backend/frontend, without model calls. The minimum-payment browser check
now also checks the unpaid-balance explanation and opens the original MoneySense
source; its real-model run remains pending, not passed.

Limits: these are conservative checks of explicit common currency codes,
symbols, percentages and a few English attribution patterns. They do not prove
semantic correctness, translated claims, omitted units, written-out numbers or
which account a matching figure describes. No fake model was used.

## Session and privacy work — 13 October 2026

The browser regression reproduced a source panel reopening after New chat.
Ignoring results from an earlier chat fixes it. Ten browser workflows now pass,
including request-history inspection after New chat/refresh, cancellation, privacy
checks before transmission, emergency variants and the language-scope notice.

Three initial privacy cases exposed missed spaced codes and altered characters.
Additional complete-token assertions cover partially removed phrases and codes.
The browser and backend now read the same fifteen synthetic privacy examples.
Normal financial questions and password-reset questions stay unchanged.

The final local suite has 134 backend tests, 21 frontend tests and 17 offline
validator cases passing, plus the production build. Real-model tests added this
round are not run. No source or prompt version was edited, no customer records
were changed, and no fake model is used.
