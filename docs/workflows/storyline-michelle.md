# Storyline: "Michelle says it once" (demo script, 7 Nov 2026; updated 11 Nov 2026)

One connected client story from her first question to the follow-up, aligned with how a private bank in Singapore
serves a wealth client (research: [ubs-practices.md](../research/ubs-practices.md) §12–14). UBS Connect is fictional; no
page names UBS. Synthetic data only. Since 11 Nov the story is a **Lombard loan** (she borrows against her advised
portfolio to buy a condo, so she keeps her investments) instead of a mortgage; the timed run, prompts and bank-team
scripts are in `docs/workflows/demo-end-to-end.md`.

**Cast.** Michelle Ng (1002): advised portfolio (SGD 1.18m, including time deposit ****4410 maturing soon),
multi-currency account, periodic review in progress (due 30 Nov 2026: the Bangkok property sale agreement to send,
proof of address with the bank). Sophie Lim: her relationship manager.

**Problem statement.** A client's questions, documents, meetings and follow-ups are scattered across disconnected
channels, so relationship managers lack a single, source-linked view of her situation, priorities and history. They
spend too long piecing information together before each meeting, can't always respond with confidence, and lose time
that should go to high-value relationship work. Clients feel the same gap: routine tasks still need the bank's help, and
when a complex matter is handed over, only part of her history goes with it, so she often has to explain herself again.

## Before the demo (hosted project, SQL Editor)

1. Batch `42_apply_0115_0118_hosted.sql` once (on our hosted project: `42r_repair_0115_0118_hosted.sql` instead), then `embed_knowledge.sql`.
2. `58_reset_storyline_demo.sql`: the one demo reset, in place of 34 + 41 + 51 (Michelle and Sophie back to the seed,
   Sophie's day re-dated from today). It leaves Michelle with no booked meeting, so she asks for one in scene 1. As
   Michelle, switch *Transaction insights* off on Data sharing afterwards: the reset shares them, and the scene 1
   switch appears only for a category she does not share yet.
3. Backend with the matching code; `MEETINGS_ENABLED=true` and the meeting worker for the notes and minutes part. Zoom
   is optional: a link needs `ZOOM_OAUTH_DIRECTORY` and a backend on macOS, Linux or WSL
   ([zoom-wsl.md](../setup/zoom-wsl.md)); without it the transcript is uploaded as a file.

## Scenes and what to click

| Scene | Who | Steps | What it shows | Built in |
|---|---|---|---|---|
| 1. She helps herself | Michelle | Home → Investments: her advised portfolio, including *Time deposit ****4410, matures …*. Home → Statements → download September. Periodic review → upload the sale agreement (checked before submit). Chat: *"I'd like to buy a condo next year without selling my investments. Can I meet Sophie by video to discuss a Lombard loan?"* → the assistant drafts a meeting request (her words as purpose, topics, up to 3 free times); she can switch on *Also share your transaction insights with Sophie until this request is answered*, then confirms. | Routine tasks without staff; the moment is captured once; sharing is her choice, for this request only | existing; financing articles 0115 (`lombard-financing`), switches 0114 |
| 2. Sophie prepares | Sophie | Today: Michelle's request and the time deposit maturity are ranked (her review joins Today from 16 Nov, when it is due within 14 days). Schedule → Requests → accept a time. Assistant: *"Give me an overview of Michelle Ng"*: portfolio, what she could borrow against it, her review and, because she shared it, the SGD 120,000 from her Bangkok sale. Today → **Briefing** opens the saved brief in the assistant: who she is, products and portfolio, history, open matters, what she told us (topic from her chats as an AI suggestion), conversation starters; every point numbered to its source. **Approve version 1**. | One assembled, source-linked view; prepared before she asks; approved by exact version | Brief Writer 0129/0134 (meeting side), 0116 portfolio |
| 3. The meeting | Both | Video (Zoom link, optional). Schedule → **Open meeting** → **Meeting started**, the call, **Meeting ended** → *Upload transcript* (a `.vtt` file from `docs/workflows/demo-files/`; speakers *Sophie Lim* = RM, *Michelle Ng* = client) or *Type my own notes*. Review the minutes (each action quotes the call), approve, select the follow-ups. A transcript is used only when Michelle agreed, on the meeting or once for every meeting on Data sharing (0149). | No note-taking from memory; nothing leaves without her approval | minutes 0120–0124, 0149 |
| 4. After the meeting | Sophie, Michelle | Share the recap. In the assistant: *"Document this meeting"* proposes a **Financing specialist referral** (goal, timeline, context) and a **Compliance review question** (Thai sale agreement); Sophie checks and sends both. Michelle's Inbox: the recap with her to-dos. | Hand-over inside the bank with context: the specialist and compliance never re-ask | 0117 request type |
| 5. Weeks later | Sophie | Run `44_demo_lombard_alert.sql` (an advance drawn, markets fall). Today: **Lombard collateral below its lending value** is a Do first item; the client page shows the shortfall. Open the next briefing: it includes the approved minutes and follow-ups. Optional: Michelle withdraws chat topics in Data sharing → the briefing shows *Removed by client on <date>*. | The story keeps building; consent is re-checked every time | 0116, 0129 |

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
