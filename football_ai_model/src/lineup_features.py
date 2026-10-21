"""
Turn starting XIs into numbers the model can use.

For every team we look at its recent line-ups (most recent counts most) and
work out how often each player starts. A new line-up is then scored on:

  strength  share of the usual XI that is starting (1.0 = full strength)
  missing   regulars (start 60%+ of recent games) who are not starting
  gk        1 if the usual goalkeeper is not starting
  changes   players changed from the team's previous match (0 to 11)

Only matches before the one being scored are used, so nothing leaks.
"""

from collections import defaultdict, deque

HISTORY = 10          # recent matches used to judge who the regulars are
DECAY = 0.85          # weight of a match k games ago = DECAY ** k
REGULAR_SHARE = 0.6
MIN_HISTORY = 3       # with fewer matches we can't tell, so assume full strength

FEATURES = ["strength_deficit", "missing", "gk", "changes"]
NEUTRAL = {"strength_deficit": 0.0, "missing": 0.0, "gk": 0.0, "changes": 0.0}


def parse_starters(text: str) -> list:
    """'id:pos|id:pos' -> [(id, pos), ...]"""
    players = []
    for item in (text or "").split("|"):
        if item:
            pid, _, pos = item.partition(":")
            players.append((pid, pos))
    return players


class TeamHistory:
    def __init__(self):
        self.matches = deque(maxlen=HISTORY)  # most recent last: list of (id, pos)

    def score(self, starters: list) -> dict:
        if len(self.matches) < MIN_HISTORY:
            return dict(NEUTRAL)
        weights = [DECAY ** k for k in range(len(self.matches))]
        total = sum(weights)
        share: dict = defaultdict(float)
        keeper_share: dict = defaultdict(float)
        for weight, match in zip(weights, reversed(self.matches)):
            for pid, pos in match:
                share[pid] += weight / total
                if pos == "G":
                    keeper_share[pid] += weight / total

        today = {pid for pid, _ in starters}
        usual = sum(sorted(share.values(), reverse=True)[:11]) or 1.0
        strength = min(sum(share.get(pid, 0.0) for pid in today) / usual, 1.0)
        usual_keeper = max(keeper_share, key=keeper_share.get) if keeper_share else None
        last = {pid for pid, _ in self.matches[-1]}
        return {
            "strength_deficit": round(1.0 - strength, 4),
            "missing": float(sum(1 for pid, s in share.items() if s >= REGULAR_SHARE and pid not in today)),
            "gk": float(bool(usual_keeper and keeper_share[usual_keeper] >= REGULAR_SHARE and usual_keeper not in today)),
            "changes": float(len(today - last)),
        }

    def add(self, starters: list) -> None:
        self.matches.append(starters)


def build_histories(rows: list, before: str | None = None) -> dict:
    """{espn_team: TeamHistory} from stored line-up rows (kickoff < before)."""
    histories: dict = defaultdict(TeamHistory)
    for row in sorted(rows, key=lambda r: r["kickoff"]):
        if before and row["kickoff"] >= before:
            break
        starters = parse_starters(row["starters"])
        if len(starters) >= 11:
            histories[row["espn_team"]].add(starters)
    return histories


def match_features(rows: list) -> dict:
    """Pre-match line-up features for every stored match.

    Returns {event_id: {"home": {...}, "away": {...}, "row_home": row, "row_away": row}}.
    """
    histories: dict = defaultdict(TeamHistory)
    by_event: dict = defaultdict(dict)
    for row in sorted(rows, key=lambda r: (r["kickoff"], r["event_id"], r["side"])):
        starters = parse_starters(row["starters"])
        if len(starters) < 11:
            continue
        history = histories[row["espn_team"]]
        by_event[row["event_id"]][row["side"]] = history.score(starters)
        by_event[row["event_id"]]["row_" + row["side"]] = row
        history.add(starters)
    return {event: data for event, data in by_event.items() if "home" in data and "away" in data}


def feature_vector(home: dict, away: dict) -> list:
    """Order used by the line-up layer: home features then away features."""
    return [home[name] for name in FEATURES] + [away[name] for name in FEATURES]
