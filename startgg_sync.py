"""
Shared start.gg sync logic used by both the one-time historical populate
and the daily incremental update scripts.
"""

import os
import sqlite3
import sys
import time

import requests

API_TOKEN = os.environ.get("STARTGG_TOKEN", "b1e8be7fa5cbc4261c5dd75ccf0c6f1f")
URL = "https://api.start.gg/gql/alpha"
DB_PATH = "sets.db"
DISCRIMINATOR = "2d2988d7"  # Pascal

USER_QUERY = """
query User($slug: String!) {
  user(slug: $slug) {
    id
    player { id }
  }
}
"""

SETS_QUERY = """
query PlayerSets($playerId: ID!, $page: Int!, $perPage: Int!, $filters: SetFilters) {
  player(id: $playerId) {
    sets(page: $page, perPage: $perPage, filters: $filters) {
      pageInfo { totalPages }
      nodes {
        id
        winnerId
        displayScore
        fullRoundText
        completedAt
        event {
          id
          name
          slug
          numEntrants
          startAt
          videogame { name }
          tournament { name }
        }
        slots {
          entrant {
            id
            name
            initialSeedNum
            isDisqualified
            standing { placement }
            participants { player { id } }
          }
        }
      }
    }
  }
}
"""

INSERT_SQL = """
INSERT OR REPLACE INTO sets (
    set_id, event_id, event_name, tournament_name, game, event_slug,
    event_entrants, event_start, seed, placement, dq,
    opponent_name, opponent_id, completed_at, round, score, result
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def gql(query: str, variables: dict, retries: int = 3) -> dict:
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    }
    for attempt in range(retries):
        try:
            resp = requests.post(
                URL, json={"query": query, "variables": variables},
                headers=headers, timeout=30,
            )
            if resp.status_code == 429:
                time.sleep(2 ** (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            if "errors" in data:
                raise RuntimeError(f"GraphQL errors: {data['errors']}")
            return data["data"]
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise RuntimeError(f"API request failed after {retries} attempts: {e}")
    raise RuntimeError("Rate limited on every attempt")


def get_player_id() -> int:
    user = gql(USER_QUERY, {"slug": f"user/{DISCRIMINATOR}"})["user"]
    if user is None:
        sys.exit(f"No user found for discriminator '{DISCRIMINATOR}'")
    return user["player"]["id"]


def player_entrant(set_: dict, player_id: int):
    for slot in set_.get("slots") or []:
        ent = slot.get("entrant")
        if ent and any(
            (p.get("player") or {}).get("id") == player_id
            for p in ent.get("participants") or []
        ):
            return ent
    return None


def set_to_row(s: dict, player_id: int):
    ev = s.get("event") or {}
    ent = player_entrant(s, player_id)
    if ent is None:
        return None

    opponent = next(
        (slot["entrant"] for slot in s.get("slots") or []
         if slot.get("entrant") and slot["entrant"]["id"] != ent["id"]),
        None,
    )
    opp_pid = None
    if opponent:
        parts = [p for p in opponent.get("participants") or [] if p.get("player")]
        if len(parts) == 1:
            opp_pid = parts[0]["player"]["id"]

    winner_id = s.get("winnerId")
    result = None if winner_id is None else ("W" if winner_id == ent["id"] else "L")

    return (
        s["id"],
        ev.get("id"),
        ev.get("name"),
        (ev.get("tournament") or {}).get("name"),
        (ev.get("videogame") or {}).get("name"),
        ev.get("slug"),
        ev.get("numEntrants"),
        ev.get("startAt"),
        ent.get("initialSeedNum"),
        (ent.get("standing") or {}).get("placement"),
        int(bool(ent.get("isDisqualified"))),
        opponent["name"] if opponent else None,
        opp_pid,
        s.get("completedAt"),
        s.get("fullRoundText"),
        s.get("displayScore"),
        result,
    )


def fetch_sets(player_id: int, since: int = None):
    """
    Paginate through the player's sets.

    If since is given, only sets completed or updated at/after that epoch
    timestamp are fetched (used for the daily incremental sync, passing the
    DB's current max completed_at). If None, every set is fetched (used for
    the initial historical populate).
    """
    filters = {"updatedAfter": since} if since is not None else None

    rows = []
    page, total_pages = 1, None
    while total_pages is None or page <= total_pages:
        data = gql(SETS_QUERY, {
            "playerId": player_id, "page": page, "perPage": 40, "filters": filters,
        })
        conn_data = data["player"]["sets"]
        total_pages = conn_data["pageInfo"]["totalPages"]
        nodes = conn_data["nodes"] or []

        if not nodes:
            break

        for node in nodes:
            row = set_to_row(node, player_id)
            if row:
                rows.append(row)

        print(f"page {page}/{total_pages}: {len(rows)} sets so far", file=sys.stderr)

        page += 1
        time.sleep(0.8)

    return rows


def write_rows(conn: sqlite3.Connection, rows: list):
    if rows:
        conn.executemany(INSERT_SQL, rows)
        conn.commit()
