#!/usr/bin/env python3
"""Build assets/podiums.js: the top three of every completed event.

Over-the-board events come from chess-results; online events from Lichess
(arena or Swiss, through its public API). Every finisher also gets a
performance rating where the source has one: chess-results' "Performance
rating" from the player card, Lichess's own "performance" figure.

For each completed event in assets/data.js that links chess-results, pick the
event's highest section (open/elite/masters before rating-capped sections,
older age groups before younger, open/boys before girls/ladies, side blitz
events last), fetch that section's final ranking and keep places 1-3. Only a
page headed "Final Ranking" is used, so an unfinished or empty table never
produces a podium. Same generate-then-commit pattern as
extract-poster-colors.mjs: run it from the repo root after results are posted.

    python3 tools/fetch-podiums.py            # all completed events
    python3 tools/fetch-podiums.py --poster 7 # one event
"""
import argparse
import concurrent.futures as cf
import html
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "podiums.js"

DUMP = r"""
const d = await import(process.argv[1]);
const ev = d.prepare(d.EVENTS);
console.log(JSON.stringify(ev.filter((e) => e.status === "past").map((e) => ({
  poster: e.poster, title: e.title, links: (e.links || []).filter((l) => /chess-results\.com\/tnr\d+/.test(l.url || ""))
  , lichess: (e.links || []).map((l) => l.url || "").filter((u) => /lichess\.org\/(tournament|swiss)\/\w+/.test(u))
})).filter((e) => e.links.length || e.lichess.length)));
"""


def events():
    url = (ROOT / "assets" / "data.js").as_uri()
    out = subprocess.run(["node", "--input-type=module", "-e", DUMP, url],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def section_score(label):
    l = (label or "").lower()
    s = 0
    ages = [int(a) for a in re.findall(r"\bu-?(\d{1,2})\b", l)]
    caps = [int(r) for r in re.findall(r"\bu-?(\d{4})\b", l)]
    if caps:
        s = 600 + max(caps) // 10
    elif ages:
        s = max(ages) * 10
    elif re.search(r"master", l):
        s = 1030
    elif re.search(r"elite", l):
        s = 1020
    elif re.search(r"\b(open|main|prestige|championship)\b", l):
        s = 1000
    elif re.search(r"junior|youth", l):
        s = 300
    else:
        s = 900  # a plain "Results" / "Standings"
    if re.search(r"girl|ladies|lady|women", l):
        s -= 250 if not ages else 3
    if re.search(r"blitz|bullet", l):
        s -= 400
    return s


TITLES = re.compile(r"(?:W|A)?(?:GM|IM|FM|CM)|NM")


def fetch(url, accept=None):
    headers = {"User-Agent": "Mozilla/5.0 (FRC254 podium builder)"}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(4):  # chess-results throttles bursts: back off and retry
        try:
            return urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404 or attempt == 3:
                raise
        except Exception:
            if attempt == 3:
                raise
        time.sleep(5 * (attempt + 1))


def performance(tnr, sno):
    """The "Performance rating" row of a chess-results player card, or None."""
    try:
        page = fetch(f"https://chess-results.com/tnr{tnr}.aspx?lan=1&art=9&turdet=YES&snr={sno}")
    except Exception:
        return None
    m = re.search(r"Performance rating\s*</td>\s*<td[^>]*>\s*(\d{3,4})\s*<", page)
    return m.group(1) if m and int(m.group(1)) > 0 else None


def lichess_podium(url):
    """Top three of a finished Lichess arena or Swiss, with performance."""
    kind, tid = re.search(r"lichess\.org/(tournament|swiss)/(\w+)", url).groups()
    if kind == "tournament":
        info = json.loads(fetch(f"https://lichess.org/api/tournament/{tid}"))
        if not info.get("isFinished"):
            return None
    rows = [json.loads(l) for l in fetch(f"https://lichess.org/api/{kind}/{tid}/results?nb=3",
                                         "application/x-ndjson").splitlines() if l.strip()]
    places = []
    for r in rows[:3]:
        p = {"rank": r["rank"], "name": r["username"]}
        if r.get("title"):
            p["title"] = r["title"]
        score = r.get("score", r.get("points"))
        if score is not None:
            p["pts"] = str(score)
        if r.get("rating"):
            p["rtg"] = str(r["rating"])
        if r.get("performance"):
            p["perf"] = str(r["performance"])
        places.append(p)
    return places if len(places) == 3 else None


def text(cell):
    return html.unescape(re.sub(r"<[^>]+>", "", cell)).replace("\xa0", " ").strip()


def podium(tnr, team):
    art = 46 if team else 1
    page = fetch(f"https://chess-results.com/tnr{tnr}.aspx?lan=1&art={art}&turdet=YES")
    heads = [text(h) for h in re.findall(r"<h2[^>]*>(.*?)</h2>", page, re.S)]
    # Organisers often leave a finished event at "Rank after Round N" and never
    # re-publish it as final; for a completed event that table is the result.
    # "Round 0" is only the pairing list.
    if not any(h.startswith("Final Ranking") or re.match(r"Rank after Round [1-9]", h) for h in heads):
        return None
    table = re.search(r'<table class="CRs1"[^>]*>(.*?)</table>', page, re.S)
    if not table:
        return None
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(1), re.S)
    head = [text(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", rows[0], re.S)]

    def col(*names):
        for n in names:
            if n in head:
                return head.index(n)
        return None

    i_name, i_pts = col("Name", "Team"), col("Pts.", "Pts", "MP")  # no guessing from tiebreak columns
    i_fed, i_rtg, i_sno = col("FED"), col("Rtg"), col("SNo")
    places = []
    for r in rows[1:4]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        if i_name is None or len(cells) <= i_name:
            continue
        title = text(cells[i_name - 1]) if i_name and not team else ""
        p = {"rank": int(text(cells[0]) or len(places) + 1), "name": text(cells[i_name]).strip(" ,")}
        if TITLES.fullmatch(title):
            p["title"] = title
        if i_pts is not None and i_pts < len(cells):
            p["pts"] = text(cells[i_pts]).replace(",", ".")
        if i_fed is not None and text(cells[i_fed]) not in ("", "KEN"):
            p["fed"] = text(cells[i_fed])
        if i_rtg is not None and text(cells[i_rtg]) not in ("", "0"):
            p["rtg"] = text(cells[i_rtg])
        if i_sno is not None and not team and text(cells[i_sno]).isdigit():
            p["sno"] = text(cells[i_sno])
        places.append(p)
    if len(places) != 3 or not all(p["name"] for p in places):
        return None
    for p in places:  # one extra page per finisher, for the performance rating
        sno = p.pop("sno", None)
        perf = sno and performance(tnr, sno)
        if perf:
            p["perf"] = perf
    return places


def build(ev):
    if not ev["links"] and LOCAL_ONLY:
        return ev["poster"], "error"   # online events need Lichess: not in --local-only
    if not ev["links"]:  # online: Lichess
        url = ev["lichess"][0]
        try:
            places = lichess_podium(url)
        except urllib.error.HTTPError as exc:  # deleted tournament: no podium
            print(f"  {ev['poster']}: Lichess {exc.code}", file=sys.stderr)
            return ev["poster"], None
        except Exception as exc:
            print(f"  {ev['poster']}: fetch failed ({exc})", file=sys.stderr)
            return ev["poster"], "error"
        if not places:
            return ev["poster"], None
        return ev["poster"], {"section": "", "team": False, "source": "lichess", "places": places,
                              "url": url.split("?")[0]}
    # A league weekend (a team-standings link to a section the League tab holds): the top three teams from the site's own
    # league data (tools/build-gp-league.py), no request at all. The standings are the latest the site has: the final ones once
    # the season is over, and the label says which.
    for l in ev["links"]:
        lg = league_podium(re.search(r"tnr(\d+)", l["url"]).group(1)) if "art=46" in l["url"] else None
        if lg:
            return ev["poster"], lg
    if LOCAL_ONLY:
        return ev["poster"], "error"   # keep whatever the event has: nothing is fetched in this mode
    # Highest section first; if it has no published ranking, fall back to
    # the next one rather than leaving the whole event without a podium.
    for best in sorted(ev["links"], key=lambda l: -section_score(l["label"])):
        tnr = re.search(r"tnr(\d+)", best["url"]).group(1)
        team = "art=46" in best["url"]
        try:
            places = podium(tnr, team)
        except Exception as exc:  # network hiccup: keep the previous entry
            print(f"  {ev['poster']}: fetch failed ({exc})", file=sys.stderr)
            return ev["poster"], "error"
        if places:
            break
    else:
        return ev["poster"], None
    section = re.sub(r"\b(results|standings)\b", "", best["label"], flags=re.I).strip(" ()") or ""
    return ev["poster"], {"section": section, "team": team, "places": places,
                          "url": f"https://chess-results.com/tnr{tnr}.aspx?lan=1&art={46 if team else 1}&turdet=YES"}


LEAGUE = {}   # tournament id -> (section, the season's data), from assets/gp-league*.js
LOCAL_ONLY = False

def load_league():
    """Every league section the site holds, loaded once before the threads start (they used to race to fill it)."""
    if not LEAGUE:
        for f in sorted((ROOT / "assets").glob("gp-league*.js")):
            if "pairings" in f.name or "view" in f.name:
                continue
            m = re.search(r"=\s*(\{.*\})\s*;?\s*$", f.read_text(encoding="utf-8"), re.S)
            for k, sec in (json.loads(m.group(1)).get("sections") or {}).items() if m else []:
                LEAGUE[k] = sec


def league_podium(tnr):
    sec = LEAGUE.get(tnr)
    if not sec or not sec.get("teams"):
        return None
    teams = sorted(sec["teams"], key=lambda t: t.get("rk") or 99)[:3]
    if len(teams) < 3 or not all(t.get("g") for t in teams):
        return None
    played = round(sum(t.get("g", 0) for t in sec["teams"]) / 2)
    total = len(sec["teams"]) * (len(sec["teams"]) - 1) // 2
    return {"section": f"{sec.get('name', '')} · {'final standings' if played >= total else 'standings so far'}".strip(" ·"), "team": True,
            "places": [{"rank": t.get("rk") or i + 1, "name": t["n"], "pts": (lambda v: str(int(v)) if isinstance(v, float) and v.is_integer() else str(v))(t.get("mp", "")), **({"rtg": str(t["avg"])} if t.get("avg") else {})}
                       for i, t in enumerate(teams)],
            "url": f"https://chess-results.com/tnr{tnr}.aspx?lan=1&art=46&turdet=YES"}


def main():
    global LOCAL_ONLY
    ap = argparse.ArgumentParser()
    ap.add_argument("--poster", type=int, action="append")
    ap.add_argument("--local-only", action="store_true", help="only what needs no request (league weekends from the site's league data)")
    args = ap.parse_args()
    LOCAL_ONLY = args.local_only
    load_league()
    current = {}
    if OUT.exists():
        m = re.search(r"PODIUMS = (\{.*\});", OUT.read_text(), re.S)
        if m:
            current = {int(k): v for k, v in json.loads(m.group(1)).items()}
    todo = [e for e in events() if not args.poster or e["poster"] in args.poster]
    with cf.ThreadPoolExecutor(3) as ex:
        for poster, result in ex.map(build, todo):
            if result == "error":
                continue
            if result:
                current[poster] = result
            else:
                current.pop(poster, None)
    body = json.dumps({str(k): current[k] for k in sorted(current)}, ensure_ascii=False, indent=1)
    OUT.write_text(
        "/* GENERATED by tools/fetch-podiums.py — do not edit by hand.\n"
        "   Top three of each completed event's highest section, from the\n"
        "   chess-results final ranking (or Lichess for online events), with\n"
        "   performance ratings. Keyed by poster number. */\n"
        f"export const PODIUMS = {body};\n")
    print(f"podiums.js: {len(current)} events with a podium ({len(todo)} checked)")


if __name__ == "__main__":
    main()
