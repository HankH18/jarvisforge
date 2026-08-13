"""``preferences`` table (docs/DESIGN.md §Data models): a plain Postgres
table living alongside mem0's own pgvector tables in the same database.
T-5 creates it and provides the get/add round-trip; T-10 populates it via
voice capture (out of this ticket's scope -- SPEC R3 / non-goals).

Deliberately plain SQL via ``psycopg`` rather than mem0 -- this is not a
vector-search concern, just a small append-only rules table.
"""

from __future__ import annotations

import psycopg

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS preferences (
    user_id text NOT NULL,
    rule text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
)
"""


def ensure_preferences_table(conninfo: str) -> None:
    """Idempotent migration: create ``preferences`` if it doesn't exist yet."""
    with psycopg.connect(conninfo) as conn, conn.cursor() as cur:
        cur.execute(_CREATE_TABLE_SQL)
        conn.commit()


def add_preference(conninfo: str, user_id: str, rule: str) -> None:
    with psycopg.connect(conninfo) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO preferences (user_id, rule) VALUES (%s, %s)",
            (user_id, rule),
        )
        conn.commit()


def get_preferences(conninfo: str, user_id: str) -> list[str]:
    with psycopg.connect(conninfo) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT rule FROM preferences WHERE user_id = %s ORDER BY created_at",
            (user_id,),
        )
        return [row[0] for row in cur.fetchall()]
