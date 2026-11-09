"""The product is UBS Connect: knowledge read from an older database still says DemoBank and is renamed on read."""
from app.services import kb


def test_knowledge_rows_are_renamed_on_read():
    rows=[{'title':'Loans at DemoBank','heading':'What DemoBank offers','body':'DemoBank offers property financing.','publisher':'DemoBank coursework project',
        'scope':'Fictional demo guidance; not a live bank policy.','cite':'kb:loans@v3#main','score':0.5}]
    out=kb.renamed(rows)[0]
    assert out['title']=='Loans at UBS Connect' and out['body']=='UBS Connect offers property financing.' and out['publisher']=='UBS Connect'
    assert out['scope']==rows[0]['scope'] and out['cite']=='kb:loans@v3#main' and out['score']==0.5
    assert kb.renamed(None)==[]
