"""Retrieval quality floors for both knowledge stores (evaluation/retrieval.py, synthetic hand-labelled cases).

The memory check always runs. The database check needs TEST_DATABASE_LOCAL=1 (see test_database_local.py).
Floors sit a little under the 31 Oct 2026 results so new articles can reshuffle ranks; a larger drop is a regression.
No-match floors were lowered on 2 Nov 2026 (0087): six gap cases became covered, and they had counted as correct
empty results. On the remaining gap cases both stores score the same with or without 0087 (memory 0.667, database 0.476).
"""
import os
from typing import TYPE_CHECKING
import pytest
from evaluation import retrieval

if TYPE_CHECKING:
    from psycopg import Connection
    from tests.database_support import SqlRow

FLOORS={'memory':{'hit_at_1':0.9,'hit_at_4':0.95,'no_match_accuracy':0.6},
    'database':{'hit_at_1':0.9,'hit_at_4':0.95,'no_match_accuracy':0.45}}


def check(report: dict,store: str) -> None:
    overall=report['overall']
    low={name:overall[name] for name,floor in FLOORS[store].items() if overall[name]<floor}
    assert not low,f'{store} retrieval fell below its floor {low}:\n'+'\n'.join(retrieval.misses(report))
    off_topic=[row for row in report['rows'] if row['group']=='off_topic' and row['found']]
    assert not off_topic,f'off-topic questions returned articles: {[(row["id"],row["found"]) for row in off_topic]}'


def test_cases_are_well_formed() -> None:
    from app.services.knowledge import INDEX
    rows=retrieval.cases()
    assert len({row['id'] for row in rows})==len(rows)
    for row in rows:
        assert row['split'] in {'tune','holdout'} and row['group'] in {'covered','informal','gap','off_topic','non_english'}
        assert (row['expected'] is None)==(row['group'] in {'gap','off_topic'})
        assert all(article in INDEX for article in row['expected'] or [])


def test_memory_retrieval_meets_floors() -> None:
    check(retrieval.evaluate(retrieval.memory_search,retrieval.cases()),'memory')


@pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')
def test_database_retrieval_meets_floors(database: 'Connection[SqlRow]') -> None:
    check(retrieval.evaluate(retrieval.database_search(database),retrieval.cases()),'database')
