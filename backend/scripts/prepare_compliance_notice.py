"""Prepare an operator-reviewed SQL notice import; never connects, deploys or uses app credentials.

python -m scripts.prepare_compliance_notice --input /private/tmp/notice.json --output /private/tmp/notice.sql
Input: synthetic=true, customer_id, title, notice_text, notice_date, due_date, items
(each item: label, instructions, optional check_guidance). Item IDs and case IDs are generated.
The database owner must review/apply the SQL; customer-uploaded letters cannot authorise new requirements.
"""
import argparse
import json
from datetime import date
from pathlib import Path
from uuid import uuid4
from pydantic import BaseModel,ConfigDict,Field,StrictInt


class Item(BaseModel):
    model_config=ConfigDict(extra='forbid')
    label: str=Field(min_length=1,max_length=2000)
    instructions: str=Field(min_length=1,max_length=2000)
    check_guidance: str | None=Field(default=None,max_length=2000)


class Notice(BaseModel):
    model_config=ConfigDict(extra='forbid')
    synthetic: bool
    customer_id: StrictInt=Field(gt=0)
    title: str=Field(min_length=1,max_length=2000)
    notice_text: str=Field(min_length=1,max_length=2000)
    notice_date: date
    due_date: date | None=None
    items: list[Item]=Field(min_length=1,max_length=200)


def render(notice):
    if not notice.synthetic:raise ValueError('Only synthetic coursework notices are supported')
    payload=notice.model_dump(mode='json');payload['case_id']=str(uuid4())
    for item in payload['items']:item['item_id']=str(uuid4())
    literal="'"+json.dumps(payload,ensure_ascii=False).replace("'","''")+"'::jsonb"
    delimiter='$notice_'+uuid4().hex+'$'
    while delimiter in literal:delimiter='$notice_'+uuid4().hex+'$'
    sql='''-- Synthetic authorised notice import. Review before applying as database owner.
begin;
do $notice$
declare n jsonb:='''+literal+'''; item jsonb; position int:=0;
begin
 insert into compliance.cases(case_id,customer_id,case_type,title,notice_text,notice_date,due_date,status,status_updated_at)
 values((n->>'case_id')::uuid,(n->>'customer_id')::bigint,'additional_review',n->>'title',n->>'notice_text',
 (n->>'notice_date')::date,(n->>'due_date')::date,'documents_requested',clock_timestamp());
 for item in select value from jsonb_array_elements(n->'items') loop
  position:=position+1;
  insert into compliance.requested_items(item_id,case_id,customer_id,label,instructions,check_guidance,sort_order)
  values((item->>'item_id')::uuid,(n->>'case_id')::uuid,(n->>'customer_id')::bigint,item->>'label',item->>'instructions',item->>'check_guidance',position);
 end loop;
end $notice$;
commit;
'''
    return sql.replace('do $notice$','do '+delimiter,1).replace('end $notice$;','end '+delimiter+';',1)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();notice=Notice.model_validate_json(Path(args.input).read_text())
    with Path(args.output).open('x') as out:out.write(render(notice))
    print('Prepared a synthetic notice import; no SQL executed.')


if __name__=='__main__':main()
