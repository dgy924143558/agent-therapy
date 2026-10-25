import os

import pytest

# Postgres tests run when TEST_DATABASE_URL points at a disposable database (CI sets one up).
PG_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(params=["sqlite", "postgres"])
def database(request, tmp_path):
    if request.param == "sqlite":
        return str(tmp_path / "test.db")
    if not PG_URL:
        pytest.skip("set TEST_DATABASE_URL to run the Postgres tests")
    import psycopg

    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS events, daily_counts")
    return PG_URL
