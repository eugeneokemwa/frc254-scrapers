#!/usr/bin/env python3
"""
FRC254: keep completed events' results up to date
==================================================
Runs every 6 hours (.github/workflows/sync-results.yml) and by hand.

Two jobs, both for events that have already finished:

1. RESULTS LINKS. A completed event in assets/data.js that has no results link
   (no chess-results tournament and no Lichess tournament) is looked up in
   chess-results' tournament database: Kenyan tournaments ending within two days
   either side of the event's dates, matched by name. A confident match is written
   to assets/results-links.js, which data.js merges into the event's links, so the
   events page shows a Results link. Nothing in data.js is edited, and a link
   written there by hand always wins: an event that gets its own link is dropped
   from results-links.js.

2. TOP THREE. Every completed event that has a results link but no entry in
   assets/podiums.js is passed to tools/fetch-podiums.py, which keeps a podium only
   from a page headed "Final Ranking" (or "Rank after Round N"), so an unfinished
   event never shows a table. Events whose results are not up yet are simply
   tried again at the next run.

Usage
  python3 tools/sync-event-results.py            # apply
  python3 tools/sync-event-results.py --dry-run  # print what it would do, write nothing

Needs: requests, beautifulsoup4, node.
"""
import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "results-links.js"
URL = "https://chess-results.com/TurnierSuche.aspx?lan=1"
UA = "FRC254-site-bot/1.0 (+https://github.com/eugeneokemwa/frc254-site; completed-event results links)"
RESULT_URL = "https://chess-results.com/tnr{t}.aspx?lan=1&art=1&turdet=YES"
WINDOW_DAYS = 2
MAX_LINKS = 8

DUMP = r"""
const d = await import(process.argv[1]);
const pod = (await import(process.argv[2])).PODIUMS;
const isRes = (u) => /chess-results\.com\/tnr\d+|lichess\.org\/(tournament|swiss)\/\w+/.test(u || "");
const past = d.prepare(d.EVENTS).filter((e) => e.status === "past");
console.log(JSON.stringify({
  noLink: past.filter((e) => !(e.links || []).some((l) => isRes(l.url))).map((e) => ({
    poster: e.poster, title: e.title, start: e.start, end: e.end,
    hand: (d.EVENTS.find((x) => x.poster === e.poster) || {}).links?.some((l) => isRes(l.url)) || false })),
  noPodium: past.filter((e) => (e.links || []).some((l) => isRes(l.url)) && !pod[e.poster]).map((e) => e.poster),
  usedTnr: [...new Set(d.EVENTS.flatMap((e) => (e.links || []).map((l) => (/tnr(\d+)/.exec(l.url || "") || [])[1]).filter(Boolean)))],
}));
"""

STOP = {"chess", "championship", "championships", "tournament", "tournaments", "edition", "annual",
        "the", "and", "for", "2026", "kenya", "open", "section", "results"}


def node_state():
    out = subprocess.run(["node", "--input-type=module", "-e", DUMP,
                          (ROOT / "assets" / "data.js").as_uri(), (ROOT / "assets" / "podiums.js").as_uri()],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower().replace("’", "").replace("'", "")) if len(w) >= 3 and w not in STOP}


def similar(a, b):
    """Share of the smaller name's words found in the other, and how many are shared."""
    shared = len(a & b)
    return (shared / min(len(a), len(b)) if a and b else 0), shared


def search(session, start, end):
    """Kenyan tournaments on chess-results ending between two dates: [(tnr, name)]."""
    r = session.get(URL, timeout=40)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    form = {i["name"]: i.get("value", "") for i in soup.select("input[type=hidden]") if i.get("name")}
    if "__VIEWSTATE" not in form:
        raise RuntimeError("empty form")
    rows_sel = soup.find("select", {"name": "ctl00$P1$combo_anzahl_zeilen"})
    land = soup.find("select", {"name": "ctl00$P1$combo_land"})
    ken = [o["value"] for o in land.find_all("option") if o["value"] == "KEN" or o.get_text(strip=True).startswith("KEN")]
    form.update({
        "ctl00$P1$cb_suchen": "Search",
        "ctl00$P1$txt_von_tag": start, "ctl00$P1$txt_bis_tag": end,
        "ctl00$P1$combo_land": ken[0] if ken else "KEN",
        "ctl00$P1$combo_anzahl_zeilen": max((o["value"] for o in rows_sel.find_all("option")),
                                            key=lambda v: int(v) if v.isdigit() else -1),
    })
    r = session.post(r.url, data=form, timeout=60)
    r.raise_for_status()
    found, seen = [], set()
    for a in BeautifulSoup(r.text, "html.parser").select("table.CRs1 a[href*=tnr], table.CRs2 a[href*=tnr]"):
        m = re.search(r"tnr(\d+)\.aspx", a["href"])
        if m and "art=" not in a["href"] and m.group(1) not in seen and a.get_text(strip=True):
            seen.add(m.group(1))
            found.append((m.group(1), a.get_text(strip=True)))
    return found


def read_existing():
    if not OUT.exists():
        return {}
    m = re.search(r"RESULTS_LINKS = (\{.*\});", OUT.read_text(), re.S)
    return json.loads(m.group(1)) if m else {}


def write(links):
    body = json.dumps({str(k): links[k] for k in sorted(links, key=int)}, ensure_ascii=False, indent=1)
    OUT.write_text(
        "/* GENERATED by tools/sync-event-results.py (every 6 hours): do not edit by hand.\n"
        "   Results links for completed events that have none in data.js, found on chess-results\n"
        "   by name and date. data.js merges these into the event's links. Keyed by poster number. */\n"
        f"export const RESULTS_LINKS = {body};\n")


def label_for(cand_name, event_tokens, taken):
    extra = [w for w in re.findall(r"[A-Za-z0-9&]+", cand_name) if w.lower() not in event_tokens and w.lower() not in STOP]
    label = f"Results ({' '.join(extra[:3])})" if extra else "Results"
    if label in taken:
        label += f" {len(taken) + 1}"
    return label


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    state = node_state()
    existing = read_existing()
    used = set(state["usedTnr"])
    for links in existing.values():
        used.update(re.search(r"tnr(\d+)", l["url"]).group(1) for l in links)
    session = requests.Session()
    session.headers["User-Agent"] = UA

    links, notes = dict(existing), []
    for ev in state["noLink"]:
        key = str(ev["poster"])
        if ev["hand"]:
            continue
        if key in existing:       # already found on an earlier run
            continue
        d0 = dt.date.fromisoformat(ev["start"]) - dt.timedelta(days=WINDOW_DAYS)
        d1 = dt.date.fromisoformat(ev["end"]) + dt.timedelta(days=WINDOW_DAYS)
        try:
            cands = search(session, d0.isoformat(), d1.isoformat())
        except Exception as exc:
            print(f"  {key}: search failed ({exc})", file=sys.stderr)
            time.sleep(5)
            continue
        et = tokens(ev["title"])
        hits = []
        for tnr, name in cands:
            if tnr in used:
                continue
            ratio, shared = similar(et, tokens(name))
            if ratio >= 0.8 and shared >= 2:
                hits.append((tnr, name))
        if hits:
            taken = []
            out = []
            for tnr, name in hits[:MAX_LINKS]:
                lab = label_for(name, et, taken)
                taken.append(lab)
                out.append({"label": lab, "url": RESULT_URL.format(t=tnr)})
                used.add(tnr)
            links[key] = out
            notes.append(f"  + {key} {ev['title'][:48]}: " + ", ".join(f"{h[0]} {h[1][:30]}" for h in hits[:MAX_LINKS]))
        else:
            notes.append(f"  - {key} {ev['title'][:48]}: no confident match ({len(cands)} candidates)")
        time.sleep(1.5)

    # An event that now has its own link in data.js no longer needs an automatic one.
    handled = {str(e["poster"]) for e in state["noLink"]}
    links = {k: v for k, v in links.items() if k in handled}

    print("\n".join(notes) or "no completed events without a results link")
    if args.dry_run:
        print("dry run: nothing written")
        return
    if links != existing:
        write(links)
        print(f"results-links.js: {len(links)} events")

    # Top three: ask for it again for every completed event with a link and no podium.
    todo = node_state()["noPodium"]
    if todo:
        print(f"podiums to try: {todo}")
        cmd = [sys.executable, str(ROOT / "tools" / "fetch-podiums.py")]
        for p in todo:
            cmd += ["--poster", str(p)]
        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
