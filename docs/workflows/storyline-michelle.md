# Storyline: "Michelle says it once" (demo script, 7 Nov 2026)

One connected client story from her first question to the follow-up, aligned with how a private bank in Singapore
serves a wealth client (research: [ubs-practices.md](../research/ubs-practices.md) §12–14). DemoBank is fictional; no
page names UBS. Synthetic data only.

**Cast.** Michelle Ng (1002): advised portfolio (SGD 1.18m, including time deposit ****4410 maturing soon),
multi-currency account, periodic review in progress. Sophie Lim: her relationship manager.

**Problem statement.** A client's questions, documents, meetings and follow-ups are scattered across disconnected
channels, so relationship managers lack a single, source-linked view of her situation, priorities and history. They
spend too long piecing information together before each meeting, can't always respond with confidence, and lose time
that should go to high-value relationship work. Clients feel the same gap: routine tasks still need the bank's help, and
when a complex matter is handed over, only part of her history goes with it, so she often has to explain herself again.

## Before the demo (hosted project, SQL Editor)

1. Batch `42_apply_0115_0118_hosted.sql` once (on our hosted project: `42r_repair_0115_0118_hosted.sql` instead), then `embed_knowledge.sql`.
2. `34_reset_meeting_demo.sql` (meeting request, topics, grants, portfolio) and `41_eval_cleanup.sql`.
3. Backend with the matching code; `MEETINGS_ENABLED=true` for the notes and minutes part.

## Scenes and what to click

| Scene | Who | Steps | What it shows | Built in |
|---|---|---|---|---|
| 1. She helps herself | Michelle | Overview → Investments: her advised portfolio, including *Time deposit ****4410, matures …*. Overview → Statements → download September. My requests → review → upload the sale agreement (checked before submit). Chat: *"I'm thinking of buying a condo next year, can I meet Sophie about financing?"* → the assistant drafts **Property financing options** (Property financing, Investments), she confirms. | Routine tasks without staff; the moment is captured once (topic shared under her chat-topics setting) | existing; financing articles 0115 |
| 2. Sophie prepares | Sophie | Today: Michelle's request, the time deposit maturity and her review are ranked. Schedule → Requests → accept a time. Today shows "A briefing was prepared…"; **Briefing** opens the saved brief: who she is, products and portfolio, history, open matters, what she told us (topic from her chats as an AI suggestion), conversation starters; every point numbered to its source. **Approve version 1**. | One assembled, source-linked view; prepared before she asks; approved by exact version | 0118 Brief Writer, 0116 portfolio |
| 3. The meeting | Both | Video (Zoom link). Afterwards Schedule → **Notes and follow-up** → upload `demo-files/property-financing-call.vtt` (speakers *Sophie Lim* = RM, *Michelle Ng* = client) or type notes. Review the minutes (each action quotes the call), approve, select the follow-ups. | No note-taking from memory; nothing leaves without her approval | minutes 0120–0124 |
| 4. After the meeting | Sophie, Michelle | Share the recap. In the assistant: *"Document this meeting"* proposes a **Financing specialist referral** (goal, timeline, context) and a **Compliance review question** (Thai sale agreement); Sophie checks and sends both. Michelle's Inbox: the recap with her to-dos. | Hand-over inside the bank with context: the specialist and compliance never re-ask | 0117 request type |
| 5. Weeks later | Sophie | Run `44_demo_lombard_alert.sql` (an advance drawn, markets fall). Today: **Lombard collateral below its lending value** is a Do first item; the client page shows the shortfall. Open the next briefing: it includes the approved minutes and follow-ups. Optional: Michelle withdraws chat topics in Data sharing → the briefing shows *Removed by client on <date>*. | The story keeps building; consent is re-checked every time | 0116, 0118 |

Closing line (vision, not built): *"Next step: connect UBS Advisor Messaging, so requests clients send their RM on
WhatsApp or WeChat feed the same story. The hard part is built: what a client says becomes follow-ups and brief points
the RM approves; Advisor Messaging is one more source."*

## Metrics

| Side | Metric | How we measure it | Context | Prototype / pilot target |
|---|---|---|---|---|
| RM | Prep time per meeting | Gather a client's situation by hand vs. open the saved briefing | UBS US: about 10,000 advisor hours a month saved on meeting prep | One-client brief in about 48 s (R22); target: ready in under 10 min |
| RM | Time to document a meeting (minutes, follow-ups, recap) | Write all three by hand vs. upload transcript or notes, then approve | AI notes estimated to save 20–30 min a meeting | Target: approved in under 5 min |
| RM | Brief points with a source | Share of points citing a record, statement or note | Human review and citations relied on | 100% by design; uncited facts become gaps |
| Client | Hand-overs with full context | Hand-overs carrying her question, earlier messages and documents, no repeat question | UBS Switzerland passes a chat summary at hand-over | Target: over 90% |
| Client | Routine tasks without staff | Statement, document upload, meeting request completed in the app | UBS Switzerland's chatbot serves about 1.8M clients | All work end to end; target over 70% in a pilot |

Prototype results are on synthetic data; UBS figures are from its US and Swiss businesses (check the original articles).
