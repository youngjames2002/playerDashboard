"""
Streamlit dashboard for Pascal's (start.gg user/2d2988d7) tournament history.

Usage:
    streamlit run testDashBoard.py

Fetches the full set history from start.gg (cached for an hour) and shows
win rate over time, recent form, streaks, head-to-head records, a locals
split, and events as cards with per-event set history.

DQ sets are always excluded from win-rate/streak/H2H stats; they still
appear in event set histories.
"""

import math
import os
import re
import sys
import time

import altair as alt
import pandas as pd
import requests
import streamlit as st


def _get_token() -> str:
    """start.gg API key from .streamlit/secrets.toml or the environment."""
    try:
        return st.secrets["STARTGG_TOKEN"]
    except Exception:
        return os.environ.get("STARTGG_TOKEN", "")


API_TOKEN = _get_token()
URL = "https://api.start.gg/gql/alpha"

DISCRIMINATOR = "2d2988d7"  # Pascal

USER_QUERY = """
query User($slug: String!) {
  user(slug: $slug) {
    id
    slug
    player {
      id
      gamerTag
      prefix
    }
  }
}
"""

SETS_QUERY = """
query PlayerSets($playerId: ID!, $page: Int!, $perPage: Int!) {
  player(id: $playerId) {
    sets(page: $page, perPage: $perPage) {
      pageInfo {
        total
        totalPages
      }
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
            participants {
              player { id }
            }
          }
        }
      }
    }
  }
}
"""


def gql(query: str, variables: dict, retries: int = 3) -> dict:
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    }
    for attempt in range(retries):
        try:
            resp = requests.post(
                URL,
                json={"query": query, "variables": variables},
                headers=headers,
                timeout=30,
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


def fetch_all_sets(player_id: int) -> list:
    nodes, page, total_pages = [], 1, None
    while total_pages is None or page <= total_pages:
        data = gql(SETS_QUERY, {"playerId": player_id, "page": page, "perPage": 40})
        conn = data["player"]["sets"]
        total_pages = conn["pageInfo"]["totalPages"]
        nodes.extend(conn["nodes"] or [])
        print(f"sets: page {page}/{total_pages} ({len(nodes)} fetched)",
              file=sys.stderr)
        page += 1
        time.sleep(0.8)
    return nodes


def player_entrant(set_: dict, player_id: int) -> dict | None:
    """The entrant slot in this set that belongs to the given player."""
    for slot in set_.get("slots") or []:
        ent = slot.get("entrant")
        if ent and any(
            (p.get("player") or {}).get("id") == player_id
            for p in ent.get("participants") or []
        ):
            return ent
    return None


# ---------------------------------------------------------------------------
# Event classification (from event/tournament names)
# ---------------------------------------------------------------------------

BRACKET_TYPES = ["Singles", "Doubles & teams", "Squad Strike", "Randoms & gimmicks"]

# Smash titles only: Ultimate, Melee, Wii U, Brawl, 64, Project+/M, HewDraw Remix.
# Everything else (Rivals, chess, kart, NASB, ...) is dropped at load time.
SMASH_RE = re.compile(r"super smash bros|hewdraw|project\s?[+m]", re.I)

AMATEUR_RE = re.compile(r"amm(?:ies|y|ie)|amateur|redemption|arcadian", re.I)
LOCAL_RE = re.compile(r"^big fish\s*\d+", re.I)
GIMMICK_RE = re.compile(
    r"random|randub|reverse mains|smashdown|luigi only|hazards on|"
    r"final smash meter|giant smash|chaos|fox only|rock paper scissors|"
    r"jousting|5-a-side|wario\s?ware|ladder",
    re.I,
)
TEAMS_RE = re.compile(r"doubles|dubs|dubbies|2v2|triples|crews|teams", re.I)
SQUAD_RE = re.compile(r"squad\s?strike", re.I)


def bracket_type(event_name: str) -> str:
    if GIMMICK_RE.search(event_name):
        return "Randoms & gimmicks"
    if SQUAD_RE.search(event_name):
        return "Squad Strike"
    if TEAMS_RE.search(event_name):
        return "Doubles & teams"
    return "Singles"


def classify(event_name: str, tournament_name: str) -> dict:
    return {
        "bracket": bracket_type(event_name or ""),
        "amateur": bool(AMATEUR_RE.search(event_name or "")),
        "local": bool(LOCAL_RE.match(tournament_name or "")),
    }


# ---------------------------------------------------------------------------
# DataFrames
# ---------------------------------------------------------------------------

def events_df_from_sets(sets: list, player_id: int) -> pd.DataFrame:
    """One row per event the player appears in, aggregated from their sets."""
    events = {}
    for s in sets:
        ev = s.get("event")
        ent = player_entrant(s, player_id)
        if not ev or not ent:
            continue
        tournament = (ev.get("tournament") or {}).get("name")
        rec = events.setdefault(ev["id"], {
            "event_id": ev["id"],
            "tournament": tournament,
            "event": ev["name"],
            "game": (ev.get("videogame") or {}).get("name"),
            "entrants": ev.get("numEntrants"),
            "start": ev.get("startAt"),
            "seed": ent.get("initialSeedNum"),
            "placement": (ent.get("standing") or {}).get("placement"),
            "dq": bool(ent.get("isDisqualified")),
            "sets_won": 0,
            "sets_lost": 0,
            "slug": ev.get("slug"),
            **classify(ev.get("name"), tournament),
        })
        if s.get("winnerId") is not None:
            if s["winnerId"] == ent["id"]:
                rec["sets_won"] += 1
            else:
                rec["sets_lost"] += 1
    df = pd.DataFrame(list(events.values()))
    df["start"] = pd.to_datetime(df["start"], unit="s")
    return df.sort_values("start", ascending=False).reset_index(drop=True)


def sets_df_from_sets(sets: list, player_id: int) -> pd.DataFrame:
    """One row per set: opponent, score, result, event context."""
    rows = []
    for s in sets:
        ev = s.get("event") or {}
        ent = player_entrant(s, player_id)
        if ent is None:
            continue
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
        tournament = (ev.get("tournament") or {}).get("name")
        rows.append({
            "date": s.get("completedAt"),
            "tournament": tournament,
            "event": ev.get("name"),
            "game": (ev.get("videogame") or {}).get("name"),
            "round": s.get("fullRoundText"),
            "opponent": opponent["name"] if opponent else None,
            "score": s.get("displayScore"),
            "result": None if winner_id is None else ("W" if winner_id == ent["id"] else "L"),
            "event_id": ev.get("id"),
            "opponent_id": opp_pid,
            **classify(ev.get("name"), tournament),
        })
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], unit="s")
    return df.sort_values("date", ascending=False).reset_index(drop=True)


def fetch_player_data():
    """Returns (user, events_df, sets_df) for the hardcoded player."""
    user = gql(USER_QUERY, {"slug": f"user/{DISCRIMINATOR}"})["user"]
    if user is None:
        raise RuntimeError(f"No user found for discriminator '{DISCRIMINATOR}'")

    player_id = user["player"]["id"]
    sets = fetch_all_sets(player_id)
    events_df = events_df_from_sets(sets, player_id)
    sets_df = sets_df_from_sets(sets, player_id)

    def smash_only(df):
        keep = df["game"].map(lambda g: bool(SMASH_RE.search(g or "")))
        return df[keep].reset_index(drop=True)

    return user, smash_only(events_df), smash_only(sets_df)


# ---------------------------------------------------------------------------
# Stats helpers (all operate on DQ-free, completed sets)
# ---------------------------------------------------------------------------

def playable(sets_df: pd.DataFrame) -> pd.DataFrame:
    """Sets that count for stats: completed, dated, not a DQ."""
    return sets_df[
        sets_df["result"].notna()
        & sets_df["date"].notna()
        & (sets_df["score"] != "DQ")
    ]


def win_rate(df: pd.DataFrame) -> float | None:
    return None if df.empty else (df["result"] == "W").mean() * 100


def record(df: pd.DataFrame) -> str:
    w = int((df["result"] == "W").sum())
    return f"{w}–{len(df) - w}"


def streak_stats(played: pd.DataFrame) -> dict:
    """played ordered oldest -> newest. Returns each streak's length and its sets."""
    df = played.reset_index(drop=True)
    runs = []  # [result, first_idx, last_idx]
    for i, r in enumerate(df["result"]):
        if runs and runs[-1][0] == r:
            runs[-1][2] = i
        else:
            runs.append([r, i, i])

    def longest(kind):
        cand = [run for run in runs if run[0] == kind]
        if not cand:
            return 0, df.iloc[0:0]
        run = max(cand, key=lambda x: x[2] - x[1])
        return run[2] - run[1] + 1, df.iloc[run[1]:run[2] + 1]

    lw, lw_sets = longest("W")
    ll, ll_sets = longest("L")
    if runs:
        run = runs[-1]
        current = f"{run[2] - run[1] + 1}{run[0]}"
        current_sets = df.iloc[run[1]:run[2] + 1]
    else:
        current, current_sets = "—", df.iloc[0:0]
    return {
        "longest_w": lw, "longest_w_sets": lw_sets,
        "longest_l": ll, "longest_l_sets": ll_sets,
        "current": current, "current_sets": current_sets,
    }


def ordinal(n) -> str:
    if pd.isna(n):
        return "—"
    n = int(n)
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

# Bump when the DataFrame schema changes: it becomes part of the cache key,
# so stale cached frames from an older code version are never served.
DATA_VERSION = 4


@st.cache_data(ttl=3600, show_spinner="Fetching full set history from start.gg (~1-2 min)...")
def load_data(version: int):
    return fetch_player_data()


def chart_colors() -> tuple[str, str]:
    """(line, bar) hex for the active streamlit theme.

    Purple/pink palette validated (contrast + CVD separation) against the
    surfaces in .streamlit/config.toml.
    """
    if theme_is_dark():
        return "#9b78ea", "#5a3a68"
    return "#7c3aed", "#e3c5f2"


def muted_ink() -> str:
    """Muted label color for direct chart labels."""
    return "#c9a9bd" if theme_is_dark() else "#8a6f80"


def win_rate_chart(played: pd.DataFrame):
    """Cumulative (all-time-so-far) win rate, one point after each event."""
    line_c, _ = chart_colors()
    df = played.sort_values("date").reset_index(drop=True)
    wins = (df["result"] == "W").cumsum()
    total = pd.Series(range(1, len(df) + 1))
    df["cum_rate"] = wins / total * 100
    df["cum_record"] = wins.astype(str) + "–" + (total - wins).astype(str)

    # last set of each event = win rate at the moment that event wrapped up
    per_event = df.groupby("event_id", sort=False).tail(1)

    return (
        alt.Chart(per_event)
        .mark_line(color=line_c, strokeWidth=2, point={"filled": True, "size": 30, "color": line_c})
        .encode(
            x=alt.X("date:T", title=None),
            y=alt.Y("cum_rate:Q", title="Total win rate (%)", scale=alt.Scale(domain=[0, 100])),
            tooltip=[
                alt.Tooltip("tournament:N", title="Tournament"),
                alt.Tooltip("event:N", title="Event"),
                alt.Tooltip("date:T", format="%d %b %Y", title="Date"),
                alt.Tooltip("cum_rate:Q", format=".1f", title="Win rate %"),
                alt.Tooltip("cum_record:N", title="Record"),
            ],
        )
        .properties(height=320)
    )


def h2h_table(played: pd.DataFrame) -> pd.DataFrame:
    df = played.dropna(subset=["opponent"]).copy()
    if df.empty:
        return pd.DataFrame(columns=["opponent", "sets", "W", "L", "win_pct", "last_played"])
    df["key"] = df["opponent_id"].astype("string")
    df.loc[df["key"].isna(), "key"] = "name:" + df.loc[df["key"].isna(), "opponent"]
    df = df.sort_values("date")
    g = df.groupby("key")
    out = pd.DataFrame({
        "opponent": g["opponent"].last(),
        "sets": g.size(),
        "W": g["result"].apply(lambda r: int((r == "W").sum())),
        "last_played": g["date"].max(),
    })
    out["L"] = out["sets"] - out["W"]
    out["win_pct"] = out["W"] / out["sets"] * 100
    return (
        out[["opponent", "sets", "W", "L", "win_pct", "last_played"]]
        .sort_values(["sets", "last_played"], ascending=False)
        .reset_index(drop=True)
    )


def event_cards(events: pd.DataFrame, all_sets: pd.DataFrame):
    per_page = 12
    total_pages = max(1, math.ceil(len(events) / per_page))
    top_l, top_r = st.columns([3, 1])
    top_l.caption(f"{len(events)} events match the current filters")
    page = top_r.selectbox("Page", range(1, total_pages + 1), label_visibility="collapsed")
    rows = events.iloc[(page - 1) * per_page: page * per_page]

    cols = st.columns(3)
    medals = {1: "🥇 ", 2: "🥈 ", 3: "🥉 "}
    for i, row in enumerate(rows.itertuples()):
        with cols[i % 3].container(border=True):
            st.markdown(f"**{row.tournament}**")
            date = "" if pd.isna(row.start) else f" · {row.start:%d %b %Y}"
            st.caption(f"{row.event} · {row.game}{date}")
            medal = medals.get(row.placement, "")
            entrants = "?" if pd.isna(row.entrants) else int(row.entrants)
            st.markdown(f"### {medal}{ordinal(row.placement)} of {entrants}")
            seed = "" if pd.isna(row.seed) else f"Seed {int(row.seed)} · "
            dq = " · DQ" if row.dq else ""
            st.caption(f"{seed}Sets {row.sets_won}–{row.sets_lost}{dq}")
            ev_sets = all_sets[all_sets["event_id"] == row.event_id]
            with st.expander(f"Set history ({len(ev_sets)})"):
                st.dataframe(
                    ev_sets[["round", "opponent", "score", "result"]],
                    hide_index=True,
                    width="stretch",
                )


# ---------------------------------------------------------------------------
# Draft extras — additional charts appended below the main dashboard
# ---------------------------------------------------------------------------

SCORELINE_RE = re.compile(r"^(.*)\s(\d+)\s-\s(.*)\s(\d+)$")


def theme_is_dark() -> bool:
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return False


def scoreline(row) -> str | None:
    """Set score like '3–1' from the player's perspective."""
    s = row["score"]
    if not isinstance(s, str):
        return None
    m = SCORELINE_RE.match(s)
    if not m:
        return None
    name1, s1, s2 = m.group(1), int(m.group(2)), int(m.group(4))
    mine, theirs = (s2, s1) if row["opponent"] and name1 == row["opponent"] else (s1, s2)
    if max(mine, theirs) > 9:  # crew battles / stock counts, not set scores
        return None
    return f"{mine}–{theirs}"


def placement_dist_chart(ev: pd.DataFrame):
    line_c, _ = chart_colors()
    p = ev["placement"].dropna().astype(int)
    if p.empty:
        return None
    labels = p.map(lambda n: ordinal(n) if n < 17 else "17th+")
    counts = labels.value_counts().reset_index()
    counts.columns = ["placement", "events"]
    order = sorted(
        counts["placement"].unique(),
        key=lambda s: 999 if s == "17th+" else int(re.match(r"\d+", s).group()),
    )
    bars = alt.Chart(counts).mark_bar(color=line_c, cornerRadiusEnd=4).encode(
        x=alt.X("placement:N", sort=order, title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("events:Q", title="Events"),
        tooltip=[alt.Tooltip("placement:N", title="Placement"),
                 alt.Tooltip("events:Q", title="Events")],
    )
    text = bars.mark_text(dy=-8, color=muted_ink()).encode(text="events:Q")
    return (bars + text).properties(height=280)


# Placements a double-elim bracket can actually produce. A seed projects to the
# highest of these at or below it: seeds 5-6 project 5th, 9-12 project 9th, etc.
BRACKET_PLACEMENTS = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513]


def projected_placement(seed: int) -> int:
    return max(p for p in BRACKET_PLACEMENTS if p <= seed)


def seed_perf_chart(ev: pd.DataFrame):
    df = ev.dropna(subset=["seed", "placement"]).copy()
    if df.empty:
        return None
    df["seed"] = df["seed"].astype(int)
    df["placement"] = df["placement"].astype(int)
    df["expected"] = df["seed"].map(projected_placement)
    df["performance"] = df.apply(
        lambda r: "Beat seed" if r["placement"] < r["expected"]
        else ("Below seed" if r["placement"] > r["expected"] else "Matched seed"),
        axis=1,
    )
    purple = "#9b78ea" if theme_is_dark() else "#7c3aed"
    pink = "#df4f9b" if theme_is_dark() else "#d6367f"
    grey = "#8f7f8a" if theme_is_dark() else "#9b8b95"
    grid = "#4a3145" if theme_is_dark() else "#d9c2d2"

    lim = int(max(df["expected"].max(), df["placement"].max()))
    scale = alt.Scale(type="log", domain=[1, lim])
    points = alt.Chart(df).mark_circle(size=60, opacity=0.75).encode(
        x=alt.X("expected:Q", scale=scale, title="Expected placing (from seed)"),
        y=alt.Y("placement:Q", scale=scale, title="Placement"),
        color=alt.Color(
            "performance:N",
            scale=alt.Scale(domain=["Beat seed", "Below seed", "Matched seed"],
                            range=[purple, pink, grey]),
            legend=alt.Legend(title=None, orient="top"),
        ),
        tooltip=[
            alt.Tooltip("tournament:N", title="Tournament"),
            alt.Tooltip("event:N", title="Event"),
            alt.Tooltip("seed:Q", title="Seed"),
            alt.Tooltip("expected:Q", title="Expected placing"),
            alt.Tooltip("placement:Q", title="Placement"),
            alt.Tooltip("entrants:Q", title="Entrants"),
            alt.Tooltip("start:T", format="%d %b %Y", title="Date"),
        ],
    )
    diag = alt.Chart(pd.DataFrame({"v": [1, lim]})).mark_line(
        color=grid, strokeDash=[4, 4],
    ).encode(x=alt.X("v:Q", scale=scale), y=alt.Y("v:Q", scale=scale))
    return (diag + points).properties(height=280)


def scoreline_chart(played: pd.DataFrame):
    line_c, _ = chart_colors()
    s = played.apply(scoreline, axis=1).dropna()
    if s.empty:
        return None
    counts = s.value_counts().head(10).reset_index()
    counts.columns = ["scoreline", "sets"]
    bars = alt.Chart(counts).mark_bar(color=line_c, cornerRadiusEnd=4).encode(
        y=alt.Y("scoreline:N", sort="-x", title=None),
        x=alt.X("sets:Q", title="Sets"),
        tooltip=[alt.Tooltip("scoreline:N", title="Score"),
                 alt.Tooltip("sets:Q", title="Sets")],
    )
    text = bars.mark_text(dx=8, align="left", color=muted_ink()).encode(text="sets:Q")
    return (bars + text).properties(height=280)


def round_side(text) -> str:
    t = (text or "").lower()
    if "grand" in t:
        return "Grand finals"
    if "losers" in t:
        return "Losers side"
    if "winners" in t:
        return "Winners side"
    return "Pools / other"


def bracket_side_chart(played: pd.DataFrame):
    line_c, _ = chart_colors()
    df = played.assign(side=played["round"].map(round_side))
    g = df.groupby("side")
    out = pd.DataFrame({
        "sets": g.size(),
        "wins": g["result"].apply(lambda r: int((r == "W").sum())),
    }).reset_index()
    if out.empty:
        return None
    out["win_rate"] = out["wins"] / out["sets"] * 100
    out["label"] = out.apply(
        lambda r: f"{r['win_rate']:.0f}%  ({r['wins']}–{int(r['sets'] - r['wins'])})", axis=1
    )
    order = ["Winners side", "Losers side", "Grand finals", "Pools / other"]
    bars = alt.Chart(out).mark_bar(color=line_c, cornerRadiusEnd=4).encode(
        y=alt.Y("side:N", sort=order, title=None),
        x=alt.X("win_rate:Q", title="Win rate (%)", scale=alt.Scale(domain=[0, 100])),
        tooltip=[alt.Tooltip("side:N", title="Bracket side"),
                 alt.Tooltip("label:N", title="Win rate")],
    )
    text = bars.mark_text(dx=8, align="left", color=muted_ink()).encode(text="label:N")
    return (bars + text).properties(height=200)


def activity_chart(ev: pd.DataFrame):
    _, bar_c = chart_colors()
    df = ev.dropna(subset=["start"])
    if df.empty:
        return None
    m = df.set_index("start").resample("MS").size().reset_index(name="events")
    m = m[m["events"] > 0]
    m["month_end"] = m["start"] + pd.offsets.MonthEnd(0) - pd.Timedelta(days=1)
    return alt.Chart(m).mark_bar(color=bar_c, cornerRadius=2).encode(
        x=alt.X("start:T", title=None),
        x2="month_end:T",
        y=alt.Y("events:Q", title="Events"),
        tooltip=[alt.Tooltip("start:T", format="%b %Y", title="Month"),
                 alt.Tooltip("events:Q", title="Events")],
    ).properties(height=200)


def show_chart(chart):
    if chart is None:
        st.caption("Not enough data in the current filters.")
    else:
        st.altair_chart(chart, width="stretch")


def main():
    st.set_page_config(page_title="Pascal 🦋 start.gg", page_icon="🦋", layout="wide")

    user, events_df, sets_df = load_data(DATA_VERSION)
    player = user["player"]

    # ---- sidebar filters -------------------------------------------------
    st.sidebar.header("🦋 Filters")

    games = sorted(events_df["game"].dropna().unique())
    default_games = [g for g in games if g == "Super Smash Bros. Ultimate"] or games
    sel_games = st.sidebar.multiselect("Games", games, default=default_games)

    sel_brackets = st.sidebar.multiselect(
        "Bracket types", BRACKET_TYPES, default=["Singles"],
    )

    include_amateur = st.sidebar.toggle(
        "Include amateur brackets", value=True,
        help="Ammies, amateur, redemption, arcadian brackets",
    )

    PRESETS = {
        "All time": None,
        "Last month": 1,
        "Last 3 months": 3,
        "Last 6 months": 6,
        "Last year": 12,
        "Custom": "custom",
    }
    preset = st.sidebar.radio("Period", list(PRESETS))
    now = pd.Timestamp.now()
    start_at, end_at = pd.Timestamp.min, pd.Timestamp.max
    if PRESETS[preset] == "custom":
        d_min = sets_df["date"].min()
        picked = st.sidebar.date_input(
            "Date range", (d_min.date(), now.date()),
            min_value=d_min.date(), max_value=now.date(),
        )
        if len(picked) == 2:
            start_at = pd.Timestamp(picked[0])
            end_at = pd.Timestamp(picked[1]) + pd.Timedelta(days=1)
    elif PRESETS[preset]:
        start_at = now - pd.DateOffset(months=PRESETS[preset])

    def base_filter(df):
        keep = df["game"].isin(sel_games) & df["bracket"].isin(sel_brackets)
        if not include_amateur:
            keep &= ~df["amateur"]
        return df[keep]

    sets_base = base_filter(sets_df)          # game/bracket/amateur only
    date_col = sets_base["date"]
    sets_f = sets_base[(date_col >= start_at) & (date_col < end_at)]
    ev_base = base_filter(events_df)
    ev_f = ev_base[(ev_base["start"] >= start_at) & (ev_base["start"] < end_at)]

    played = playable(sets_f).sort_values("date")       # date-filtered stats
    played_all = playable(sets_base).sort_values("date")  # for recent form

    # ---- header + KPIs ---------------------------------------------------
    tag = f"{player['prefix']} | {player['gamerTag']}" if player["prefix"] else player["gamerTag"]
    st.title(f"🦋 {tag}")
    st.caption(
        f"start.gg [{user['slug']}](https://start.gg/{user['slug']}) · "
        f"{preset} · DQs excluded from all stats"
    )

    wr = win_rate(played)
    streaks = streak_stats(played)
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Win rate", "—" if wr is None else f"{wr:.1f}%")
    k2.metric("Record", record(played))
    k3.metric("Events", len(ev_f))
    k4.metric("Trophies", int((ev_f["placement"] == 1).sum()))
    k5.metric("Current streak", streaks["current"])

    # ---- recent form (always now-relative, ignores period filter) --------
    st.subheader("🦋 Recent form")
    wr_all = win_rate(played_all)
    cols = st.columns(3)
    for col, (label, months) in zip(
        cols, [("Last month", 1), ("Last 3 months", 3), ("Last 6 months", 6)]
    ):
        window = played_all[played_all["date"] >= now - pd.DateOffset(months=months)]
        w = win_rate(window)
        delta = None
        if w is not None and wr_all is not None:
            delta = f"{w - wr_all:+.1f}% vs all-time"
        col.metric(label, "—" if w is None else f"{w:.1f}%", delta=delta,
                   help=f"{record(window)} over {len(window)} sets")

    # ---- win rate over time ----------------------------------------------
    st.subheader("🦋 Win rate over time")
    if len(played) < 10:
        st.info("Not enough sets in the current filters to chart.")
    else:
        st.caption("Total win rate after each event, within the current filters")
        st.altair_chart(win_rate_chart(played), width="stretch")

    # ---- streaks -----------------------------------------------------------
    st.subheader("🦋 Streaks")
    streak_cols = ["date", "tournament", "round", "opponent", "score"]
    for col, (label, value, sets_key) in zip(st.columns(3), [
        ("Longest win streak", streaks["longest_w"], "longest_w_sets"),
        ("Longest losing streak", streaks["longest_l"], "longest_l_sets"),
        ("Current", streaks["current"], "current_sets"),
    ]):
        with col:
            st.metric(label, value)
            streak_sets = streaks[sets_key]
            if not streak_sets.empty:
                with st.expander("Sets in streak"):
                    st.dataframe(
                        streak_sets[streak_cols].sort_values("date", ascending=False),
                        hide_index=True,
                        width="stretch",
                        column_config={"date": st.column_config.DateColumn("date", format="DD MMM YY")},
                    )

    # ---- locals split --------------------------------------------------------
    st.subheader("🦋 Locals vs other events")
    summary = []
    for label, is_local in [("Big Fish weeklies", True), ("Other events", False)]:
        part = played[played["local"] == is_local]
        evs = ev_f[ev_f["local"] == is_local]
        w = win_rate(part)
        summary.append({
            "": label,
            "events": len(evs),
            "record": record(part),
            "win rate": "—" if w is None else f"{w:.1f}%",
        })
    st.dataframe(pd.DataFrame(summary), hide_index=True, width="stretch")

    # ---- head to head ------------------------------------------------------
    st.subheader("🦋 Head to head")
    h2h = h2h_table(played)
    search = st.text_input("Search opponent", placeholder="e.g. Ronan")
    if search:
        h2h = h2h[h2h["opponent"].str.contains(search, case=False, na=False)]
    st.dataframe(
        h2h,
        hide_index=True,
        width="stretch",
        height=400,
        column_config={
            "opponent": "Opponent",
            "sets": st.column_config.NumberColumn("Sets"),
            "W": st.column_config.NumberColumn("W"),
            "L": st.column_config.NumberColumn("L"),
            "win_pct": st.column_config.ProgressColumn(
                "Win %", format="%.0f%%", min_value=0, max_value=100,
            ),
            "last_played": st.column_config.DateColumn("Last played", format="DD MMM YYYY"),
        },
    )

    # ---- events as cards ---------------------------------------------------
    st.subheader("🦋 Events")
    event_cards(ev_f, sets_f)

    # ---- draft extras --------------------------------------------------------
    st.divider()
    st.header("🦋 More stats (draft)")
    if played.empty:
        st.info("No sets in the current filters.")
        return

    st.subheader("🦋 Matchup highlights")
    full_h2h = h2h_table(played)
    eligible = full_h2h[full_h2h["sets"] >= 5]
    if eligible.empty:
        st.caption("Needs an opponent with 5+ sets in the current filters.")
    else:
        nem = eligible.sort_values(["win_pct", "sets"], ascending=[True, False]).iloc[0]
        best = eligible.sort_values(["win_pct", "sets"], ascending=[False, False]).iloc[0]
        most = full_h2h.iloc[0]
        for col, title, r in zip(
            st.columns(3),
            ["Nemesis", "Best matchup", "Most played"],
            [nem, best, most],
        ):
            col.metric(title, r["opponent"])
            col.caption(f"{int(r['W'])}–{int(r['L'])} ({r['win_pct']:.0f}% over {int(r['sets'])} sets)")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("🦋 Placement distribution")
        show_chart(placement_dist_chart(ev_f))
        st.subheader("🦋 Scorelines")
        st.caption("Set scores from Pascal's perspective · 10 most common")
        show_chart(scoreline_chart(played))
        st.subheader("🦋 Events per month")
        show_chart(activity_chart(ev_f))
    with c2:
        st.subheader("🦋 Seed vs placement")
        st.caption("One dot per event · below the dashed line = beat the seed · log scales")
        show_chart(seed_perf_chart(ev_f))
        st.subheader("🦋 Win rate by bracket side")
        show_chart(bracket_side_chart(played))


if __name__ == "__main__":
    main()
