-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0086$
declare previous supabase_migrations.schema_migrations; script text:=$source_0086$-- Released source SHA256: 0ff58cbef6e9e905de4ac96acac702136b40b6415270a2b6d2b53851c2480637
-- CR16: approved demo information for home and personal loans (car loans already had it), so loan questions are answered
-- from DemoBank articles instead of general education. No rates, amounts or eligibility decisions; the bank decides.
insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('home-loans',1,'Home loans',array['retail'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null),
  ('personal-loans',1,'Personal loans',array['retail'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null)
on conflict do nothing;
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('home-loans',1,'main','Home loans','The fictional DemoBank offers home loans to buy a private property or HDB flat, or to refinance an existing home loan. A home loan is secured on the property and repaid in monthly instalments over an agreed term. Interest rates, fees, maximum loan amounts and loan-to-value limits are not supplied in this demo. Applicants generally need to be at least 21 years old and provide identification, proof of income (for example payslips, tax assessments or, if self-employed, business accounts) and the option to purchase or sales agreement for the property. To apply in this demo, use Loans in the official DemoBank app or website, or ask your relationship manager. The bank assesses each application and makes the final decision; the assistant cannot confirm eligibility, approval, rates or disbursement dates. This is general information, not advice.','home loan housing loan mortgage hdb property refinance apply eligibility'),
  ('personal-loans',1,'main','Personal loans','The fictional DemoBank offers unsecured personal loans for planned expenses such as renovation, education or a wedding, repaid in fixed monthly instalments over an agreed term. Interest rates, fees and maximum loan amounts are not supplied in this demo. Applicants generally need to be at least 21 years old, have a regular income and provide identification and proof of income. To apply in this demo, use Loans in the official DemoBank app or website, or ask your relationship manager. The bank assesses each application and makes the final decision; the assistant cannot confirm eligibility, approval, rates or disbursement dates. Borrow only what you can repay. This is general information, not advice.','personal loan renovation education wedding unsecured apply eligibility')
on conflict do nothing;
$source_0086$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0086';
  if found then
    if previous.name is distinct from 'home_and_personal_loans' or previous.statements is distinct from array[script] then
      raise exception 'migration 0086 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0086 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0086','home_and_personal_loans',array[script]);
  end if;
end $apply_0086$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
