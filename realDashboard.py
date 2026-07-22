import streamlit as st
import pandas as pd
import sqlite3

conn = sqlite3.connect('sets.db')
df = pd.read_sql_query("SELECT * FROM sets", conn)
conn.close()

# convert date columns
df["event_start"] = pd.to_datetime(df["event_start"], unit="s") # event date
df["completed_at"] = pd.to_datetime(df["completed_at"], unit="s") # set date
# sort by date
df = df.sort_values("completed_at")

# filter by game
df.loc[df["event_name"].str.lower().str.contains("hdr"), "game"] = "HewDraw Remix" #hdr brackets sometimes marked as ult singles
games = df["game"].value_counts().index.tolist()
game = st.selectbox(options=games, label="Game:")
if game:
    df = df[df["game"] == game]

# categorize by bracket type
bracket_types = ["Singles", "Teams", "Ammies", "Side Events"]
df["bracket_type"] = "Singles"
df.loc[df["event_name"].str.lower().str.contains("doubles|dubs|triples|dubbies|crews"), "bracket_type"] = "Teams"
df.loc[df["event_name"].str.lower().str.contains("ammies|amateur|silver|bronze|intermediate|redemption|ladder|arcadian"), "bracket_type"] = "Ammies"
df.loc[df["event_name"].str.lower().str.contains("squad|random|reverse|doc day|side|faso|only|smashdown|msr|steve|wario|meter|giant|hazards"), "bracket_type"] = "Side Events"


# filter by bracket type
bracket_type = st.selectbox(options=bracket_types, label="Bracket Type: ")
if bracket_type:
    df = df[df["bracket_type"] == bracket_type]

# event list df
events_df = df[["event_name", "tournament_name", "game", "event_entrants", "seed", "placement", "event_start"]]
events_df = events_df.drop_duplicates(subset=["tournament_name", "event_name"])

st.write("event list df")
st.dataframe(events_df)
# set list df
sets_df = df[["event_name", "tournament_name", "game", "opponent_name", "opponent_id", "round", "score", "result", "completed_at"]]

st.write("sets list df")
st.dataframe(sets_df)

# stats
# win rate
sets_df = sets_df[sets_df["score"] != "DQ"] # exclude dqs for stats calculation
sets_won = len(sets_df[sets_df["result"]=="W"])
total_sets = len(sets_df)
win_rate = round((sets_won / total_sets)*100, 2)
st.write(f"win rate: {win_rate}% ({sets_won} - {total_sets-sets_won})")

# add recent form win rates

# events attended
total_events = len(events_df)
st.write(f"total events: {total_events}")

# average placement
avg_placement = round(events_df["placement"].mean(),2)
avg_entrants = round(events_df["event_entrants"].mean(),2)
st.write(f"average placement: {avg_placement} out of {avg_entrants}")

# graphs
# win rate over time
# seed performance
# placement distribution
# events entered over time

# event history
# each event and placement and SPR
# list of sets played

# are streaks interesting?
# big fish filter
# h2h stuff
# should check old pgstats videos for ideas
# include PR seasons ??
# hardcode a best wins section?? - bit gay