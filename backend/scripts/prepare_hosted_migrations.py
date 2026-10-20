"""Prepare atomic SQL Editor batches for the existing DemoBank hosted project.

Usage from backend/: python -m scripts.prepare_hosted_migrations --first 8 --last 12
--output /private/tmp/demobank-0008-0012.sql. Inspect the existing schema first.
The original 0001-0007 baseline is already present without complete migration history.
Migration 0008 reuses the verified legacy kb namespace, preserving its tables; released
files stay unchanged. Executed SQL is recorded in Supabase's migration history.
This command only writes a reviewable file. It never connects to a hosted database.
"""
import argparse
import hashlib
import re
from pathlib import Path
from pydantic import BaseModel,ConfigDict


class Migration(BaseModel):
    model_config=ConfigDict(frozen=True,strict=True)
    version: str
    name: str
    sql: str


def load_migrations(folder: Path,first: int,last: int) -> list[Migration]:
    if not 8<=first<=last<=9999:raise ValueError('Choose an ordered range of unapplied migrations from 0008 onwards')
    files: dict[int,Path]={}
    for path in sorted(folder.glob('*.sql')):
        match=re.fullmatch(r'([0-9]{4})_([a-z0-9_]+)\.sql',path.name)
        if match is None:raise ValueError(f'Invalid migration filename: {path.name}')
        number=int(match[1])
        if number in files:raise ValueError(f'Duplicate migration version: {number:04d}')
        files[number]=path
    result: list[Migration]=[]
    for number in range(first,last+1):
        if number not in files:raise FileNotFoundError(f'Missing migration {number:04d} in {folder}')
        path=files[number]
        result.append(Migration(version=f'{number:04d}',name=path.stem[5:],sql=path.read_text()))
    return result


def adopt_knowledge_namespace(sql: str) -> str:
    statement='create schema kb;'
    if sql.count(statement)!=1:raise ValueError('Migration 0008 no longer has the reviewed namespace declaration; inspect it before deployment')
    guard="""do $legacy_kb$
begin
  if to_regclass('kb.documents') is null or to_regclass('kb.support_contacts') is null then
    raise exception 'this batch requires the verified legacy kb namespace; use unchanged migrations on a fresh project';
  end if;
end $legacy_kb$;"""
    return sql.replace(statement,guard)


def render_bundle(migrations: list[Migration]) -> str:
    if not migrations:raise ValueError('At least one migration is required')
    parts=['-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.\nbegin;',
        "set local lock_timeout='10s';\nset local statement_timeout='120s';",
        "select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));"]
    for migration in migrations:
        if not re.fullmatch(r'[0-9]{4}',migration.version) or not re.fullmatch(r'[a-z0-9_]+',migration.name):
            raise ValueError('Migration version/name must be a safe repository identifier')
        sql=adopt_knowledge_namespace(migration.sql) if migration.version=='0008' else migration.sql
        checksum=hashlib.sha256(migration.sql.encode()).hexdigest()
        sql=f'-- Released source SHA256: {checksum}\n'+sql
        delimiter=f'$source_{migration.version}$'
        block=f'$apply_{migration.version}$'
        if delimiter in sql or block in sql:raise ValueError(f'Migration {migration.version} conflicts with its SQL quoting delimiter')
        source=delimiter+sql+delimiter
        parts.append(f"""do {block}
declare previous supabase_migrations.schema_migrations; script text:={source};
begin
  select * into previous from supabase_migrations.schema_migrations where version='{migration.version}';
  if found then
    if previous.name is distinct from '{migration.name}' or previous.statements is distinct from array[script] then
      raise exception 'migration {migration.version} is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration {migration.version} already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('{migration.version}','{migration.name}',array[script]);
  end if;
end {block};""")
    parts.append("commit;\nselect version,name from supabase_migrations.schema_migrations order by version;")
    return '\n\n'.join(parts)+'\n'


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--first',type=int,required=True)
    parser.add_argument('--last',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    migrations=load_migrations(Path(__file__).parents[1]/'supabase'/'migrations',args.first,args.last)
    with args.output.open('x') as output:output.write(render_bundle(migrations))
    print(f'Prepared {len(migrations)} migrations in {args.output}; no hosted SQL was executed.')


if __name__=='__main__':main()
