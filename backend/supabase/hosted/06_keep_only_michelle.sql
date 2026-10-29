-- Keep only Michelle (customer 1002, demo.michelle@example.com). Removes every other customer
-- (legacy 1-4, Alex 1001, David 1003): their logins and all their rows (accounts, cards, loans,
-- transactions, chats, actions, tickets, consent, notifications...). Advisers (rm.advisors),
-- knowledge articles and request categories stay. The audit log is append-only and is never
-- changed; old audit rows simply keep the removed customers' ids.
--
-- Run in the Supabase SQL editor in three steps. Nothing is deleted until step 2.

-- 1. Preview: customers that will be removed (should list everyone except 1002)
select customer_id,full_name,demo_email,auth_user_id is not null as has_login
from banking.customer_profiles where customer_id<>1002 order by customer_id;

-- 2. Remove them (one transaction; if anything fails, nothing changes)
begin;
-- Their logins (customer_profiles.auth_user_id is set to null automatically)
delete from auth.users u using banking.customer_profiles p
where p.customer_id<>1002 and (p.auth_user_id=u.id or lower(u.email)=p.demo_email);
-- Break the two links that point both ways, then staff records that hang off their actions
update compliance.requested_items set current_document_id=null where customer_id<>1002 and current_document_id is not null;
delete from actions.staff_commands s using actions.commands c where c.command_id=s.command_id and c.customer_id<>1002;
-- Every table with a customer_id column, repeated until nothing more can be deleted (children before parents)
do $keep$
declare t record; n bigint; pass int; progress boolean; blocked text[];
begin
  for pass in 1..15 loop
    progress:=false; blocked:='{}';
    for t in
      select c.table_schema s,c.table_name tn from information_schema.columns c
      join information_schema.tables x on x.table_schema=c.table_schema and x.table_name=c.table_name and x.table_type='BASE TABLE'
      where c.column_name='customer_id' and c.table_schema not in ('audit','pg_catalog','information_schema')
      order by 1,2
    loop
      begin
        execute format('delete from %I.%I where customer_id is not null and customer_id<>1002',t.s,t.tn);
        get diagnostics n=row_count;
        if n>0 then progress:=true; raise notice 'removed % rows from %.%',n,t.s,t.tn; end if;
      exception when foreign_key_violation then
        blocked:=blocked||format('%I.%I',t.s,t.tn);
      end;
    end loop;
    exit when not progress;
  end loop;
  if cardinality(blocked)>0 then raise exception 'Could not remove rows from: % (nothing was changed)',array_to_string(blocked,', '); end if;
end $keep$;
commit;

-- 3. Check: every line should show 0
select c.table_schema||'.'||c.table_name as table_name,
  (xpath('/row/n/text()',query_to_xml(format('select count(*) as n from %I.%I where customer_id<>1002',c.table_schema,c.table_name),false,true,'')))[1]::text::int as other_customers
from information_schema.columns c join information_schema.tables x using(table_schema,table_name)
where c.column_name='customer_id' and x.table_type='BASE TABLE' and c.table_schema not in ('audit','pg_catalog','information_schema')
order by 2 desc,1;
