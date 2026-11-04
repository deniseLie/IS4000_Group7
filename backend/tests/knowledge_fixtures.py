"""Approved section text as the database returns it (kb_search rows), for validator and composer unit tests.

The text is copied from the published sections (migration 0009); unit tests check sample strings through the validators,
never retrieval. Retrieval and article content are tested against the database in test_knowledge_database.py.
"""
from app.schemas.agents import Evidence
from app.schemas.knowledge import KnowledgeSection

SECTIONS={
    'sg-accounts':dict(title='Singapore bank account basics',publisher='MoneySense',
        scope='General Singapore banking education, summarised by this project. Not a DemoBank product policy. Confirm your own bank’s terms.',
        reference_url='https://www.moneysense.gov.sg/understanding-bank-accounts/',checked_on='2026-10-12',review_due='2027-01-12',
        body='Savings and current accounts can be used for everyday payments. Fixed deposits hold money until a maturity date. Withdrawing '
            'a fixed deposit early may reduce or remove interest and may incur a fee. Before opening an account, check its minimum deposit, '
            'minimum balance, fees, withdrawal limits and interest terms. Ask the bank which identification and supporting documents it '
            'needs. Product terms differ between banks.'),
    'transactions':dict(title='Failed pending and unfamiliar payments',publisher='DemoBank coursework project',
        scope='Fictional demo guidance; not a live bank policy.',reference_url=None,checked_on=None,review_due=None,
        body='Failed means the payment did not complete; check the message in your official banking app before retrying. Pending means '
            'processing is not complete; check the status and avoid duplicate payments. Exact release or completion times are unavailable '
            'here. If a transaction is unfamiliar, treat it as possible fraud and contact your bank immediately through its official app or '
            'website. The chatbot cannot reverse or refund payments.'),
}


def cite(article_id: str) -> str:
    return f'kb:{article_id}@v1#main'


def evidence(article_id: str) -> list[Evidence]:
    """One published v1 section as Evidence, exactly as the pipelines build it from a search result."""
    item=KnowledgeSection.model_validate({'cite':cite(article_id),'article_id':article_id,'version':1,'anchor':'main',
        'heading':SECTIONS[article_id]['title'],'score':0.5,**SECTIONS[article_id]})
    return [Evidence(id=item.cite,text=item.body,publisher=item.publisher,scope=item.scope,source=item.source())]
