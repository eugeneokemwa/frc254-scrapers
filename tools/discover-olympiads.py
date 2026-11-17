#!/usr/bin/env python3
"""Every Olympiad on chess-results.com in a date window: searches tournaments by name "Olympiad" (TurnierSuche.aspx), unions the hits with
the tournament ids already known (the "Olympiad" entries of the player-tournament files, 22 today) and --extra ids; writes one id a line.
Usage: discover-olympiads.py --from 2020-01-01 --to 2026-12-31 [--known FILE...] [--extra id,id] [--out olympiads.txt] [--page FILE]
  --known: files with {"tournaments": {id: {"n": name}}} (default assets/gp-player-tournaments*.js) or {id: name} (gp-tournament-names.js);
           only ids whose name has "Olympiad" or "Olimpiad" count. --page FILE: read the result page from FILE instead of the site (tests).
Exit 3 when chess-results answers with its "daily limit exceeded" page (nothing is written). Search field names are unverified: check on Actions."""
import argparse, json, os, re, sys
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, "..")
URL = "https://chess-results.com/TurnierSuche.aspx?lan=1"
ap = argparse.ArgumentParser()
ap.add_argument("--from", dest="frm", default="2020-01-01"); ap.add_argument("--to", default="2026-12-31")
ap.add_argument("--known", nargs="*", default=[os.path.join(ROOT, "assets", n) for n in ("gp-player-tournaments.js", "gp-player-tournaments-history.js")])
ap.add_argument("--extra", default=""); ap.add_argument("--out", default=""); ap.add_argument("--page", default="")
a = ap.parse_args()

def limited(text):
    if "exceeded! Your daily limit" in text[:2000]:
        print("chess-results has rate-limited this IP (2,000 page requests a day); stopping, nothing written", file=sys.stderr)
        sys.exit(3)
    return text

def search():
    if a.page: return limited(open(a.page, encoding="utf-8").read())
    import requests
    S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
    r = S.get(URL, timeout=60); limited(r.text)
    soup = BeautifulSoup(r.text, "html.parser")
    f = {i["name"]: i.get("value", "") for i in soup.select("input[type=hidden]") if i.get("name")}
    sel = soup.find("select", {"name": "ctl00$P1$combo_anzahl_zeilen"})
    f.update({"ctl00$P1$txt_name": "Olympiad", "ctl00$P1$txt_von_tag": a.frm, "ctl00$P1$txt_bis_tag": a.to, "ctl00$P1$cb_suchen": "Search"})
    if sel: f["ctl00$P1$combo_anzahl_zeilen"] = max((o["value"] for o in sel.find_all("option")), key=lambda v: int(v) if v.isdigit() else -1)
    r = S.post(r.url, data=f, timeout=60); r.raise_for_status()
    return limited(r.text)

def found(html):   # same table the player search reads: table.CRs2, rows CRg1/CRg2, the tournament link tnr<id>
    ids = set()
    for tr in BeautifulSoup(html, "html.parser").select("table.CRs2 tr.CRg1, table.CRs2 tr.CRg2"):
        for l in tr.find_all("a", href=re.compile(r"tnr\d+")):
            if "art=9" not in l["href"]: ids.add(re.search(r"tnr(\d+)", l["href"]).group(1))
    return ids

def known(path):
    if not os.path.exists(path): return set()
    x = json.loads(re.search(r"=\s*(\{.*\})\s*;?\s*$", open(path, encoding="utf-8").read(), re.S).group(1))
    t = x.get("tournaments", x)
    return {i for i, v in t.items() if re.search(r"olym?piad", v["n"] if isinstance(v, dict) else v, re.I)}

ids = found(search())
print(len(ids), "from the search", file=sys.stderr)
for p in a.known: ids |= known(p)
ids |= {i.strip() for i in a.extra.split(",") if i.strip()}
out = "\n".join(sorted(ids, key=int)) + "\n"
if a.out: open(a.out, "w").write(out)
else: sys.stdout.write(out)
print(len(ids), "ids in all", file=sys.stderr)
