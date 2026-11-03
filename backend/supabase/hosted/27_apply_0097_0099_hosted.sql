-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0097$
declare previous supabase_migrations.schema_migrations; script text:=$source_0097$-- Released source SHA256: 553de0f62e21b1739cb13a229638493e4a126640d0b2cfe5ca8ae2c8cbd3c8b5
-- Knowledge search precision (retrieval evaluation, backend/evaluation/retrieval.py).
-- 0008 ORs every question word, so any shared body word matched: "Tell me a joke" returned four articles and only
-- 16% of questions with no approved article returned nothing. A section now matches only when at least one question
-- word is in its heading or keywords (weight A); body words still count towards the rank. A score threshold was
-- tried and rejected: unrelated questions scored up to 0.86 while correct matches scored as low as 0.29.
-- Keywords are search metadata only: cited headings and bodies are unchanged, so no article version is bumped.
create or replace function kb.search(p_query text,p_categories text[],p_k int)
returns setof kb.search_result
language plpgsql stable security invoker set search_path=''
as $$
declare query tsquery := ''::tsquery; token text;
begin
  if p_query is null or length(p_query)>4000 then
    raise exception 'knowledge query must be non-null and at most 4000 characters' using errcode='22023';
  end if;
  if p_k is null or p_k not between 1 and 20 then
    raise exception 'knowledge result limit must be between 1 and 20' using errcode='22023';
  end if;
  if p_categories is null or array_position(p_categories,null) is not null
      or not p_categories <@ array['retail','fraud','compliance','document','relationship']::text[] then
    raise exception 'knowledge categories must contain only registered domains' using errcode='22023';
  end if;
  foreach token in array tsvector_to_array(to_tsvector('simple',p_query)) loop
    query := query || plainto_tsquery('english',token);
  end loop;
  if numnode(query)=0 then return; end if;
  -- Weights are {D,C,B,A}: a non-zero rank with only A counted means a heading or keyword matched.
  return query select 'kb:'||a.article_id||'@v'||a.version||'#'||s.anchor,a.article_id,a.version,s.anchor,
    a.title,s.heading,s.body,a.publisher,a.scope,a.reference_url,a.checked_on,a.review_due,
    ts_rank_cd(s.search_vector,query,32)
  from kb.articles a join kb.sections s using(article_id,version)
  where a.status='published' and (a.kind='demo' or current_date between a.checked_on and a.review_due)
    and (cardinality(p_categories)=0 or a.categories && p_categories) and s.search_vector @@ query
    and ts_rank_cd('{0,0,0,1}'::float4[],s.search_vector,query)>0
  order by ts_rank_cd(s.search_vector,query,32) desc,a.article_id,s.anchor limit p_k;
end $$;

-- Customer wording for the published sections; broad words that pulled unrelated articles are removed
-- (support: close, dispute, transfer, refund; cards: payment, due, limit, interest, late).
update kb.sections s set keywords=k.keywords
from kb.articles a,(values
  ('cards','main','card credit fee declined decline not working atm online payment shop payment error'),
  ('support','main','human person real support staff contact handoff speak talk someone'),
  ('lost-card','main','lost stolen stole missing card block freeze wallet purse replacement'),
  ('app-login-recovery','steps','locked locked out lockout app mobile phone login log in sign in forgot password reset unlock access attempts'),
  ('sg-accounts','main','fixed deposit maturity matures early withdrawal savings current account open an account savings account minimum balance'),
  ('sg-card-payments','main','minimum payment minimum sum pay the minimum unpaid interest credit card payment due late fee bill'),
  ('sg-card-disputes','main','dispute chargeback unauthorised unauthorized non-delivery not delivered never delivered merchant refund'),
  ('overseas-transfers','main','overseas transfer international transfer send money abroad telegraphic swift iban wire remittance foreign'),
  ('transfer-timing','main','pending transfer transfers fast landlord rent payee delayed arrive resend send again on time stuck still going')
) as k(article_id,anchor,keywords)
where s.article_id=k.article_id and s.anchor=k.anchor and a.article_id=s.article_id and a.version=s.version and a.status='published';
$source_0097$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0097';
  if found then
    if previous.name is distinct from 'knowledge_search_precision' or previous.statements is distinct from array[script] then
      raise exception 'migration 0097 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0097 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0097','knowledge_search_precision',array[script]);
  end if;
end $apply_0097$;

do $apply_0098$
declare previous supabase_migrations.schema_migrations; script text:=$source_0098$-- Released source SHA256: 6c391747fcc7f2bedeb84c592c405b0f66f7fc89c4accd77dbf819db6cddd97b
-- Client guidance informed by research on how a private bank explains these topics (docs/research/ubs-practices.md).
-- All text is fictional DemoBank guidance (kind 'demo'), written in our own words; no other bank is named or quoted.
-- New: review-status, review-documents, document-uploads, bank-questions, review-reminders (compliance and document
-- guidance, none existed), personal-details (updating your address, phone or email) and rm-working (how to reach your
-- relationship manager). New versions: support v5 (which team handles what), security v2 (what DemoBank never asks for,
-- scam signs, what to do after sharing a code), statements v3 (older copies, why a month is missing) and
-- overseas-transfers v2 (details needed, fees and timing, changed bank details, a transfer sent to the wrong place).
-- rm-change v1 is unchanged: 0062 pins its sections for the relationship introduction.
-- Every figure is a DemoBank rule the app already enforces (document templates, upload limit, statements page) or
-- already approved text, and is recorded in kb.facts.

update kb.articles set status='retired' where article_id='support' and version=4 and status='published';
update kb.articles set status='retired' where article_id='security' and version=1 and status='published';
update kb.articles set status='retired' where article_id='statements' and version=2 and status='published';
update kb.articles set status='retired' where article_id='overseas-transfers' and version=1 and status='published';
insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('review-status',1,'Your compliance review status',array['compliance','document'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('review-documents',1,'Documents for your compliance review',array['compliance','document'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('document-uploads',1,'Uploading documents',array['document','compliance'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('bank-questions',1,'Answering the bank''s questions',array['compliance','document'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('review-reminders',1,'Review reminders',array['compliance'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('personal-details',1,'Updating your personal details',array['retail','compliance'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('rm-working',1,'Working with your relationship manager',array['relationship'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('support',5,'Getting human support',array['retail','fraud','compliance','document','relationship'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('security',2,'Protecting private information',array['fraud'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('statements',3,'Getting a statement',array['retail','document'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('overseas-transfers',2,'Sending money overseas',array['retail'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('review-status',1,'review-stages','The stages of a compliance review','Your Compliance review page shows one status for the whole review. Review opened: DemoBank has started your periodic check and will list what it needs. Documents requested: the list of documents and questions is ready for you. Documents partially received: the bank has some of the items, but others are still outstanding. Awaiting client documents: the bank is waiting for you to send the remaining items. Under review: the bank is checking what you sent; you do not need to do anything unless it asks you a question. Information requested: the bank has a question or needs a corrected document, shown at the top of the page. Completed: the bank has finished the review. Closed: the review is no longer active. Only the bank changes these statuses. The assistant can explain a status, but it cannot move it to the next stage or say how long the bank will take.','compliance review status stage progress under review documents requested partially received awaiting client documents information requested completed closed'),
  ('review-status',1,'item-status','Status of each requested document','Each requested item on your compliance review has its own status. Not prepared and Prepared are your own marks for keeping track; marking an item as prepared does not send anything to the bank. Submitted – awaiting review means you sent a file and the bank has not decided on it yet. Correction requested by bank means the bank looked at the file and needs a new or corrected one; the reason is shown with the item. Accepted by bank means the bank is satisfied with that item. Unknown means the status could not be read; reload the page or use Talk to a person. The automatic check that runs when you upload a file is preliminary and is labelled Not Formal Compliance Approval: it looks for things such as your name, a date and an address, but only the bank itself accepts a document.','document status item status submitted awaiting review correction requested accepted prepared not prepared preliminary check precheck checklist'),
  ('review-status',1,'why-review','Why DemoBank reviews your information','Like other banks, DemoBank must keep what it knows about its clients up to date, both under anti-money-laundering rules and to protect your accounts. It does this in a periodic review, also called a know-your-client (KYC) check. Every client is checked from time to time; it is routine and is not a sign that something is wrong. The bank may ask you to confirm your address, your tax residency and where your wealth comes from, and to send documents that show this. If items are still outstanding after the due date, the process takes longer and your relationship manager may contact you; the bank decides any next step. If you cannot send something in time, tell your relationship manager before the due date with Talk to a person.','periodic review kyc know your client routine reviewed'),
  ('review-documents',1,'proof-of-address','What counts as proof of address','A proof of address shows where you live now. DemoBank accepts a recent utility bill, telco bill, town council statement or bank statement that shows: your full name as it appears in your DemoBank records; your residential address in Singapore, including the postal code; the name of the company that issued it; and a bill or statement date within the last 3 months. A PDF downloaded from the provider''s website or app is easiest to read. A document addressed to someone else, an envelope on its own or a bill older than 3 months is not enough; download the latest one instead. If you have recently moved, update your address under My requests first, then send a proof of the new address.','proof of address address proof utility telco town council residential address postal code'),
  ('review-documents',1,'bank-statements','Statements your review needs','When your review asks for bank statements, send one statement for each of the last 3 full calendar months. Each statement must show your full name and the bank''s name. You can upload one PDF that combines the three months, or a single statement that covers all of them. Upload every page and do not crop out transactions; the bank needs the whole statement. Monthly statements for your DemoBank accounts are under Overview, then Statements.','bank statements for my review three months calendar months'),
  ('review-documents',1,'source-of-wealth','What a source of wealth declaration is','Source of wealth is how you built your overall wealth over time, for example salary and bonuses, a business you own or sold, the sale of a property, an inheritance or investments. It is different from source of funds, which is where the money for one particular payment or deposit came from. The source of wealth declaration on your review asks you to describe this in your own words. It must show your full name, where your wealth comes from, the date you completed it and your signature; sign and date it before you scan it or save it as a PDF. Give enough detail for the bank to follow, such as your work or business, roughly when, and any major events such as a property sale. The bank may ask for supporting documents, such as a sale contract or payslips, or ask a follow-up question.','source of wealth wealth declaration source of funds inheritance declaration form'),
  ('review-documents',1,'tax-residency','What a tax residency self-certification is','A tax residency self-certification is a signed form in which you tell DemoBank every country where you are resident for tax purposes, with your tax identification number (TIN) for each. Banks collect it under international rules for exchanging financial account information between tax authorities, such as the Common Reporting Standard (CRS) and the US FATCA rules. The form must show your full name, each country of tax residence, the date you completed it and your signature. List every country where you are tax resident, not only Singapore. If your tax residency changes later, for example after a move abroad, tell DemoBank and complete a new form. DemoBank and this assistant cannot tell you where you are tax resident or give tax advice; ask a tax adviser if you are unsure.','tax residency self-certification self certification tax form tin tax identification number crs fatca tax resident'),
  ('review-documents',1,'identity','Passport or identity card for your review','When your review asks for an identity document, send a copy of your passport or national identity card that shows your full name and an expiry date that has not passed. DemoBank needs a certified copy: a lawyer, notary or other authorised certifier checks the copy against the original, writes on it that it is a true copy, then signs and dates it with their name and role. Scan the photo page in colour as a PDF. If the document is not in English, add a certified English translation.','identity document passport nric identity card id card certified true copy notary identification'),
  ('document-uploads',1,'formats','File formats and sizes you can upload','You can upload a document as a PDF, a Word document (DOCX), or a JPEG or PNG image. Each file can be up to 10 MB. The file name must end with the matching extension: .pdf, .docx, .jpg, .jpeg or .png. Other types, such as HEIC photos from some phones, ZIP files or spreadsheets, cannot be uploaded; save or export the document as a PDF first. If a document has several pages, put them all in one PDF rather than uploading separate photos. A password-protected file cannot be checked, so remove the password before you upload.','file format formats file type pdf docx jpeg jpg png heic image photo size file size mb upload'),
  ('document-uploads',1,'legibility','Making your document easy to read','The bank can only accept a document it can read in full. 1. Where you can, download the original PDF from the issuer''s website or app instead of photographing a paper copy. 2. For a photo or scan, include the whole page with all its edges, lay it flat, use good light and avoid glare, shadows and blur. 3. Keep it in colour and do not crop, edit or cover any part of it. 4. Upload every page, including pages that look blank or only list terms. 5. Zoom in and check that names, dates and addresses are sharp. If the document is in another language, add a certified English translation. When the automatic check cannot read a file, it shows We could not check this file; upload a clearer copy.','legible readable clear blurry blurred photo scan scanned quality unreadable glare translation'),
  ('document-uploads',1,'how-to-upload','How to upload a document for your review','1. Open Compliance review. 2. Find the requested item and read what your file must show. 3. Choose the file and upload it. 4. Wait for the preliminary check; if it lists something to fix, fix it and upload again. 5. Send it to the bank. You get a receipt in Actions, and the item shows Submitted – awaiting review. Uploading a file does not by itself send it to the bank or accept it. You cannot delete or replace a file after you upload it; to correct one, upload a new file for the same item. Uploaded files are private to you and the bank. The assistant cannot upload, send or accept a document for you.','upload uploading submit attach upload a document for review'),
  ('bank-questions',1,'answer','How to answer a question on your review','When the bank needs more information during your review, the question appears at the top of Compliance review under The bank asked you, with the date to reply by, and you get a notice in your Inbox. 1. Read the question and any instructions. 2. Type your answer in the box and select Send to the bank. 3. If the question asks for a document, upload it for the matching item too. After you send it, the question shows that it was sent to the bank and is waiting for review, and the receipt is in Actions. The bank then accepts your answer or asks a follow-up question, which appears in the same place. Explain in chat asks the assistant what a question means; the assistant cannot answer for you or say whether the bank will accept your answer.','answer reply respond information request follow-up clarification'),
  ('bank-questions',1,'clear-answer','Writing a clear answer','Answer exactly what the bank asked, in plain words. Be specific: give dates, amounts and currencies, who sent or received the money, and where it came from, for example the sale of a property or a bonus from your employer. Say which document supports your answer and upload it for the matching item. If you cannot confirm something, say so rather than guessing. Do not include passwords, PINs, one-time codes or full card numbers; they are removed before your answer is saved. If you do not understand the question or cannot reply by the date shown, use Talk to a person to ask your relationship manager.','clear answer write explain specific'),
  ('bank-questions',1,'correction','Sending a corrected document','If the bank cannot accept a file, the item shows Correction requested by bank with the reason, and you get a notice in your Inbox. Read the reason, then upload a new file for the same item that fixes it, for example a more recent bill, the missing pages or a signed copy, and send it to the bank. The earlier file stays on record; you do not need to delete it. The correction request closes when you send the new file, and the item shows Submitted – awaiting review again.','correction corrected rejected resubmit reupload correction requested'),
  ('review-reminders',1,'why','Why you get review reminders','DemoBank sends reminders to your Inbox while something on your compliance review is still outstanding: requested items before their due date, or a question or correction from the bank that you have not answered yet. A reminder is a service message about your account; it is not a decision and not a sign that something is wrong. Because they are service messages, review reminders cannot be switched off while items are outstanding. They stop once you have sent everything that was asked for, and a reminder about a bank question clears once that question is answered. If the due date changes, reminders for the old date are cleared. If you think you have already sent everything, check each item on Compliance review: Submitted – awaiting review means the bank has it.','reminder reminders outstanding overdue inbox'),
  ('review-reminders',1,'deadline','If you cannot meet the review deadline','If you cannot send a document by your review deadline, tell your relationship manager before the due date with Talk to a person: say which item, why it is delayed and when you expect to have it. Send the items you already have in the meantime. Only the bank can agree to an extension or a later date; the assistant cannot extend a deadline or promise that a late item will be accepted. If the bank changes the due date, the new date appears on Compliance review.','review deadline extension extend late overdue more time'),
  ('personal-details',1,'how-to','How to update your address, phone number or email','To change your address, phone number or email address: 1. Open My requests and choose Raise a request. 2. Choose Update personal details. 3. Say what changed and give the new details. 4. For a new address, add a proof of the new address. The client records team checks the request and makes the change, and you can follow it under My requests. Your records do not change until the bank has made the change. DemoBank does not take changes of details by email or through a link in a message, and the assistant cannot change your details itself. The bank may contact you through the details it already holds to confirm a change, so update a phone number before the old one stops working.','update address new address moved house phone number mobile number email address personal details contact details'),
  ('personal-details',1,'proof','Proof for a new address or phone number','A new address needs a proof of address that shows your full name, the new residential address with its postal code, the company that issued it and a date within the last 3 months, such as a utility bill, telco bill or bank statement. If your compliance review is open when you move, the review needs a proof of your current address, so send the new one there too. A new phone number matters for security, because one-time codes are sent only to your registered phone. If you move to another country, your tax residency may change as well; tell DemoBank and complete a new tax residency self-certification.','moved proof of new address moving abroad moved overseas registered phone'),
  ('rm-working',1,'role','What your relationship manager does','Your relationship manager is your main contact at DemoBank. They know your accounts and the requests and appointments recorded for you, and they bring in other DemoBank teams when needed, such as the cards and credit team, the client records team or the compliance team. Your relationship manager passes on your requests and explains what the bank needs, but decisions such as approving a loan or a limit, accepting a review document or reversing a charge are made by the responsible team after its own checks. Your relationship manager''s name and contact details are in Records.','relationship manager rm role main contact responsibilities'),
  ('rm-working',1,'contact','How to reach your relationship manager','1. In the assistant, choose Talk to a person: you see a short summary, and approving it sends your question to your relationship manager with a reference to follow under My requests. A follow-up on the same topic is added to that request instead of opening a new one. 2. To meet, open My requests, then Meetings, and book one of the free times shown. 3. Use the contact details in Records. Do not use contact details from other sources or from unexpected messages. For a lost card or charges you do not recognise, do not wait for your relationship manager: call the fraud line on the back of your card or report it in the official app; the fraud team handles it as urgent.','contact reach rm relationship manager meeting meet book appointment'),
  ('support',5,'main','Getting human support','When you are signed in, Talk to a person shows a short summary; approving it passes your question to your relationship manager with a reference to follow in My requests. Charges you do not recognise and lost or stolen cards go to the fraud team as urgent instead. As a guest you can review, edit and copy a summary for your own use; copying does not send anything. For a real issue, contact your bank using its official app or website. The assistant cannot make payments, block or cancel cards, close accounts, dispute, refund or reverse charges, or change banking records.','human person real support staff contact handoff speak talk someone'),
  ('support',5,'which-team','Which team handles your request','Some requests go straight to the team that handles them. Under My requests, Raise a request lets you ask for a card limit change, a replacement for a lost, stolen or damaged card, a dispute about a charge you recognise, a statement or document copy, an update to your address, phone number or email, help with a payment or transfer that failed, is pending or went to the wrong place, help with signing in, or a question about your compliance review. Each request gets a reference and shows which team handles it and who decides. For a lost card or charges you do not recognise, call the fraud line on the back of your card or report it in the official app first; the fraud team treats it as urgent. For anything else, Talk to a person reaches your relationship manager.','which team raise a request request reference who handles'),
  ('security',2,'never-share','Passwords and PINs DemoBank never needs from you','DemoBank will never ask you for your password, PIN, one-time code (OTP), card security code (CVV) or full card number, whether by phone, email, SMS, chat or in person. It will never send you a link to sign in, ask you to install a remote-access app on your phone or computer, or ask you to move money to a safe account to protect it. Never share these details in this chat either; sign in only through the separate sign-in form or the official app. Anyone who asks for them is not DemoBank, even if the caller ID, email address or message looks genuine. This demo uses best-effort text filtering and is not a place for real private banking data.','password pin otp cvv security secret login sign in share'),
  ('security',2,'warning-signs','How to spot a scam call or message','Signs that a call, email or message may be a scam: it creates urgency or pressure, such as a threat that your account will be closed today; it asks you to keep it secret or to move to another app; it contains a link or attachment you did not expect, or a web address slightly different from the official one; it asks for a code, a password or a payment to a new account; or it asks you to approve something in the app that you did not start. If in doubt, hang up or do not reply, and contact the bank using the number on the back of your card or the official app, never the details given in the message. Only approve sign-ins, new payees and payments that you started yourself.','scam phishing fake call message sms link suspicious caller impersonation spot'),
  ('security',2,'if-shared','If you shared your details or clicked a scam link','Act straight away: 1. Stop: do not enter any more details or reply to the message. 2. Change your password in the official app with Forgot password, never through a link. 3. Call the fraud line on the back of your card, or report it in the official app, so the bank can protect your account and cards. 4. Check your recent transactions and payees and report anything you do not recognise. 5. If you lost money to a scam, also report it to the police. Do not wait for a reply in this chat. The assistant cannot block your access or cards or reverse a payment; only the bank can.','shared clicked link gave disclosed scammed'),
  ('statements',3,'monthly','How to view and print a statement','To get a monthly statement in DemoBank: 1. Open Overview. 2. Scroll to Statements. 3. Choose the account or card and the month. 4. Select View statement, then Print or save as PDF. Statements for the last three full months are available there. The assistant cannot open, download or send a statement itself. These are synthetic demo statements, not real bank documents.','statement statements download e-statement print pdf view'),
  ('statements',3,'older-copies','Older, certified or paper statements','For a month older than the last three full months, a certified copy, a paper statement or a bank letter, raise a Statement or document copy request under My requests. Give the account or card, the period and whether you want an e-statement or paper. The operations team prepares it, and you can follow the request under My requests. The assistant cannot prepare, certify or send a statement or letter itself.','old statement older statement certified copy paper statement letter copy of my statement'),
  ('statements',3,'missing','Why a month or a transaction is not on a statement','The Statements list shows full calendar months only, so the current month appears after it ends. A statement lists posted transactions only: a payment that is still pending appears on the statement for the month in which it posts. If an older month is not listed, raise a Statement or document copy request under My requests. If a transaction on a statement looks wrong or unfamiliar, report it; charges you do not recognise go to the fraud team.','missing statement not showing month'),
  ('overseas-transfers',2,'how-to','How to send money overseas','In this demo, you send money overseas from the official DemoBank app or website: choose Pay and transfer, then Overseas transfer, add the recipient, choose the currency and amount, and check the fees and exchange rate shown before you confirm. The assistant cannot send or confirm a transfer. These are synthetic demo terms, not a live bank service standard.','overseas transfer international transfer send money abroad telegraphic wire remittance foreign'),
  ('overseas-transfers',2,'details-needed','Details you need for an overseas transfer','Before you start, ask the recipient for: their full name and address as their bank holds them; their bank''s name and country; the bank''s SWIFT or BIC code; and their account number, or their IBAN in countries that use one. Check that the account can receive the currency you are sending; if it cannot, the receiving bank may convert the money or send it back. Copy the details from a source you trust and check them twice, because a transfer sent to the wrong account is hard to recover.','swift swift code bic iban recipient beneficiary overseas transfer international'),
  ('overseas-transfers',2,'fees-timing','Overseas transfer fees and arrival times','Before you confirm, the app shows the fees and the exchange rate for the transfer. It may also ask who pays the charges: you pay them all, the recipient pays them, or you each pay your own bank; when the recipient pays some charges, the amount that arrives can be lower than the amount you sent. Overseas (telegraphic) transfers usually arrive within one to three business days, depending on the countries and banks involved. Transfers are processed on business days, and one confirmed after the day''s cut-off time starts on the next business day. Reaching the recipient''s bank is not the same as being credited to their account, which their bank may take longer to do. These are synthetic demo terms, not a live bank service standard.','overseas international fees arrive arrival business days cut-off telegraphic'),
  ('overseas-transfers',2,'safety','Check before you send money overseas','Send money only to people you know, and never because someone in a call or message tells you to. If an email or message says that a person or company you pay has new bank details, confirm it by calling them on a number you already have, not one in the message. Large transfers, transfers to a trust or company, or moving funds between countries can be discussed with your relationship manager. If a transfer went to the wrong place, raise a Payment or transfer issue under My requests straight away; once a transfer is sent, the bank can ask the receiving bank to return it but cannot promise the money back. The assistant cannot send, stop or recall a transfer.','wrong account wrong recipient wrong transfer recall misdirected beneficiary changed scam');
insert into kb.facts(fact_id,value,unit,article_id,version,anchor) values
  ('review.proof_of_address_max_age','3'::jsonb,'months','review-documents',1,'proof-of-address'),
  ('review.statement_months','3'::jsonb,'months','review-documents',1,'bank-statements'),
  ('documents.max_upload_size','10'::jsonb,'MB','document-uploads',1,'formats'),
  ('statements.months_available','3'::jsonb,'months','statements',3,'monthly'),
  ('transfers.overseas_business_days','"1-3"'::jsonb,'business days','overseas-transfers',2,'fees-timing');

-- Point the request categories at the guidance clients see before raising them.
update support.request_categories set kb_article='personal-details' where category='personal_details_update' and kb_article is null;
update support.request_categories set kb_article='review-documents' where category='compliance_documents' and kb_article is null;
$source_0098$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0098';
  if found then
    if previous.name is distinct from 'client_guidance_articles' or previous.statements is distinct from array[script] then
      raise exception 'migration 0098 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0098 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0098','client_guidance_articles',array[script]);
  end if;
end $apply_0098$;

do $apply_0099$
declare previous supabase_migrations.schema_migrations; script text:=$source_0099$-- Released source SHA256: b28b80342437060c6df28a2447942bfd38eefb2c4336e4169948d4264eb490bc
-- Hybrid knowledge search (KNOWLEDGE_SEARCH=hybrid): keyword ranks from kb.search plus embedding similarity, fused.
-- Section vectors are written by the database owner with `python -m scripts.embed_knowledge` (its own embedding key);
-- app roles only read them. Each vector records its model and a hash of the embedded text (title, heading, body;
-- keywords are search metadata and are not embedded). Questions are embedded by the backend and passed in.
-- kb.search is unchanged and stays the keyword-only path. A section found only by similarity must reach
-- p_min_similarity, so a question no article covers can still return nothing (0097). No vector index: about 60
-- sections, so an exact scan is faster and returns exact neighbours.
do $check$
begin
  if exists (select 1 from pg_extension e join pg_namespace n on n.oid=e.extnamespace where e.extname='vector' and n.nspname<>'extensions') then
    raise exception 'pgvector is installed outside the extensions schema; run: alter extension vector set schema extensions';
  end if;
end $check$;
create extension if not exists vector with schema extensions;
grant usage on schema extensions to anon,authenticated;

create table kb.section_embeddings (
  article_id text not null,
  version int not null,
  anchor text not null,
  model text not null check (model ~ '^[a-z0-9][a-z0-9._/:-]{0,99}$'),
  content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
  embedding extensions.vector not null check (extensions.vector_dims(embedding) between 1 and 4096),
  embedded_at timestamptz not null default now(),
  primary key (article_id,version,anchor,model),
  foreign key (article_id,version,anchor) references kb.sections on delete restrict
);
alter table kb.section_embeddings enable row level security;
revoke all on kb.section_embeddings from public,anon,authenticated;
grant select on kb.section_embeddings to anon,authenticated;
create policy published_embeddings on kb.section_embeddings for select to anon,authenticated using
  (exists (select 1 from kb.articles a where a.article_id=section_embeddings.article_id and a.version=section_embeddings.version));

-- Reciprocal rank fusion (k=60) over the top 20 of each ranking; the fused score is the returned score.
create function kb.hybrid_search(p_query text,p_embedding real[],p_model text,p_min_similarity real,p_categories text[],p_k int)
returns setof kb.search_result
language plpgsql stable security invoker set search_path=''
as $$
declare query_vector extensions.vector;
begin
  if p_k is null or p_k not between 1 and 20 then
    raise exception 'knowledge result limit must be between 1 and 20' using errcode='22023';
  end if;
  if p_embedding is null or cardinality(p_embedding) not between 1 and 4096 or array_position(p_embedding,null) is not null then
    raise exception 'query embedding must hold 1 to 4096 numbers' using errcode='22023';
  end if;
  if p_model is null or p_model !~ '^[a-z0-9][a-z0-9._/:-]{0,99}$' then
    raise exception 'embedding model must be a model id' using errcode='22023';
  end if;
  if p_min_similarity is null or p_min_similarity not between -1 and 1 then
    raise exception 'minimum similarity must be between -1 and 1' using errcode='22023';
  end if;
  query_vector := p_embedding::extensions.vector;
  -- kb.search validates the query text and categories and applies the publication and review-date rules.
  return query
  with keyword as (
    select k.article_id,k.version,k.anchor,row_number() over (order by k.score desc,k.article_id,k.anchor) as rank
    from kb.search(p_query,p_categories,20) k
  ), nearby as (
    select e.article_id,e.version,e.anchor,1-(e.embedding operator(extensions.<=>) query_vector) as similarity
    from kb.section_embeddings e join kb.articles a using(article_id,version)
    where e.model=p_model and extensions.vector_dims(e.embedding)=cardinality(p_embedding)
      and a.status='published' and (a.kind='demo' or current_date between a.checked_on and a.review_due)
      and (cardinality(p_categories)=0 or a.categories && p_categories)
  ), semantic as (
    select s.article_id,s.version,s.anchor,s.similarity,row_number() over (order by s.similarity desc,s.article_id,s.anchor) as rank
    from nearby s order by s.similarity desc,s.article_id,s.anchor limit 20
  ), fused as (
    select article_id,version,anchor,(coalesce(1.0/(60+k.rank),0)+coalesce(1.0/(60+s.rank),0))::real as score
    from keyword k full join semantic s using(article_id,version,anchor)
    where k.rank is not null or s.similarity>=p_min_similarity
  )
  select 'kb:'||a.article_id||'@v'||a.version||'#'||sec.anchor,a.article_id,a.version,sec.anchor,
    a.title,sec.heading,sec.body,a.publisher,a.scope,a.reference_url,a.checked_on,a.review_due,f.score
  from fused f join kb.articles a using(article_id,version) join kb.sections sec using(article_id,version,anchor)
  order by f.score desc,a.article_id,sec.anchor limit p_k;
end $$;

create function public.kb_hybrid_search(p_query text,p_embedding real[],p_model text,p_min_similarity real,p_categories text[],p_k int)
returns setof kb.search_result language sql stable security invoker set search_path=''
as $$ select * from kb.hybrid_search(p_query,p_embedding,p_model,p_min_similarity,p_categories,p_k) $$;
revoke all on function kb.hybrid_search(text,real[],text,real,text[],int),public.kb_hybrid_search(text,real[],text,real,text[],int) from public;
grant execute on function kb.hybrid_search(text,real[],text,real,text[],int),public.kb_hybrid_search(text,real[],text,real,text[],int) to anon,authenticated;
$source_0099$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0099';
  if found then
    if previous.name is distinct from 'knowledge_hybrid_search' or previous.statements is distinct from array[script] then
      raise exception 'migration 0099 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0099 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0099','knowledge_hybrid_search',array[script]);
  end if;
end $apply_0099$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
