# Client persona (single, 28 October 2026)

There is one client persona. The relationship manager is not a separate persona in the client view: the assistant hands questions to the RM, and the RM answers through **My requests**.

## Michelle Ng — established private client

| | |
|---|---|
| Profile | Self-employed business owner, long-standing client. Busy; reads the chat on her phone between meetings. Synthetic customer 1002. |
| Situation | Her periodic compliance review is open (due 30 Nov 2026). The bank has asked for proof of address, bank statements for the last 3 months, a source-of-wealth declaration and a tax residency self-certification. She also has everyday questions about her accounts and her card ending 7310. |
| Goals | Get short, clear answers any time without calling the bank. Know exactly which documents are still needed and whether a file is right **before** sending it. Reach her relationship manager when something needs a person, without repeating herself. |
| Frustrations | Long, robotic replies full of codes and disclaimers. Uploading a document only to hear weeks later that it was the wrong one. Being told "contact the bank" with no follow-up. |
| What she uses | Assistant chat · Compliance review (checklist, upload, quick check, submit) · My requests (references and status) · Inbox · Records and Actions |

## What the assistant does for her

1. **General questions (within guardrails).** Accounts, cards, loans, transactions and banking concepts, from her own records and approved knowledge. No invented figures, no personalised investment, legal or tax advice.
2. **Compliance review.** Explains what is still needed and by when; each requested document shows what it must contain with a made-up example; every upload gets a quick template check (name, Singapore address and postal code, recent date, statement months, signature) before it goes to the bank.
3. **Handover to her RM.** When a question needs a person (advice, fee or rate requests, complaints, "talk to my RM", or anything the assistant can't answer reliably), the assistant says so kindly, passes the question to her RM with the chat context, and gives her a `TCK-` reference to follow in My requests.
4. **Urgent safety.** A lost card or an unrecognised charge still gets the fixed safety steps and the in-chat card block; fraud goes to fraud operations, not the RM.

## Guardrails (acceptance criteria for every reply)

- Answers first, in plain words, usually under 80 words, with one next step.
- Never shows internal codes, never invents a figure, date, fee or reference.
- Never claims to have done something the records don't show; only the bank accepts documents or decides requests.
- Never asks for a PIN, password, OTP or full card number.
- "Talk to a person" is always available and goes to her RM.
