"""Versioned fictional demo guidance, not live bank policies or rates."""
from dataclasses import dataclass
import re

@dataclass(frozen=True)
class Article:
    id: str
    title: str
    content: str
    keywords: tuple[str, ...]
    version: str = '2026-10-12'

ARTICLES = [
    Article('accounts', 'Demo account information', 'DemoBank is a fictional coursework bank. General guidance is available without login. Sign in to view your synthetic account balances and recent transactions. The demo does not provide live balances. Specific account fees, interest rates and limits are unavailable unless supplied in an approved product record.', ('account','balance','saving','current','interest','fee','limit')),
    Article('loans', 'Demo loan enquiries', 'Loan product records describe eligibility and application steps. Any comparison with a synthetic customer profile is preliminary guidance only. Ask for missing details instead of treating them as eligible. Only the bank can decide on an actual application. The chatbot cannot submit an application or promise approval. This is general information, not advice or a contract.', ('loan','mortgage','borrow','eligibility','eligible','application','income','repayment')),
    Article('cards', 'Demo credit card enquiries', 'Use the matching credit card product record for fees and terms. If no product record is supplied, its terms cannot be looked up in this chat. For a card that is not working, ask whether the problem concerns an ATM withdrawal, online payment or shop payment and what error appeared. Do not invent an amount or use another card product. Personal card limits, balances and due dates require sign-in and are synthetic. Available credit cannot be calculated without sufficient records. Fee guidance is general information, not advice or a contract.', ('card','credit','fee','payment','due','limit','interest','late')),
    Article('statements', 'Finding a statement', 'The chatbot cannot open or download your statement. For a real statement, use your bank’s official app or website and look for its statement or document service. Exact navigation varies by bank; contact verified support if you cannot find it. Do not upload a real statement to this demo.', ('statement','download','document')),
    Article('transactions', 'Failed pending and unfamiliar payments', 'Failed means the payment did not complete; check the message in your official banking app before retrying. Pending means processing is not complete; check the status and avoid duplicate payments. Exact release or completion times are unavailable here. If a transaction is unfamiliar, treat it as possible fraud and contact your bank immediately through its official app or website. The chatbot cannot reverse or refund payments.', ('transaction','declined','failed','pending','unfamiliar','fraud','payment','decline')),
    Article('lost-card', 'Lost or stolen card guidance', 'Report a lost or stolen card to your bank immediately through its official app or website. If the official app offers a freeze or block control, use that control. Review recent activity and report unfamiliar transactions. The chatbot cannot block a card or confirm a block. Do not wait for a reply here if your real card is at risk. Never share your PIN, password, OTP, CVV or full card number.', ('lost','stolen','missing card','block','freeze')),
    Article('security', 'Protecting private information', 'Never share passwords, PINs, OTPs, CVVs or full card numbers in chat. Ignore anyone asking for these details in chat. Sign in only through the separate sign-in form. If you already disclosed a secret, stop sharing it and use your bank’s official security or recovery service. This demo uses best-effort text filtering and is not a place for real private banking data.', ('password','pin','otp','cvv','security','secret','login','sign in')),
    Article('support', 'Getting human support', 'This demo has no staffed support queue. You can review, edit and copy a summary for your own use. Copying a summary does not send a ticket or contact a person. For a real issue, contact your bank using its official app or website. The chatbot cannot make payments, close accounts, dispute charges or change banking records.', ('human','person','support','staff','contact','close','dispute','transfer','refund')),
]
INDEX = {a.id: a for a in ARTICLES}

def search(text: str) -> list[Article]:
    query = text.lower()
    for typo,word in {'crad':'card','balnce':'balance','acount':'account','transction':'transaction','laon':'loan'}.items():query=query.replace(typo,word)
    scores = [(sum(bool(re.search(r'\b'+re.escape(k)+r's?\b',query)) for k in a.keywords),a) for a in ARTICLES]
    return [a for score,a in sorted(scores,key=lambda x:x[0],reverse=True) if score][:4]

def source(article: Article) -> dict:
    return {'id':article.id,'title':article.title,'url':f'/knowledge/{article.id}','version':article.version}
