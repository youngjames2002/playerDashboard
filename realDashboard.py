import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px

st.set_page_config(layout="wide")

conn = sqlite3.connect('sets.db')
df = pd.read_sql_query("SELECT * FROM sets", conn)
conn.close()

# convert date columns
df["event_start"] = pd.to_datetime(df["event_start"], unit="s") # event date
df["completed_at"] = pd.to_datetime(df["completed_at"], unit="s") # set date
# sort by date
df = df.sort_values("completed_at")

# title + custom date range filter (filters both events_df and sets_df since they derive from df)
title_col, start_date_col, end_date_col = st.columns([3, 1, 1])
title_col.title("PASCAL DASHBOARD")
min_event_date = df["event_start"].min().date()
max_event_date = df["event_start"].max().date()
start_date = start_date_col.date_input("Start Date", value=min_event_date, min_value=min_event_date, max_value=max_event_date)
end_date = end_date_col.date_input("End Date", value=max_event_date, min_value=min_event_date, max_value=max_event_date)
df = df[(df["event_start"].dt.date >= start_date) & (df["event_start"].dt.date <= end_date)]

#filters
fc1,fc2,fc3 = st.columns(3)
# filter by game
df.loc[df["event_name"].str.lower().str.contains("hdr"), "game"] = "HewDraw Remix" #hdr brackets sometimes marked as ult singles
games = df["game"].value_counts().index.tolist()
game = fc1.selectbox(options=games, label="Game:")
if game:
    df = df[df["game"] == game]

# categorize by bracket type
bracket_types = ["Singles", "Teams", "Ammies", "Side Events"]
df["bracket_type"] = "Singles"
df.loc[df["event_name"].str.lower().str.contains("doubles|dubs|triples|dubbies|crews|2v2"), "bracket_type"] = "Teams"
df.loc[df["event_name"].str.lower().str.contains("ammies|amateur|silver|bronze|intermediate|redemption|ladder|arcadian"), "bracket_type"] = "Ammies"
df.loc[df["event_name"].str.lower().str.contains("squad|random|reverse|doc day|side|faso|only|smashdown|msr|steve|wario|meter|giant|hazards"), "bracket_type"] = "Side Events"

# filter by bracket type
bracket_type = fc2.selectbox(options=bracket_types, label="Bracket Type: ")
if bracket_type:
    df = df[df["bracket_type"] == bracket_type]

# filter by big fish or not
fish_filter_options = ["All Events","Just Fish", "Just Not Fish",]
fish_filter = fc3.selectbox(options=fish_filter_options,label="Fish Filter: ")
if fish_filter == "Just Fish":
    df = df[df["tournament_name"].str.lower().str.contains("fish|pond")]
elif fish_filter == "Just Not Fish":
    df = df[~df["tournament_name"].str.lower().str.contains("fish|pond")]


# event list df
events_df = df[["event_name", "tournament_name", "game", "event_entrants", "seed", "placement", "event_start"]]
events_df = events_df.drop_duplicates(subset=["tournament_name", "event_name"])
# add month column to events
events_df["month"] = events_df["event_start"].dt.strftime('%Y-%m')

# set list df
sets_df = df[["event_name", "tournament_name", "game", "opponent_name", "opponent_id", "round", "score", "result", "completed_at"]]
# add month column to sets
sets_df["month"] = sets_df["completed_at"].dt.strftime('%Y-%m')

# stats metrics
c1,c2,c3 = st.columns(3)
# win rate
def get_win_rate(sets_df):
    sets_won = len(sets_df[sets_df["result"]=="W"])
    total_sets = len(sets_df)
    win_rate = round((sets_won / total_sets)*100, 1) if total_sets else 0.0
    return win_rate, sets_won, total_sets
sets_df = sets_df[sets_df["score"] != "DQ"] # exclude dqs for stats calculation
win_rate_all_time, sets_won_all_time, total_sets_all_time=get_win_rate(sets_df)
c1.metric(
    f"win rate all time:",
    f"{win_rate_all_time}% ({sets_won_all_time} - {total_sets_all_time-sets_won_all_time})"
)

# events attended
total_events = len(events_df)
c2.metric(f"total events: ",total_events)

# average placement
if len(events_df):
    avg_placement = round(events_df["placement"].mean(),1)
    avg_entrants = round(events_df["event_entrants"].mean(),1)
    c3.metric(f"average placement: ", f"{avg_placement} out of {avg_entrants}")
else:
    c3.metric(f"average placement: ", "N/A")

# add recent form win rates, compared against all-time win rate
now = pd.Timestamp.now()
recent_form_periods = [
    ("month", 30),
    ("6 months", 180),
    ("year", 365),
]
recent_form_cols = st.columns(len(recent_form_periods))
for col, (label, days) in zip(recent_form_cols, recent_form_periods):
    period_sets = sets_df[sets_df["completed_at"] > now - pd.Timedelta(days=days)]
    win_rate, sets_won, total_sets = get_win_rate(period_sets)
    col.metric(
        f"win rate last {label}",
        f"{win_rate}% ({sets_won} - {total_sets - sets_won})",
        delta=f"{win_rate - win_rate_all_time:+.2f}% vs all-time",
        delta_color="off" if total_sets == 0 else "normal",
    )


# graphs
gc1, gc2  = st.columns(2)
# win rate over time (cumulative per-set, so months with few sets don't skew it as much as months with many)
sets_df["cum_wins"] = sets_df["result"].eq("W").cumsum()
sets_df["cum_sets"] = range(1, len(sets_df) + 1)
sets_df["cum_win_rate"] = round(sets_df["cum_wins"] / sets_df["cum_sets"] * 100, 2)

win_rate_over_time = sets_df.groupby("month")["cum_win_rate"].last().reset_index()
win_rate_over_time = win_rate_over_time.rename(columns={"cum_win_rate": "average_after_month"})

win_rate_over_time["month_date"] = pd.to_datetime(win_rate_over_time["month"], format="%Y-%m")
win_rate_default_start = win_rate_over_time["month_date"].iloc[max(0, len(win_rate_over_time) - 10)] if len(win_rate_over_time) else None
win_rate_default_end = win_rate_over_time["month_date"].iloc[-1] if len(win_rate_over_time) else None

win_rate_fig = px.line(win_rate_over_time, x="month_date", y="average_after_month", markers=True)
win_rate_fig.update_traces(
    marker=dict(size=7, symbol="circle", color="#ffffff", line=dict(width=2, color="#1f77b4")),
    line=dict(width=2),
)
win_rate_fig.update_xaxes(
    rangeslider=dict(visible=True, thickness=0.08, bgcolor="#e9e9e9", bordercolor="#c4c4c4", borderwidth=1),
    tickformat="%b %Y",
    range=[win_rate_default_start, win_rate_default_end],
)
gc1.markdown("## Win Rate Over Time")
gc1.plotly_chart(win_rate_fig, use_container_width=True)

# events entered over time
events_graph_df = events_df.groupby("month").size().reset_index(name="tournaments")
events_graph_df["month_date"] = pd.to_datetime(events_graph_df["month"], format="%Y-%m")
axis_labels = {"month_date": "Date", "tournaments": "# Events Entered"}
default_start = events_graph_df["month_date"].iloc[max(0, len(events_graph_df) - 10)] if len(events_graph_df) else None
default_end = events_graph_df["month_date"].iloc[-1] if len(events_graph_df) else None

events_fig = px.bar(events_graph_df, x="month_date", y="tournaments", labels=axis_labels)
events_fig.update_xaxes(
    rangeslider=dict(visible=True, thickness=0.08, bgcolor="#e9e9e9", bordercolor="#c4c4c4", borderwidth=1),
    tickformat="%b %Y",
    range=[default_start, default_end],
)
gc2.markdown("## Events Over Time")
gc2.plotly_chart(events_fig, use_container_width=True)

# seed performance
# placement distribution

# event history
# each event and placement and SPR
# list of sets played

# are streaks interesting?
# h2h stuff
# should check old pgstats videos for ideas
# include PR seasons ?? hardcoded event lists?
# hardcode a best wins section?? - bit gay

# DEBUG DATAFRAMES
st.write("debug datafrmaes")
st.write("event list df")
st.dataframe(events_df)
st.write("sets list df")
st.dataframe(sets_df)