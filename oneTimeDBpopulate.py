"""
One-off script: full historical populate of sets.db from start.gg.

Run once to seed the database. The daily incremental sync is a separate
script (not this one) that only fetches new sets going forward.

Usage:
    python oneTimeDBpopulate.py
"""

import sqlite3
import sys

import startgg_sync as sg


def main():
    conn = sqlite3.connect(sg.DB_PATH)
    conn.executescript(open("schema.sql").read())

    player_id = sg.get_player_id()
    rows = sg.fetch_sets(player_id, since=None)
    sg.write_rows(conn, rows)

    conn.close()
    print(f"Done. {len(rows)} sets written to {sg.DB_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
