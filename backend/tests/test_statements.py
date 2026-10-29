"""Statements (CR15): Michelle's three months of history and the statements article v2 (0077); real temporary PostgreSQL."""
from decimal import Decimal
import pytest
from app.services import knowledge

pytestmark=pytest.mark.skipif(__import__('os').getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native PostgreSQL with TEST_DATABASE_LOCAL=1')


def test_michelle_has_three_full_months_of_activity(database) -> None:
    months=database.execute("""select to_char(txn_date at time zone 'Asia/Singapore','YYYY-MM'),count(*) from banking.transactions
      where customer_id=1002 and txn_date<'2026-10-01+08' group by 1 order by 1""").fetchall()
    assert [month for month,_ in months]==['2026-07','2026-08','2026-09'] and all(count>=6 for _,count in months)


def test_card_payments_clear_the_previous_month_and_lead_to_todays_balance(database) -> None:
    def charges(month): return database.execute("""select -sum(amount) from banking.transactions where card_id=10021 and amount<0
      and to_char(txn_date at time zone 'Asia/Singapore','YYYY-MM')=%s""",(month,)).fetchone()[0]
    def paid(month): return database.execute("""select sum(amount) from banking.transactions where card_id=10021 and amount>0
      and to_char(txn_date at time zone 'Asia/Singapore','YYYY-MM')=%s""",(month,)).fetchone()[0]
    assert paid('2026-08')==charges('2026-07') and paid('2026-09')==charges('2026-08')
    assert charges('2026-09')==Decimal('1250.40')  # her card balance before the 27 Oct charges in 0075


def test_only_statements_v2_is_searchable_and_it_matches_the_app(database) -> None:
    assert database.execute("select version,status from kb.articles where article_id='statements' order by 1").fetchall()==[(1,'retired'),(2,'published')]
    body,=database.execute("select body from kb.sections where article_id='statements' and version=2").fetchone()
    assert body==knowledge.INDEX['statements'].content and 'Overview' in body and 'Statement or document copy' in body
