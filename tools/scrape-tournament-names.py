#!/usr/bin/env python3
"""Full tournament names from chess-results.com. The player database search (tools/scrape-player-tournaments.py) cuts a name at 30
characters, so the sections of one event read alike ("Kisii Open Chess Championship" three times). A tournament's own page heads with
the whole name: one request a tournament, kept in tools/cache/tnr-names.jsonl ({"t": id, "n": name}; done ids are skipped on a rerun).
Runs on GitHub Actions (tools/runner), never on the phone: chess-results allows 2,000 requests a day per IP.
Usage: scrape-tournament-names.py [--list FILE...] [--shard K/N] [--only id,id]
       scrape-tournament-names.py --write FILE...   merge jsonl files into assets/gp-tournament-names.js (names that differ from the cut one)"""
import argparse, html, json, os, re, sys, time
import requests

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, "..")
OUT = os.path.join(HERE, "cache", "tnr-names.jsonl")
ap = argparse.ArgumentParser()
ap.add_argument("--list", nargs="+", default=[os.path.join(ROOT, "assets", n) for n in ("gp-player-tournaments.js", "gp-player-tournaments-history.js")])
ap.add_argument("--shard", default=""); ap.add_argument("--only", default=""); ap.add_argument("--write", nargs="+")
a = ap.parse_args()

def load(path):
    return json.loads(re.search(r"=\s*(\{.*\})\s*;?\s*$", open(path, encoding="utf-8").read(), re.S).group(1))
T = {}
for p in a.list:
    if os.path.exists(p): T.update(load(p)["tournaments"])

def read(path):
    return {o["t"]: o["n"] for o in (json.loads(l) for l in open(path, encoding="utf-8") if l.strip())} if os.path.exists(path) else {}

if a.write:
    names = {}
    for f in a.write: names.update(read(f))
    clean = lambda s: re.sub(r"\s+", " ", s).strip()
    full = {t: clean(n) for t, n in sorted(names.items()) if n and t in T and clean(n).lower() != clean(T[t]["n"]).lower()}
    with open(os.path.join(ROOT, "assets", "gp-tournament-names.js"), "w", encoding="utf-8") as f:
        f.write("/* FRC254 full tournament names. GENERATED, DO NOT EDIT. Rebuild with tools/scrape-tournament-names.py (GitHub Actions). "
                "Tournament id -> the whole name from its chess-results page, where the player database cut it at 30 characters. */\n"
                f"export const GP_TOURNAMENT_NAMES = {json.dumps(full, ensure_ascii=False, separators=(',', ':'))};\n")
    print(f"{len(names)} names read, {len(full)} differ from the cut name")
    sys.exit()

done = read(OUT)
todo = sorted((t for t in (a.only.split(",") if a.only else T) if t and t not in done), key=int)
if a.shard:
    k, n = map(int, a.shard.split("/")); todo = todo[k - 1::n]
print(len(todo), "tournament pages to read", flush=True)
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
with open(OUT, "a", encoding="utf-8") as out:
    for i, t in enumerate(todo):
        try: r = S.get(f"https://chess-results.com/tnr{t}.aspx?lan=1&turdet=NO", timeout=60)
        except requests.RequestException: time.sleep(5); continue
        if "exceeded! Your daily limit" in r.text[:2000]: sys.exit("chess-results has rate-limited this IP; stopping (rerun later: done ids are kept)")
        m = re.search(r"<h2[^>]*>(.*?)</h2>", r.text, re.S)
        name = html.unescape(re.sub(r"<[^>]*>", "", m.group(1))).strip() if m else ""
        if name: out.write(json.dumps({"t": t, "n": name}, ensure_ascii=False) + "\n"); out.flush()
        if i % 50 == 0: print(i, t, name[:70], flush=True)
        time.sleep(0.8)
print("done")
