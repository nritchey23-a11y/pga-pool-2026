#!/usr/bin/env python3
"""
Fetch live PGA Championship scores from ESPN and compute pool standings.
Outputs pool_scores.json matching the StandingsResponse format the dashboard expects.
"""
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
import sys
import os
import re

ESPN_URL = "https://site.web.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"

# Augusta National pars (fixed)
AUGUSTA_PARS = [4, 4, 4, 4, 3, 4, 4, 3, 5, 4, 4, 4, 4, 3, 4, 5, 3, 4]  # Aronimink par 70

# ── Teams ─────────────────────────────────────────────────────
TEAMS = {
    'Pat':          ['Scottie Scheffler', 'Jordan Spieth', 'Adam Scott', 'Jacob Bridgeman', 'Ben Griffin', 'Wyndham Clark'],
    'Trizz':        ['Rory McIlroy', 'Si Woo Kim', 'Shane Lowry', 'Sungjae Im', 'Min Woo Lee', 'Marco Penge'],
    'Kelly':        ['Cameron Young', 'Patrick Cantlay', 'Chris Gotterup', 'Gary Woodland', 'Sam Burns', 'Harry Hall'],
    'Berit':        ['Bryson DeChambeau', 'Russell Henley', 'Viktor Hovland', 'Harris English', 'Kristoffer Reitan', 'Sudarshan Yellamaraju'],
    'Justin (Doc)': ['Matt Fitzpatrick', 'Patrick Reed', 'Rickie Fowler', 'J.J. Spaun', 'Alex Fitzpatrick', 'Matt McCarty'],
    'Wheats':       ['Tommy Fleetwood', 'Justin Thomas', 'Denny McCarthy', 'Brian Harman', 'Alex Noren', 'Daniel Berger'],
    'JB':           ['Xander Schauffele', 'Tyrrell Hatton', 'Robert MacIntyre', 'Joaquin Niemann', 'Thomas Detry', 'Nick Taylor'],
    'Nick':         ['Ludvig Aberg', 'Hideki Matsuyama', 'Corey Conners', 'Sepp Straka', 'Keegan Bradley', 'Sahith Theegala'],
    'Ian':          ['Jon Rahm', 'Brooks Koepka', 'Jason Day', 'Akshay Bhatia', 'Nicolai Hojgaard', 'Rasmus Hojgaard'],
    'Ben':          ['Collin Morikawa', 'Justin Rose', 'Maverick McNealy', 'Kurt Kitayama', 'Ryan Gerard', 'Michael Thorbjornsen'],
}

# ── Scoring rules ─────────────────────────────────────────────
HOLE_POINTS = {
    "double_eagle": 20, "hole_in_one": 15, "eagle": 5,
    "birdie": 2, "par": 0, "bogey": -1, "double_bogey_plus": -3,
}
STREAK_BASE = 3
BOGEY_FREE_BONUS = 3
ALL_ROUNDS_IN_60S_BONUS = 5
CUT_PENALTY = -10

FINISH_POINTS = {
    1: 15, 2: 12, 3: 11, 4: 10, 5: 9, 6: 8, 7: 7, 8: 6, 9: 5, 10: 4,
    **{i: 3 for i in range(11, 21)}, **{i: 2 for i in range(21, 31)},
    **{i: 1 for i in range(31, 41)},
}


def normalize(name: str) -> str:
    """Normalize a name for fuzzy matching."""
    n = name.lower().strip()
    # Remove accented characters
    replacements = {
        'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
        'å': 'a', 'ä': 'a', 'ö': 'o', 'ü': 'u', 'ø': 'o',
        'ñ': 'n', 'ø': 'o', 'æ': 'ae', 'ß': 'ss',
        'ø': 'o', 'højgaard': 'hojgaard', 'åberg': 'aberg',
        'garcía': 'garcia', 'olazábal': 'olazabal',
        'válimäki': 'valimaki',
    }
    for old, new in replacements.items():
        n = n.replace(old, new)
    return n


def find_espn_player(competitors: list, target_name: str) -> dict | None:
    """Find a competitor by name matching."""
    target = normalize(target_name)
    for c in competitors:
        espn_name = c.get('athlete', {}).get('displayName', '')
        if normalize(espn_name) == target:
            return c
        # Also try matching with "Matt" → "Matthew" etc.
        if target == "matt fitzpatrick" and normalize(espn_name) == "matt fitzpatrick":
            return c
    return None


def classify_score(score: int, par: int) -> str:
    diff = score - par
    if diff <= -3:
        return "double_eagle"
    elif diff == -2:
        if par == 3 and score == 1:
            return "hole_in_one"
        return "eagle"
    elif diff == -1:
        return "birdie"
    elif diff == 0:
        return "par"
    elif diff == 1:
        return "bogey"
    else:
        return "double_bogey_plus"


def compute_round_scoring(hole_scores: list[int | None], pars: list[int]) -> dict:
    """Compute scoring from hole-by-hole data."""
    stats = {k: 0 for k in ["birdies", "eagles", "bogeys", "doubleBogeys",
                              "doubleEagles", "holesInOne"]}
    hole_points = 0
    has_bogey = False
    round_total = 0
    holes_played = 0
    birdie_streaks = []
    current_streak = 0

    for i, (score, par) in enumerate(zip(hole_scores, pars)):
        if score is None:
            if current_streak >= 3:
                birdie_streaks.append(current_streak)
            current_streak = 0
            continue

        holes_played += 1
        round_total += score
        cat = classify_score(score, par)

        if cat == "double_eagle":
            stats["doubleEagles"] += 1
            hole_points += HOLE_POINTS["double_eagle"]
        elif cat == "hole_in_one":
            stats["holesInOne"] += 1
            hole_points += HOLE_POINTS["hole_in_one"]
        elif cat == "eagle":
            stats["eagles"] += 1
            hole_points += HOLE_POINTS["eagle"]
        elif cat == "birdie":
            stats["birdies"] += 1
            hole_points += HOLE_POINTS["birdie"]
        elif cat == "bogey":
            stats["bogeys"] += 1
            hole_points += HOLE_POINTS["bogey"]
            has_bogey = True
        elif cat == "double_bogey_plus":
            stats["doubleBogeys"] += 1
            hole_points += HOLE_POINTS["double_bogey_plus"]
            has_bogey = True

        # Birdie streak: birdies, eagles, HIOs, double eagles all count
        if cat in ("birdie", "eagle", "hole_in_one", "double_eagle"):
            current_streak += 1
        else:
            if current_streak >= 3:
                birdie_streaks.append(current_streak)
            current_streak = 0

    if current_streak >= 3:
        birdie_streaks.append(current_streak)

    streak_bonus = sum(STREAK_BASE + (s - 3) for s in birdie_streaks)
    max_streak = max(birdie_streaks, default=0)
    bogey_free = (not has_bogey) and (holes_played == 18)
    bogey_free_bonus = BOGEY_FREE_BONUS if bogey_free else 0

    return {
        **stats,
        "birdieStreakMax": max_streak,
        "bogeyFree": bogey_free,
        "roundScore": round_total if holes_played == 18 else None,
        "holePoints": hole_points,
        "streakBonus": streak_bonus,
        "bogeyFreeBonus": bogey_free_bonus,
        "holesPlayed": holes_played,
    }


def extract_hole_scores(competitor: dict, round_num: int) -> list[int | None]:
    """Extract 18-hole score array from ESPN competitor data for a given round."""
    scores = [None] * 18
    for ls in competitor.get("linescores", []):
        if ls.get("period") == round_num:
            for hole in ls.get("linescores", []):
                hole_num = hole.get("period", 0)
                if 1 <= hole_num <= 18:
                    scores[hole_num - 1] = int(hole["value"])
            break
    return scores


def determine_position(competitors: list) -> dict:
    """Determine tournament positions from ESPN ordering, handling ties."""
    # ESPN already provides ordering. We need to figure out positions with ties.
    # Group by score
    sorted_comps = sorted(competitors,
                          key=lambda c: c.get("order", 999))
    
    positions = {}
    # ESPN order is the leaderboard order
    # For ties, ESPN groups them but we need T-notation
    i = 0
    while i < len(sorted_comps):
        c = sorted_comps[i]
        score = c.get("score", "E")
        espn_id = c.get("id")
        
        # Count how many share this score
        j = i + 1
        while j < len(sorted_comps) and sorted_comps[j].get("score") == score:
            j += 1
        
        pos = i + 1  # 1-based position
        for k in range(i, j):
            positions[sorted_comps[k]["id"]] = pos
        i = j
    
    return positions


def fetch_espn_data() -> dict:
    """Fetch live ESPN scoreboard data."""
    req = urllib.request.Request(
        ESPN_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible)"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def compute_standings(espn_data: dict) -> dict:
    """Compute full pool standings from ESPN data."""
    event = espn_data["events"][0]
    comp = event["competitions"][0]
    competitors = comp["competitors"]
    comp_status = comp.get("status", {}).get("type", {})
    current_period = comp.get("status", {}).get("period", 1)
    is_complete = comp_status.get("completed", False) and current_period >= 4
    status_detail = comp_status.get("detail", "")
    
    # Get positions (only meaningful after a round or at end)
    positions = determine_position(competitors)
    
    team_standings = []
    team_id = 1

    for owner_name, roster in TEAMS.items():
        golfer_scorings = []
        golfer_id = team_id * 100

        for draft_round, golfer_name in enumerate(roster, 1):
            player = find_espn_player(competitors, golfer_name)

            if player is None:
                print(f"  WARNING: Could not find '{golfer_name}' in ESPN data", file=sys.stderr)
                golfer_scorings.append(_empty_golfer(golfer_id, golfer_name, team_id, draft_round))
                golfer_id += 1
                continue

            espn_id = player["id"]
            pos = positions.get(espn_id)
            
            # Determine cut status (check if player has round 3/4 data when comp is in later rounds)
            missed_cut = False
            player_score_str = str(player.get("score", "E"))
            # ESPN marks cut players differently — check if they have "CUT" in status
            # For now, we'll determine this by checking if the competition is past round 2
            # and the player has no round 3 data
            
            round_data = []
            per_round_hole_pts = [0, 0, 0, 0]
            per_round_streak = [0, 0, 0, 0]
            per_round_bf = [0, 0, 0, 0]
            round_totals = []

            for rd_idx in range(4):
                rd_num = rd_idx + 1
                hole_scores = extract_hole_scores(player, rd_num)
                has_data = any(s is not None for s in hole_scores)

                if not has_data:
                    round_data.append({
                        "id": golfer_id * 10 + rd_idx, "golferId": golfer_id,
                        "roundNumber": rd_num, "birdies": 0, "eagles": 0,
                        "bogeys": 0, "doubleBogeys": 0, "doubleEagles": 0,
                        "holesInOne": 0, "birdieStreakMax": 0, "bogeyFree": False,
                        "roundScore": None,
                    })
                    continue

                scoring = compute_round_scoring(hole_scores, AUGUSTA_PARS)
                per_round_hole_pts[rd_idx] = scoring["holePoints"]
                per_round_streak[rd_idx] = scoring["streakBonus"]
                per_round_bf[rd_idx] = scoring["bogeyFreeBonus"]
                if scoring["roundScore"] is not None:
                    round_totals.append(scoring["roundScore"])

                round_data.append({
                    "id": golfer_id * 10 + rd_idx, "golferId": golfer_id,
                    "roundNumber": rd_num,
                    "birdies": scoring["birdies"], "eagles": scoring["eagles"],
                    "bogeys": scoring["bogeys"], "doubleBogeys": scoring["doubleBogeys"],
                    "doubleEagles": scoring["doubleEagles"], "holesInOne": scoring["holesInOne"],
                    "birdieStreakMax": scoring["birdieStreakMax"],
                    "bogeyFree": scoring["bogeyFree"],
                    "roundScore": scoring["roundScore"],
                })

            all_in_60s = len(round_totals) == 4 and all(s < 70 for s in round_totals)
            all_in_60s_bonus = ALL_ROUNDS_IN_60S_BONUS if all_in_60s else 0

            total_hp = sum(per_round_hole_pts)
            total_sb = sum(per_round_streak)
            total_bf = sum(per_round_bf)
            finish_pts = FINISH_POINTS.get(pos, 0) if is_complete else 0
            total_pts = total_hp + total_sb + total_bf + all_in_60s_bonus + finish_pts

            golfer_scorings.append({
                "golfer": {
                    "id": golfer_id, "name": golfer_name, "teamId": team_id,
                    "draftRound": draft_round, "missedCut": missed_cut,
                    "tournamentPosition": pos,
                },
                "rounds": round_data,
                "perRoundHolePoints": per_round_hole_pts,
                "perRoundStreakBonus": per_round_streak,
                "perRoundBogeyFreeBonus": per_round_bf,
                "totalHolePoints": total_hp,
                "totalStreakBonus": total_sb,
                "totalBogeyFreeBonus": total_bf,
                "allRoundsIn60s": all_in_60s,
                "allRoundsIn60sBonus": all_in_60s_bonus,
                "finishPoints": finish_pts,
                "totalGolferPoints": total_pts,
            })
            golfer_id += 1

        # ── Team totals ───────────────────────────────────────
        counting_points = 0
        for rd_idx in range(4):
            top_n = 5 if rd_idx < 2 else 3
            rnd_pts = sorted(
                [gs["perRoundHolePoints"][rd_idx] for gs in golfer_scorings],
                reverse=True
            )
            counting_points += sum(rnd_pts[:top_n])

        total_streak = sum(gs["totalStreakBonus"] for gs in golfer_scorings)
        total_bf = sum(gs["totalBogeyFreeBonus"] for gs in golfer_scorings)
        total_60s = sum(gs["allRoundsIn60sBonus"] for gs in golfer_scorings)
        bonus_points = total_streak + total_bf + total_60s

        finish_total = sum(gs["finishPoints"] for gs in golfer_scorings)

        made_cut_count = sum(1 for gs in golfer_scorings if not gs["golfer"]["missedCut"])
        any_cut = any(gs["golfer"]["missedCut"] for gs in golfer_scorings)
        cut_penalty = CUT_PENALTY if made_cut_count < 3 and any_cut else 0

        total_team = counting_points + bonus_points + finish_total + cut_penalty

        team_standings.append({
            "team": {"id": team_id, "name": owner_name, "ownerName": owner_name},
            "golfers": golfer_scorings,
            "countingPoints": counting_points,
            "bonusPoints": bonus_points,
            "finishPoints": finish_total,
            "cutPenalty": cut_penalty,
            "totalPoints": total_team,
        })
        team_id += 1

    team_standings.sort(key=lambda t: t["totalPoints"], reverse=True)
    for i, ts in enumerate(team_standings):
        ts["rank"] = i + 1

    return {
        "standings": team_standings,
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "statusDetail": status_detail,
    }


def _empty_golfer(gid, name, tid, dr):
    return {
        "golfer": {"id": gid, "name": name, "teamId": tid, "draftRound": dr,
                   "missedCut": False, "tournamentPosition": None},
        "rounds": [], "perRoundHolePoints": [0,0,0,0],
        "perRoundStreakBonus": [0,0,0,0], "perRoundBogeyFreeBonus": [0,0,0,0],
        "totalHolePoints": 0, "totalStreakBonus": 0, "totalBogeyFreeBonus": 0,
        "allRoundsIn60s": False, "allRoundsIn60sBonus": 0,
        "finishPoints": 0, "totalGolferPoints": 0,
    }


def main():
    print("Fetching live ESPN PGA Championship data...", file=sys.stderr)
    espn_data = fetch_espn_data()

    print("Computing pool standings...", file=sys.stderr)
    standings = compute_standings(espn_data)

    # Write to PGA tracker directory (where GitHub Pages serves from)
    out_path = "/home/user/workspace/pga-tracker/pool_scores.json"
    with open(out_path, "w") as f:
        json.dump(standings, f)
    print(f"Wrote {out_path}", file=sys.stderr)

    # Print summary
    print(f"\nStatus: {standings.get('statusDetail', '?')}", file=sys.stderr)
    print("=== POOL STANDINGS ===", file=sys.stderr)
    for ts in standings["standings"]:
        top_golfer = max(ts["golfers"], key=lambda g: g["totalGolferPoints"])
        print(f"  {ts['rank']:2d}. {ts['team']['ownerName']:15s}  {ts['totalPoints']:+4d} pts  "
              f"(best: {top_golfer['golfer']['name']} {top_golfer['totalGolferPoints']:+d})",
              file=sys.stderr)


if __name__ == "__main__":
    main()
