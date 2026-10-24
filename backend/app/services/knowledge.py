"""Versioned demo guidance and curated Singapore banking education."""
from dataclasses import dataclass
import re
from datetime import date
from urllib.parse import urlsplit

@dataclass(frozen=True)
class Article:
    id: str
    title: str
    content: str
    keywords: tuple[str, ...]
    version: str = '2026-10-12'
    publisher: str = 'DemoBank coursework project'
    reference_url: str | None = None
    checked_on: str | None = None
    review_due: str | None = None

    @property
    def notice(self):
        if self.reference_url:
            return 'General Singapore banking education, summarised by this project. Not a DemoBank product policy. Confirm your own bank’s terms.'
        return 'Fictional demo guidance; not a live bank policy.'


def is_current(article, today=None):
    if not article.reference_url:
        return True
    try:
        url = urlsplit(article.reference_url)
        checked = date.fromisoformat(article.checked_on)
        due = date.fromisoformat(article.review_due)
        return (url.scheme == 'https' and url.hostname in {'www.moneysense.gov.sg', 'www.abs.org.sg'}
                and not url.username and not url.password
                and checked <= (today or date.today()) <= due)
    except (TypeError, ValueError):
        return False

ARTICLES = [
    Article('accounts', 'Demo account information', 'DemoBank is a fictional coursework bank. General guidance is available without login. Sign in to view your synthetic account balances and recent transactions. The demo does not provide live balances. Specific account fees, interest rates and limits are unavailable unless supplied in an approved product record.', ('account','balance','saving','current','interest','fee','limit')),
    Article('loans', 'Demo loan enquiries', 'Loan product records describe eligibility and application steps. Any comparison with a synthetic customer profile is preliminary guidance only. Ask for missing details instead of treating them as eligible. Only the bank can decide on an actual application. The chatbot cannot submit an application or promise approval. This is general information, not advice or a contract.', ('loan','mortgage','borrow','eligibility','eligible','application','income','repayment')),
    Article('cards', 'Demo credit card enquiries', 'Use the matching credit card product record for fees and terms. If no product record is supplied, its terms cannot be looked up in this chat. For a card that is not working, ask whether the problem concerns an ATM withdrawal, online payment or shop payment and what error appeared. Do not invent an amount or use another card product. Personal card limits, balances and due dates require sign-in and are synthetic. Available credit cannot be calculated without sufficient records. Fee guidance is general information, not advice or a contract.', ('card','credit','fee','payment','due','limit','interest','late')),
    Article('statements', 'Finding a statement', 'The chatbot cannot open or download your statement. For a real statement, use your bank’s official app or website and look for its statement or document service. Exact navigation varies by bank; contact verified support if you cannot find it. Do not upload a real statement to this demo.', ('statement','download','document')),
    Article('transactions', 'Failed pending and unfamiliar payments', 'Failed means the payment did not complete; check the message in your official banking app before retrying. Pending means processing is not complete; check the status and avoid duplicate payments. Exact release or completion times are unavailable here. If a transaction is unfamiliar, treat it as possible fraud and contact your bank immediately through its official app or website. The chatbot cannot reverse or refund payments.', ('transaction','declined','failed','pending','unfamiliar','fraud','payment','decline')),
    Article('lost-card', 'Lost or stolen card guidance', 'Report a lost or stolen card to your bank immediately through its official app or website. If the official app offers a freeze or block control, use that control. The chatbot cannot block a card from this reply. For a suspicious charge in your signed-in synthetic records it can offer a temporary block, which runs only after you confirm it; it cannot cancel a card permanently. Review recent activity and report unfamiliar transactions. Do not wait for a reply here if your real card is at risk. Never share your PIN, password, OTP, CVV or full card number.', ('lost','stolen','missing card','block','freeze'), version='2026-10-24'),
    Article('security', 'Protecting private information', 'Never share passwords, PINs, OTPs, CVVs or full card numbers in chat. Ignore anyone asking for these details in chat. Sign in only through the separate sign-in form. If you already disclosed a secret, stop sharing it and use your bank’s official security or recovery service. This demo uses best-effort text filtering and is not a place for real private banking data.', ('password','pin','otp','cvv','security','secret','login','sign in')),
    Article('support', 'Getting human support', 'When you are signed in, Talk to a person shows a short summary; approving it records a support handoff for staff in this demo. The demo has no staffed queue, so no person will reply. As a guest you can review, edit and copy a summary for your own use; copying does not send anything. For a real issue, contact your bank using its official app or website. The chatbot cannot make payments, close accounts, dispute charges or change banking records.', ('human','person','support','staff','contact','close','dispute','transfer','refund','handoff'), version='2026-10-24'),
    Article('savings-fees', 'Savings account fees', 'The fictional DemoBank Everyday Savings account has no monthly account fee (SGD 0). A fall-below fee of SGD 2 per month applies when the average daily balance in a month is below SGD 1,000. These are synthetic demo terms, not a live bank price list.', ('savings','saving','acc','monthly fee','fee','fall-below','minimum balance'), version='2026-10-24'),
    Article('app-login-recovery', 'Mobile app login recovery', 'If you are locked out of the mobile app: 1. Close and reopen the official app and check that it is updated. 2. On the sign-in screen choose Forgot password and follow the steps; a one-time code is sent only to your registered phone. 3. Enter that code only in the official app, never in chat, email or a phone call. 4. If the app says your access is locked after several attempts, wait for the stated time or use the official app’s Help service to unlock it. 5. If you did not try to sign in yourself, treat it as a security issue and contact your bank through its official app or website. This chat never needs your password, PIN or one-time code.', ('locked','locked out','lockout','app','mobile','login','log in','forgot password','unlock'), version='2026-10-24'),
    Article('car-loans', 'Car loans', 'The fictional DemoBank offers car loans for new and used cars, repaid in fixed monthly instalments over an agreed term. A car loan is usually secured on the car. Interest rates, fees and maximum loan amounts are not supplied in this demo. Applicants generally need to be at least 21 years old, have a regular income and provide identification and proof of income. Meeting these general points does not mean an application will be approved. The bank makes the final decision on every application after its own checks. Apply through the official DemoBank app or website under Loans, then Car loan, or ask a relationship manager. The chatbot cannot submit an application or promise approval. This is general information, not advice or a contract.', ('car loan','car','auto loan','vehicle','car financing'), version='2026-10-24'),
    Article('unrecognised-charge', 'Unrecognised card charge', 'If you do not recognise a card charge, act now: 1. Contact your bank immediately through the official fraud line printed on the back of your card or the report function in its official app. 2. Ask for the card to be blocked if it may be compromised. 3. Check your other recent transactions. 4. Never share your PIN, password, one-time code or full card number. In this demo the assistant can offer a temporary block of a synthetic card, which runs only after you confirm it. It cannot investigate, dispute or reverse a charge. A temporary block stops new card payments but does not cancel the card or reverse any charge. Identity verification and permanent cancellation are done by bank staff. After staff confirm the card should be cancelled, the bank issues a replacement card with a new number to your registered address.', ('unrecognised','unrecognized','unfamiliar','dont recognise','don’t recognise','not mine','suspicious','fraud','charge','replacement card'), version='2026-10-24'),
]
# Summaries checked against the linked primary pages on 12 October 2026.
# Review dates are this project's maintenance schedule, not publisher guarantees.
ARTICLES += [
    Article('sg-accounts', 'Singapore bank account basics',
        'Savings and current accounts can be used for everyday payments. Fixed deposits hold money until a maturity date. Withdrawing a fixed deposit early may reduce or remove interest and may incur a fee. Before opening an account, check its minimum deposit, minimum balance, fees, withdrawal limits and interest terms. Ask the bank which identification and supporting documents it needs. Product terms differ between banks.',
        ('fixed deposit', 'maturity', 'early withdrawal', 'savings', 'current account', 'open an account', 'minimum balance'),
        publisher='MoneySense', reference_url='https://www.moneysense.gov.sg/understanding-bank-accounts/', checked_on='2026-10-12', review_due='2027-01-12'),
    Article('sg-card-payments', 'Singapore credit card payments',
        'A credit card lets you borrow up to a limit set by the issuer. Paying only the minimum leaves an unpaid balance that incurs interest; interest can also apply to new purchases. Check your statement for the payment due date and arrange payment so the bank receives it on time. Review the transactions and report anything you do not recognise to the bank immediately. Exact minimum payments, interest rates and fees depend on your card terms and are not supplied here.',
        ('minimum payment', 'minimum sum', 'unpaid', 'interest', 'credit card', 'payment due', 'late fee'),
        publisher='MoneySense', reference_url='https://www.moneysense.gov.sg/understanding-credit-cards/', checked_on='2026-10-12', review_due='2027-01-12'),
    Article('sg-loan-types', 'Singapore loan types',
        'A secured loan uses an asset as collateral. If the borrower does not repay, the lender may repossess and sell that asset. An unsecured loan has no pledged collateral and tends to have a higher interest rate. A term loan is repaid in instalments over a set period. Revolving credit can be drawn again within an agreed limit after repayment. These descriptions do not determine eligibility, approval or the terms of a particular loan.',
        ('secured', 'unsecured', 'collateral', 'term loan', 'revolving', 'instalment', 'loan types'),
        publisher='MoneySense', reference_url='https://www.moneysense.gov.sg/understand-the-types-of-loans/', checked_on='2026-10-12', review_due='2027-01-12'),
    Article('sg-card-disputes', 'Singapore card payment disputes',
        'A card dispute can concern an unauthorised transaction or a problem with goods or services purchased. Contact the card-issuing bank promptly using its official customer service channel. It can explain the process and may require a dispute form and supporting documents. For goods or services that were not delivered, keep the agreed delivery details and correspondence with the merchant. Deadlines and outcomes depend on the bank, card scheme and circumstances; this chat cannot promise a refund or lodge a dispute.',
        ('dispute', 'chargeback', 'unauthorised', 'unauthorized', 'non-delivery', 'not delivered', 'merchant', 'refund'),
        publisher='Association of Banks in Singapore', reference_url='https://www.abs.org.sg/consumer-banking/consumers/credit-card-chargeback-dispute-resolution', checked_on='2026-10-12', review_due='2027-01-12'),
]

INDEX = {a.id: a for a in ARTICLES}

def search(text: str) -> list[Article]:
    query = text.lower()
    for typo,word in {'crad':'card','balnce':'balance','acount':'account','transction':'transaction','laon':'loan'}.items():query=query.replace(typo,word)
    scores = [(sum(bool(re.search(r'\b'+re.escape(k)+r's?\b',query)) for k in a.keywords),a) for a in ARTICLES if is_current(a)]
    return [a for score,a in sorted(scores,key=lambda x:x[0],reverse=True) if score][:4]

def source(article: Article) -> dict:
    return {'id':article.id,'title':article.title,'url':f'/knowledge/{article.id}','version':article.version,'publisher':article.publisher,'reference_url':article.reference_url,'checked_on':article.checked_on,'review_due':article.review_due,'current':is_current(article)}
