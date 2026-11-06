# How UBS explains client topics (research for DemoBank articles)

DemoBank is a fictional coursework bank. To write clearer client guidance, we read how one real private bank, UBS, explains the same topics on its public website. We only read public pages: no logins and no forms. All checks were made on **2 Nov 2026**. Everything below is our own paraphrase. Nothing here is UBS policy for DemoBank, and no DemoBank article names UBS, copies its wording or uses its contact details, fees or limits.

**How to read this:**
- UBS has few Singapore client-help pages. Where no Singapore page exists, we used Swiss or global pages and say so. A few details come from Indian or Australian forms; those follow local regulators' rules and are not general UBS practice.
- Pages were read with an automated fetch tool that summarises each page. Before quoting anything from a page, check the original.
- Each topic ends with **Informs**: the DemoBank article it shaped. The articles are in migration `0098` and `backend/app/services/knowledge.py`.

## 1. Periodic client review (KYC)

| Page | Jurisdiction |
|---|---|
| https://www.ubs.com/global/en/wealthmanagement/financial-intermediaries/2022/regulatory-changes-in-switzerland.html | Switzerland |
| https://www.ubs.com/global/en/legal/privacy/singapore.html (links to the Singapore client privacy notice, updated Oct 2026) | Singapore |

**What UBS says:**
- Since revised anti-money-laundering rules took effect in Switzerland in 2023, every client relationship gets regular know-your-client reviews.
- A review checks both the client's basic data and their documents. Outdated or missing documents are replaced, under deadlines set by the regulator.
- UBS sends a summary of the data it holds and asks the client to confirm it on time.
- The Singapore privacy notice says compliance checks happen at onboarding and then continue throughout the relationship, including risk rating.
- Neither page says what happens when a client does not respond.

**Informs:** `review-status` (new):
- `why-review`: a review is routine and a legal duty that applies to everyone, not a sign of suspicion.
- Late items slow the review, and only the bank decides next steps.
- The article invents no consequences beyond that.

## 2. Proof of address and certified copies

No Singapore or Swiss page lists the proof of address UBS accepts. The only concrete list we found is in a UBS India account-opening pack (PDF), which follows Indian rules. Its full URL was not recorded, so treat it as background only. An Australian UBS Asset Management form (PDF, Sept 2026) explains certified copies.

**What the forms say:**
- Accepted proof includes utility bills and bank statements, with a recency limit of a few months.
- The name and address must match the client's own details.
- A document in another language needs an English translation.
- A certified copy is checked against the original by an authorised person (for example a lawyer or notary). That person writes that it is a true copy, then signs and dates it with their name and role.

**Informs:**
- `review-documents#proof-of-address`: document types, name must match, recency limit. The 3-month limit is DemoBank's own rule from `document_templates.py`, recorded in `kb.facts`.
- `review-documents#identity`: what a certifier writes on a copy.
- `document-uploads#legibility`: certified English translation.

## 3. Source of wealth and source of funds

| Page | Jurisdiction |
|---|---|
| UBS Asset Management application form, PDF dated 28 Sept 2026 (full URL not recorded) | Australia |
| https://www.ubs.com/global/en/our-firm/fatca.html (context only) | Global |

**What UBS says:**
- The form separates two ideas. Source of funds is where the money for one particular investment came from. Source of wealth is how the client built their overall wealth, meaning the work or business behind it.
- It offers common sources to choose from: savings, salary or bonus, business, investments, gift, inheritance, sale of assets.
- We found no ubs.com page explaining to clients why the bank asks, or which supporting documents it wants.

**Informs:** `review-documents#source-of-wealth`. Its first sentences give a two-line definition contrasting the two terms, a list of common sources, and the level of detail that helps.

## 4. Tax residency self-certification (CRS and FATCA)

| Page | Jurisdiction |
|---|---|
| https://www.ubs.com/global/en/our-firm/fatca.html | Global |
| https://www.ubs.com/global/en/our-firm/fatca/fatca-aei-switzerland.html | Switzerland |

**What UBS says:**
- FATCA is a US law.
- Automatic exchange of information follows the OECD Common Reporting Standard. Both aim to stop cross-border tax evasion.
- Banks record each client's tax residence and, where required, report account details to the local tax authority, which shares them with partner countries.
- Which form applies depends on the type of client.
- The pages say they are not tax advice and send unsure clients to a tax adviser.
- The Australian form also asks clients to report changes to their tax details within a fixed period.

**Informs:** `review-documents#tax-residency`:
- why the form exists, in one sentence;
- list every country, with its tax number;
- tell the bank when your residency changes. No period is given, because none is approved for DemoBank.
- DemoBank cannot give tax advice.

## 5. Answering the bank's questions and sending documents

| Page | Jurisdiction |
|---|---|
| https://www.ubs.com/sg/en/wealthmanagement/digital-banking/faq.html | Singapore |
| https://www.ubs.com/sg/en/wealthmanagement/digital-banking/faq/advisor-messaging.html | Singapore |
| https://www.ubs.com/sg/en/wealthmanagement/digital-banking/faq/security.html | Singapore |

**What UBS says:**
- Bank documents arrive in a mailbox inside digital banking and can be downloaded as PDFs.
- Clients can message their own advisor, including attachments, through a messaging service the advisor sets up. UBS warns about the risks of third-party apps.
- UBS states it does not ask for sensitive details in messages or calls the client did not expect.
- We found no public page on an upload portal, file formats or size limits.

**Informs:**
- `bank-questions` (new): one official place to answer, specific answers, never include secrets.
- `document-uploads` (new): formats, size and legibility. These come from DemoBank's own upload rules (migration `0051`, `schemas/document_uploads.py`), not from UBS.

## 6. Updating personal details

| Page | Jurisdiction |
|---|---|
| https://www.ubs.com/ch/en/help/my-data/change-address.html | Switzerland |
| https://www.ubs.com/ch/en/help/my-data/notifications.html | Switzerland |
| https://www.ubs.com/sg/en/wealthmanagement/digital-banking/faq/security.html | Singapore |

**What UBS says:**
- Clients change their address in the app or e-banking and confirm the change with strong authentication. A signed letter or a branch visit also works.
- Address changes are not accepted by email or through online forms.
- Moving abroad is reported separately.
- The bank sends a confirmation after any change to contact details, and tells clients to call at once if they did not make the change.
- The Singapore page asks clients to tell the bank promptly about a new phone number or address.

**Informs:** `personal-details` (new):
- one official route (My requests → Update personal details) and not by email;
- the bank may confirm the change through the details it already holds;
- a new phone number matters because one-time codes go to the registered phone;
- moving to another country may change your tax residency.

## 7. Reminders and notifications

| Page | Jurisdiction |
|---|---|
| https://www.ubs.com/ch/en/help/my-data/notifications.html | Switzerland |
| https://www.ubs.com/sg/en/wealthmanagement/digital-banking/faq.html | Singapore |

**What UBS says:**
- UBS notifies clients after every change to their contact details.
- In Singapore, clients can opt in to alerts when a new document arrives.
- We found no public page on reminders for outstanding review items.

**Informs:** `review-reminders` (new). It describes DemoBank's own behaviour:
- reminders are service messages;
- they clear when nothing is left to send (migrations `0078`–`0079`);
- what to do when you cannot meet a deadline.

## 8. Relationship manager model

| Page | Jurisdiction |
|---|---|
| https://www.ubs.com/sg/en/wealthmanagement/what-we-offer/private-client.html | Singapore |
| https://www.ubs.com/sg/en/wealthmanagement/digital-banking/faq/advisor-messaging.html | Singapore |
| https://www.ubs.com/sg/en/wealthmanagement/digital-banking/faq.html | Singapore |

**What UBS says:**
- The client advisor is the client's personal partner for planning.
- Clients reach their own advisor through a messaging service the advisor sets up, and the advisor can add colleagues to the conversation.
- There is a separate digital banking hotline for login and technical questions, and a separate emergency line for suspected unauthorised access. The advisor is the fallback when that line cannot be reached.
- We found no client-facing page on what happens when an advisor changes.

**Informs:** `rm-working` (new):
- what the RM does;
- that other teams make the decisions;
- the ways to reach your RM;
- a separate urgent route for lost cards and unrecognised charges.

`rm-change` is unchanged: migration `0062` pins its v1 sections for the relationship introduction, and UBS has no public page on advisor changes to learn from.

## 9. Card security, scams and one-time codes

| Page | Jurisdiction |
|---|---|
| https://www.ubs.com/sg/en/wealthmanagement/digital-banking/faq/security.html | Singapore |
| https://www.ubs.com/global/en/our-firm/cybersafe/phishing.html | Global |
| https://www.ubs.com/ch/en/services/digital-banking/security/types-of-fraud.html | Switzerland |
| https://www.ubs.com/ch/en/help/debitcard/lost-stolen-card.html | Switzerland |
| https://www.ubs.com/ch/en/help/creditcard/secure-payment.html | Switzerland |
| https://www.ubs.com/ch/en/help/debitcard/notify-abuse.html | Switzerland |
| https://www.ubs.com/ch/en/help/creditcard/notify-abuse.html | Switzerland |

**What UBS says:**
- It lists what it will never ask for: PINs, login details or codes by phone, email or SMS, and links to sign-in pages.
- It names common scams: callers pretending to be the bank or the police, fake technical support asking for remote access, and requests for SMS codes.
- Warning signs it lists are urgency, unusual senders and web addresses that are slightly wrong.
- Its advice:
  - hang up and call back on an official number;
  - approve in the app only actions you started yourself;
  - if you clicked a link or shared details, contact the bank at once, change your credentials and check your statements.
- The Swiss card pages:
  - say to block a lost card immediately in the app;
  - distinguish a temporary freeze for a misplaced card from a block;
  - explain how to report a charge you do not recognise;
  - note that temporary holds (hotels, fuel, car hire) are not final charges.

**Informs:** `security` v2, which now has three sections: `never-share`, `warning-signs` and `if-shared`.
- Not used yet: the note on temporary holds and the freeze-versus-block distinction. They would suit a later `unrecognised-charge` or `lost-card` version.
- The card articles were left unchanged in this round. Their wording is pinned by the fraud-team flow tests.

## 10. Statements and e-banking

| Page | Jurisdiction |
|---|---|
| https://www.ubs.com/ch/en/help/e-banking/edocuments.html | Switzerland |
| https://www.ubs.com/ch/en/help/digital-bank-documents/find-and-manage-dbd.html | Switzerland |
| https://www.ubs.com/ch/en/help/account/order-statement-of-assets.html | Switzerland |
| https://www.ubs.com/sg/en/wealthmanagement/digital-banking/faq.html | Singapore |

**What UBS says:**
- Clients choose electronic delivery in settings.
- Documents sit in one searchable area, and clients can download them.
- Documents are kept for a stated number of years.
- If documents do not appear after switching, clients should contact the bank.
- Some documents, such as statements of assets and confirmations, can be ordered online.

**Informs:** `statements` v3, now split into three sections:
- `monthly`: how to view and print;
- `older-copies`: older, certified or paper copies, and bank letters;
- `missing`: why the current month or a pending payment is not shown.

No retention period was added, because DemoBank has none approved.

## 11. Local and international transfers

| Page | Jurisdiction |
|---|---|
| https://www.ubs.com/ch/en/services/guide/accounts-and-cards/articles/international-money-transfers.html (dated 17 Jul 2025) | Switzerland |
| https://www.ubs.com/sg/en/wealthmanagement/digital-banking/faq/account-transfers.html | Singapore |
| https://www.ubs.com/ch/en/help/account/payment-tracking.html | Switzerland |
| https://www.ubs.com/ch/en/help/e-banking/beneficiary.html | Switzerland |
| https://www.ubs.com/ch/en/services/digital-banking/security/business-e-mail-compromise.html | Switzerland |

**What UBS says:**
- **Details needed:** international transfers need the recipient's name, account or IBAN, BIC/SWIFT, the bank's name and address, and the currency. The recipient's account should accept that currency.
- **Who pays charges:** the sender pays all, the recipient pays all, or the charges are shared.
- **Timing:** transfers run on business days. One confirmed after the currency's cut-off goes the next business day. Reaching the recipient's bank is not the same as being credited.
- **Mistakes:** a transfer can be changed until it is executed. After that, the bank can only ask the receiving bank to return the money.
- **Scams:** if someone says a payee has new bank details, confirm them by calling a number you already have, never by replying to the message.

**Informs:** `overseas-transfers` v2, now split into four sections:
- `how-to`;
- `details-needed`;
- `fees-timing`: the one-to-three business days already approved, now also in `kb.facts`;
- `safety`: changed bank details and a transfer sent to the wrong place.

## 12. Lending for private clients

| Page | Jurisdiction |
|---|---|
| https://www.ubs.com/sg/en/wealthmanagement/digital-banking/faq/lombard-loans.html | Singapore |
| https://www.ubs.com/global/en/wealthmanagement/insights/chief-investment-office/life-goals/liquidity-longevity-legacy/2021/borrow-benefits-and-considerations.html | Global |
| https://www.ubs.com/ch/en/services/guide/mortgages-and-financing/articles/documents-for-mortgage-loan.html | Switzerland |

**What UBS says:**
- A loan against investments (a Lombard loan) is secured on cash, shares, bonds or funds.
- The bank alone decides which assets count and how much can be lent against them, and can change this.
- If the collateral falls in value, the client may have to repay or add collateral, or the bank may sell it.
- New loans go through the advisor, not digital banking.
- Mortgage applications follow a set path: a document checklist, a meeting, the bank's credit check, then a property valuation.

**Informs (7 Nov 2026, migration 0115):** `lombard-financing` v1 and `home-loans` v2, using the structure in §13; no lending values, rates or periods.

## 13. What a Singapore private client is offered (checked 7 Nov 2026)

| Page | Jurisdiction |
|---|---|
| https://www.ubs.com/sg/en/wealthmanagement/_jcr_content/root/headercontainer.full-megamenu.html | Singapore |
| https://www.ubs.com/sg/en/wealthmanagement/what-we-offer/financing/financing-solutions.html | Singapore |
| https://www.ubs.com/sg/en/wealthmanagement/what-we-offer/financing/mortgage-solutions.html | Singapore |
| https://www.ubs.com/sg/en/wealthmanagement/digital-banking/faq.html | Singapore |

**What UBS says:**
- The Singapore wealth management offer is planning (wealth planning, trusts, family advisory), investing (managed and advised portfolios, sustainable and alternative investments), financing (financing solutions, mortgage solutions) and digital banking. No personal or car loans are listed.
- **Financing solutions:** Lombard credit secured on a portfolio (equities, funds, bonds, cash deposits): a variable loan facility (draw as needed, interest on the amount used) and a fixed term advance (a set amount at a rate fixed for the period); a bank guarantee. If collateral falls below a set limit, the bank may ask for more collateral or repayment and may sell investments.
- **Mortgage solutions:** property financing and refinancing as a borrowing plan prepared with mortgage planning specialists, third-party valuation and conveyancing, subject to credit approval; an uncommitted mortgage with a variable rate and no early repayment penalty. No document list or rates are published.
- **Digital banking:** advisor messaging, a document mailbox kept for 10 years, alerts (balances, transactions, orders, mailbox), FX and securities trading, research.

**Informs:** the storyline's products (`docs/workflows/storyline-michelle.md`): Michelle's advised portfolio and time deposit (0116), the financing articles (0115), the `financing_specialist` request type and the meeting "Property financing options" (0117). DemoBank's articles invent no figures and name no bank.

## 14. AI that UBS describes publicly (checked 7 Nov 2026)

From UBS's own US advisor page and press coverage (finews, Financial Planning; one aggregator, bankingnewsai.com, to be checked against its sources): US advisors use STAAT Insights (pre-meeting briefings, about 400 insight scenarios), a scheduling agent, an advisor knowledge chatbot and AI meeting notes; UBS Switzerland rolled out a meeting-preparation tool in 2026 and runs a client chatbot that passes a summary to an employee when it hands over; UBS says AI does not make investment recommendations. Nothing AI-specific is published for Singapore, where clients reach their advisor through Advisor Messaging (WhatsApp or WeChat). **Informs:** the storyline's positioning (one connected client story across these moments) and its metric benchmarks.

## Gaps in UBS's public pages

UBS has no public client page for any of these:
- what happens when a client does not answer a review;
- proof-of-address rules for Singapore;
- supporting documents for source of wealth;
- upload formats and size limits;
- reminders for outstanding review items;
- advisor changes.

DemoBank's articles on these topics rely on DemoBank's own system rules (document templates, upload limits, reminder clean-up) and say nothing beyond them.
