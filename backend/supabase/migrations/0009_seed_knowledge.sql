-- Initial hosted knowledge content mirrors the approved 2026-10-12 in-memory library.
-- Source summaries, scope and review dates are preserved. No invented fee/rate facts are seeded.
-- This migration is prepared and tested locally; hosted application and API integration remain pending.

insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('accounts',1,'Demo account information',array['retail'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('accounts',1,'main','Demo account information','DemoBank is a fictional coursework bank. General guidance is available without login. Sign in to view your synthetic account balances and recent transactions. The demo does not provide live balances. Specific account fees, interest rates and limits are unavailable unless supplied in an approved product record.','account balance saving current interest fee limit');

insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('loans',1,'Demo loan enquiries',array['retail'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('loans',1,'main','Demo loan enquiries','Loan product records describe eligibility and application steps. Any comparison with a synthetic customer profile is preliminary guidance only. Ask for missing details instead of treating them as eligible. Only the bank can decide on an actual application. The chatbot cannot submit an application or promise approval. This is general information, not advice or a contract.','loan mortgage borrow eligibility eligible application income repayment');

insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('cards',1,'Demo credit card enquiries',array['retail','fraud'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('cards',1,'main','Demo credit card enquiries','Use the matching credit card product record for fees and terms. If no product record is supplied, its terms cannot be looked up in this chat. For a card that is not working, ask whether the problem concerns an ATM withdrawal, online payment or shop payment and what error appeared. Do not invent an amount or use another card product. Personal card limits, balances and due dates require sign-in and are synthetic. Available credit cannot be calculated without sufficient records. Fee guidance is general information, not advice or a contract.','card credit fee payment due limit interest late');

insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('statements',1,'Finding a statement',array['retail','document'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('statements',1,'main','Finding a statement','The chatbot cannot open or download your statement. For a real statement, use your bank’s official app or website and look for its statement or document service. Exact navigation varies by bank; contact verified support if you cannot find it. Do not upload a real statement to this demo.','statement download document');

insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('transactions',1,'Failed pending and unfamiliar payments',array['retail','fraud'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('transactions',1,'main','Failed pending and unfamiliar payments','Failed means the payment did not complete; check the message in your official banking app before retrying. Pending means processing is not complete; check the status and avoid duplicate payments. Exact release or completion times are unavailable here. If a transaction is unfamiliar, treat it as possible fraud and contact your bank immediately through its official app or website. The chatbot cannot reverse or refund payments.','transaction declined failed pending unfamiliar fraud payment decline');

insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('lost-card',1,'Lost or stolen card guidance',array['fraud'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('lost-card',1,'main','Lost or stolen card guidance','Report a lost or stolen card to your bank immediately through its official app or website. If the official app offers a freeze or block control, use that control. Review recent activity and report unfamiliar transactions. The chatbot cannot block a card or confirm a block. Do not wait for a reply here if your real card is at risk. Never share your PIN, password, OTP, CVV or full card number.','lost stolen missing card block freeze');

insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('security',1,'Protecting private information',array['fraud'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('security',1,'main','Protecting private information','Never share passwords, PINs, OTPs, CVVs or full card numbers in chat. Ignore anyone asking for these details in chat. Sign in only through the separate sign-in form. If you already disclosed a secret, stop sharing it and use your bank’s official security or recovery service. This demo uses best-effort text filtering and is not a place for real private banking data.','password pin otp cvv security secret login sign in');

insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('support',1,'Getting human support',array['retail','fraud','compliance','document','relationship'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('support',1,'main','Getting human support','This demo has no staffed support queue. You can review, edit and copy a summary for your own use. Copying a summary does not send a ticket or contact a person. For a real issue, contact your bank using its official app or website. The chatbot cannot make payments, close accounts, dispute charges or change banking records.','human person support staff contact close dispute transfer refund');

insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('sg-accounts',1,'Singapore bank account basics',array['retail'],'MoneySense','General Singapore banking education, summarised by this project. Not a DemoBank product policy. Confirm your own bank’s terms.','external','published','https://www.moneysense.gov.sg/understanding-bank-accounts/','2026-10-12','2027-01-12');
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('sg-accounts',1,'main','Singapore bank account basics','Savings and current accounts can be used for everyday payments. Fixed deposits hold money until a maturity date. Withdrawing a fixed deposit early may reduce or remove interest and may incur a fee. Before opening an account, check its minimum deposit, minimum balance, fees, withdrawal limits and interest terms. Ask the bank which identification and supporting documents it needs. Product terms differ between banks.','fixed deposit maturity early withdrawal savings current account open an account minimum balance');

insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('sg-card-payments',1,'Singapore credit card payments',array['retail'],'MoneySense','General Singapore banking education, summarised by this project. Not a DemoBank product policy. Confirm your own bank’s terms.','external','published','https://www.moneysense.gov.sg/understanding-credit-cards/','2026-10-12','2027-01-12');
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('sg-card-payments',1,'main','Singapore credit card payments','A credit card lets you borrow up to a limit set by the issuer. Paying only the minimum leaves an unpaid balance that incurs interest; interest can also apply to new purchases. Check your statement for the payment due date and arrange payment so the bank receives it on time. Review the transactions and report anything you do not recognise to the bank immediately. Exact minimum payments, interest rates and fees depend on your card terms and are not supplied here.','minimum payment minimum sum unpaid interest credit card payment due late fee');

insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('sg-loan-types',1,'Singapore loan types',array['retail'],'MoneySense','General Singapore banking education, summarised by this project. Not a DemoBank product policy. Confirm your own bank’s terms.','external','published','https://www.moneysense.gov.sg/understand-the-types-of-loans/','2026-10-12','2027-01-12');
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('sg-loan-types',1,'main','Singapore loan types','A secured loan uses an asset as collateral. If the borrower does not repay, the lender may repossess and sell that asset. An unsecured loan has no pledged collateral and tends to have a higher interest rate. A term loan is repaid in instalments over a set period. Revolving credit can be drawn again within an agreed limit after repayment. These descriptions do not determine eligibility, approval or the terms of a particular loan.','secured unsecured collateral term loan revolving instalment loan types');

insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('sg-card-disputes',1,'Singapore card payment disputes',array['retail','fraud'],'Association of Banks in Singapore','General Singapore banking education, summarised by this project. Not a DemoBank product policy. Confirm your own bank’s terms.','external','published','https://www.abs.org.sg/consumer-banking/consumers/credit-card-chargeback-dispute-resolution','2026-10-12','2027-01-12');
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('sg-card-disputes',1,'main','Singapore card payment disputes','A card dispute can concern an unauthorised transaction or a problem with goods or services purchased. Contact the card-issuing bank promptly using its official customer service channel. It can explain the process and may require a dispute form and supporting documents. For goods or services that were not delivered, keep the agreed delivery details and correspondence with the merchant. Deadlines and outcomes depend on the bank, card scheme and circumstances; this chat cannot promise a refund or lodge a dispute.','dispute chargeback unauthorised unauthorized non-delivery not delivered merchant refund');

