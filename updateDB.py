"""
Daily incremental sync: fetches sets completed/updated since the DB's
most recent set and upserts them into sets.db.
"""

import sqlite3
import sys

import startgg_sync as sg

# Re-fetch a day's worth of overlap so a set updated after being fetched
# (e.g. a VOD attached later) still gets picked up. INSERT OR REPLACE makes
# re-fetching already-known sets harmless.
OVERLAP_SECONDS = 86400


def main():
    conn = sqlite3.connect(sg.DB_PATH)
    max_completed_at = conn.execute("SELECT MAX(completed_at) FROM sets").fetchone()[0]
    since = max(max_completed_at - OVERLAP_SECONDS, 0) if max_completed_at else None

    player_id = sg.get_player_id()
    new_rows = sg.fetch_sets(player_id, since=since)
    sg.write_rows(conn, new_rows)

    conn.close()
    print(f"Done. {len(new_rows)} sets synced.", file=sys.stderr)


if __name__ == "__main__":
    main()
