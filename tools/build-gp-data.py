#!/usr/bin/env python3
"""
FRC254 — Grand Prix data snapshot builder
==========================================================================
Writes assets/gp-data.js, the static baseline for grandprix.html.

The Grand Prix page is HYBRID: at load it tries the live API first and
falls back to this snapshot if the API is slow, down, or CORS-blocked.
So this file is never the only source of truth, but it must always be
present and valid — the page must render fully with the network off.

Source
  Chess Kenya Grand Prix results, as published by the GP Tracker API
  (gp-tracker-hidden-rain-8594.fly.dev), which scrapes Chess-Results.com.
  The underlying tournament results are factual records. The qualification
  forecast is read from the tracker's published JSON on GitHub.

Why tournament-first, not player-first
  There are ~810 ranked players but only ~27 tournaments. Fetching every
  player endpoint would be 810 requests; fetching each tournament's result
  table is ~54 and carries the same information. Player histories are then
  built by INVERTING the tournament tables locally. Do not "simplify" this
  into a per-player loop — it is 15x the requests for identical output.

Usage
  python3 tools/build-gp-data.py                 # current season
  python3 tools/build-gp-data.py --season 2025
  python3 tools/build-gp-data.py --check         # verify freshness, write nothing

Run this after each Grand Prix event is scraped into the tracker.
"""

import argparse
import datetime as dt
import json
import html
import os
import re
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("GP_API", "https://gp-tracker-hidden-rain-8594.fly.dev/api")
FORECAST_URL = (
    "https://raw.githubusercontent.com/walela/gp/main/"
    "client/lib/qualification-forecast.json"
)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "gp-data.js")

TIMEOUT = 30
RETRIES = 3


def get(url, retries=RETRIES):
    """GET JSON with linear backoff. Fly machines cold-start, so the first
    request after an idle period can take several seconds or time out once."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "frc254-gp-snapshot"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            if attempt < retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last}")


def fetch_rankings(season, gender=None):
    """All ranking pages for one category, flattened."""
    out, page, pages = [], 1, 1
    while page <= pages:
        url = f"{API}/rankings?season={season}&page={page}"
        if gender:
            url += f"&gender={gender}"
        d = get(url)
        pages = d.get("total_pages", 1)
        out.extend(d.get("rankings", []))
        page += 1
    return out


def fetch_tournament_results(tid):
    """Every result row for a tournament, across its pages."""
    out, page, pages = [], 1, 1
    while page <= pages:
        d = get(f"{API}/tournament/{tid}?page={page}")
        pages = d.get("total_pages", 1)
        out.extend(d.get("results", []))
        page += 1
    return out, d


def cr_final(source_id):
    """({start no: final rank}, field size, {start no: club}) from the chess-results Final
    Ranking (tiebreaks applied; the API's row order is not reliable). None on failure."""
    try:
        req = urllib.request.Request(
            f"https://chess-results.com/tnr{source_id}.aspx?lan=1&art=1&turdet=YES&zeilen=0",
            headers={"User-Agent": "Mozilla/5.0 (FRC254 podium builder)"})
        page = urllib.request.urlopen(req, timeout=TIMEOUT).read().decode("utf-8", "replace")
        if "Final Ranking" not in page:
            return None
        table = re.search(r'<table class="CRs1"[^>]*>(.*?)</table>', page, re.S).group(1)
        txt = lambda c: html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
        rows = [[txt(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r, re.S)]
                for r in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)]
        i_rk, i_sno = rows[0].index("Rk."), rows[0].index("SNo")
        m = {int(r[i_sno]): int(r[i_rk]) for r in rows[1:] if r[i_sno].isdigit() and r[i_rk].isdigit()}
        # Club/City, by start number (blank cells stay out; columns shift between events).
        i_club = rows[0].index("Club/City") if "Club/City" in rows[0] else None
        clubs = {int(r[i_sno]): r[i_club] for r in rows[1:]
                 if i_club is not None and len(r) > i_club and r[i_sno].isdigit() and r[i_club]}
        return (m, len(m), clubs) if m else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--check", action="store_true",
                    help="report staleness vs the live API, write nothing")
    args = ap.parse_args()

    seasons = get(f"{API}/seasons")
    if isinstance(seasons, dict):
        seasons = seasons.get("seasons", [])
    season = args.season or max(seasons)
    print(f"seasons available: {seasons} -> building {season}", file=sys.stderr)

    tournaments = get(f"{API}/tournaments?season={season}")
    if isinstance(tournaments, dict):
        tournaments = tournaments.get("tournaments", [])
    print(f"tournaments: {len(tournaments)}", file=sys.stderr)

    open_r = fetch_rankings(season)
    ladies_r = fetch_rankings(season, gender="F")
    print(f"rankings: open={len(open_r)} ladies={len(ladies_r)}", file=sys.stderr)

    if args.check:
        if not os.path.exists(OUT):
            print("STALE: assets/gp-data.js does not exist")
            return 1
        src = open(OUT, encoding="utf-8").read()
        fresh = all(str(t["id"]) in src for t in tournaments)
        print("FRESH" if fresh else "STALE: snapshot is missing tournaments the API has")
        return 0 if fresh else 1

    # --- per-tournament result tables, inverted into player histories -----
    histories = {}
    tclubs = {}   # tournament id -> {start no: club}
    tmeta = {}
    for i, t in enumerate(tournaments, 1):
        tid = str(t["id"])
        try:
            rows, detail = fetch_tournament_results(tid)
        except RuntimeError as e:
            print(f"  !! {tid} {t.get('short_name')}: {e}", file=sys.stderr)
            continue
        final = cr_final(detail.get("source_id"))
        if final:
            tclubs[tid] = final[2]
        if not final:
            print(f"  !! {tid}: no chess-results final ranking, using API order", file=sys.stderr)
        tmeta[tid] = {
            "id": tid,
            "name": t.get("name"),
            "short": t.get("short_name"),
            "start": t.get("start_date"),
            "end": t.get("end_date"),
            "location": t.get("location"),
            "rounds": t.get("rounds"),
            "section": t.get("section"),
            "status": t.get("status"),
            "players": final[1] if final else len(rows),
            "avgTop10TPR": t.get("avgTop10TPR"),
            "sourceId": detail.get("source_id"),
        }
        for pos, r in enumerate(rows, 1):  # API lists rows in final standings order
            p = r.get("player") or {}
            fid = p.get("fide_id")
            if not fid:
                continue
            histories.setdefault(fid, []).append({
                "t": tid,
                "tpr": r.get("tpr"),
                "pts": r.get("points"),
                "rating": r.get("rating"),
                "rank": r.get("start_rank"),
                "fin": final[0].get(r.get("start_rank"), pos) if final else pos,
                "status": r.get("result_status"),
                "wo": bool(r.get("has_walkover")),
            })
        print(f"  [{i}/{len(tournaments)}] {t.get('short_name')} "
              f"({t.get('section')}) {len(rows)} rows", file=sys.stderr)

    # --- qualification forecast ------------------------------------------
    forecast = None
    try:
        forecast = get(FORECAST_URL, retries=2)
        print(f"forecast: as_of {forecast.get('as_of')} "
              f"after {(forecast.get('after') or {}).get('name')}", file=sys.stderr)
    except RuntimeError as e:
        print(f"forecast unavailable, omitting: {e}", file=sys.stderr)

    def slim(rows):
        """Only the fields grandprix.html actually reads. The raw payload
        carries duplicate name/season/gender on every row; at 810 rows that
        is most of the file size."""
        return [{
            "id": r.get("fide_id"),
            "name": r.get("name"),
            "rank": r.get("rank"),
            "prev": r.get("previous_rank"),
            "chg": r.get("rank_change"),
            "rating": r.get("rating"),
            "played": r.get("tournaments_played"),
            "b1": r.get("best_1"),
            "b2": r.get("best_2"),
            "b3": r.get("best_3"),
            "b4": r.get("best_4"),
            "t1": r.get("tournament_1"),
            "new": bool(r.get("is_new")),
        } for r in rows]

    # Each player's most recent club: the newest tournament (by start date)
    # whose chess-results table gives them one. History rank = start number.
    clubs = {}
    for fid, hist in histories.items():
        best = None
        for h in hist:
            club = tclubs.get(h["t"], {}).get(h.get("rank"))
            start = (tmeta.get(h["t"]) or {}).get("start") or ""
            if club and (best is None or start > best[0]):
                best = (start, club)
        if best:
            clubs[fid] = best[1]
    print(f"clubs: {len(clubs)} of {len(histories)} players", file=sys.stderr)

    payload = {
        "season": season,
        "seasons": sorted(seasons, reverse=True),
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": API,
        "tournaments": list(tmeta.values()),
        "rankings": {"open": slim(open_r), "ladies": slim(ladies_r)},
        "histories": histories,
        "clubs": clubs,
        "forecast": forecast,
    }

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(
            "/* ========================================================================\n"
            "   FRC254 — Grand Prix snapshot. GENERATED FILE, DO NOT EDIT BY HAND.\n"
            "   Rebuild with: python3 tools/build-gp-data.py\n"
            f"   Season {season} · generated {payload['generated']}\n"
            f"   {len(open_r)} open / {len(ladies_r)} ladies ranked · "
            f"{len(tmeta)} tournaments · {len(histories)} player histories\n"
            "   This is the OFFLINE FALLBACK. grandprix.html prefers the live API\n"
            "   and only renders from here when that fetch fails.\n"
            "   ===================================================================== */\n"
            f"export const GP_SNAPSHOT = {body};\n"
        )

    kb = os.path.getsize(OUT) / 1024
    print(f"\nwrote {OUT} ({kb:.0f} KB)", file=sys.stderr)
    print(f"  {len(open_r)} open, {len(ladies_r)} ladies, {len(tmeta)} tournaments, "
          f"{len(histories)} histories, forecast={'yes' if forecast else 'no'}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
