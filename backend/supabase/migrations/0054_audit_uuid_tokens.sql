-- Canonical UUIDs are identifiers, including when their final segment has twelve digits.
-- Keep the existing card-number, token-length, nesting and collection limits for other values.
create or replace function audit.is_safe(v jsonb, depth int default 0)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare s text;
begin
  case jsonb_typeof(v)
    when 'null', 'number', 'boolean' then return true;
    when 'string' then
      s := v #>> '{}';
      return s ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        or (s !~ '[0-9]{12,}' and (s ~ '^[A-Za-z0-9_.:#/+-]{1,80}$' or s ~ '^[a-z0-9_-]{1,60}@v[0-9]+(#[a-z0-9_-]{1,40})?$'));
    when 'array' then
      return depth < 2 and jsonb_array_length(v) <= 20
        and not exists (select 1 from jsonb_array_elements(v) e where not audit.is_safe(e, depth + 1));
    when 'object' then
      return depth < 2 and (select count(*) from jsonb_object_keys(v)) <= 20
        and not exists (select 1 from jsonb_each(v) kv where kv.key !~ '^[a-z][a-z0-9_]{0,39}$' or not audit.is_safe(kv.value, depth + 1));
  end case;
  return false;
end $$;
