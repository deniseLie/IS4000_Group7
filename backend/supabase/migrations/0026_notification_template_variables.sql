-- Placeholder identifiers such as last4 include digits. Keep exact-key checking and reject private rendered text.
create or replace function notify.render(p_template text,p_vars jsonb)
returns text language plpgsql immutable security invoker set search_path=''
as $$
declare result text:=p_template; key text; value text;
begin
  if p_template is null or jsonb_typeof(p_vars) is distinct from 'object' then
    raise exception 'notification rendering requires a template and named variables' using errcode='P2001';
  end if;
  for key,value in select k,v#>>'{}' from jsonb_each(p_vars) as x(k,v) loop
    if key !~ '^[a-z][a-z0-9_]{0,29}$' or jsonb_typeof(p_vars->key)<>'string' or value is null or length(value)>200
        or value ~ '[{}]' or position('{'||key||'}' in p_template)=0 then
      raise exception 'notification variable % is invalid or not declared by the template',key using errcode='P2001';
    end if;
    result:=replace(result,'{'||key||'}',value);
  end loop;
  if result ~ '[{}]' or length(result)>1500 or result ~ '(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|sb_secret_[A-Za-z0-9_-]+|sk-or-v1-[A-Za-z0-9_-]+)' then
    raise exception 'notification template has missing variables, a credential or exceeds its size limit' using errcode='P2001';
  end if;
  perform conversation.require_redacted(result);
  return result;
end $$;
