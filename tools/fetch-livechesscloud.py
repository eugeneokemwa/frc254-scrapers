#!/usr/bin/env python3
"""Games from LiveChessCloud (the DGT live boards behind view.livechesscloud.com/#<id> links in the owner's WhatsApp chats) into
tools/cache/lcc/<id>.pgn, which build-gp-games.py merges like a broadcast. Public JSON on 1.pool.livechesscloud.com:
  get/<id>/tournament.json            name, location, rounds [{count}]
  get/<id>/round-<r>/index.json       pairings: white/black {fname, lname, title, federation, fideid}, result
  get/<id>/round-<r>/game-<g>.json    moves ["e4", "c5 5400", …: SAN, then the clock], result WHITEWIN/BLACKWIN/DRAW, firstMove (ms)
One request per game, so this runs on GitHub Actions (tools/runner/), not on the phone. A tournament already in the cache is skipped.
Usage: fetch-livechesscloud.py IDS_FILE    (one "<id> <yyyy-mm-dd day the link was posted>" per line)"""
import datetime, json, os, sys, time
import requests

HERE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(HERE, "cache", "lcc")
BASE = "https://1.pool.livechesscloud.com/get"
S = requests.Session(); S.headers["User-Agent"] = "frc254-games-fetch/1.0"
RES = {"WHITEWIN": "1-0", "BLACKWIN": "0-1", "DRAW": "1/2-1/2"}
os.makedirs(OUT, exist_ok=True)

def get(path):
    for k in range(4):
        try:
            r = S.get(f"{BASE}/{path}", timeout=40)
            if r.status_code == 404: return None
            if r.status_code == 429: time.sleep(60); continue
            r.raise_for_status(); time.sleep(0.4)
            return r.json()
        except (requests.RequestException, ValueError):
            time.sleep(3 * (k + 1))
    return None

def name(p):
    return ", ".join(x for x in (p.get("lname"), " ".join(y for y in (p.get("fname"), p.get("mname")) if y)) if x) or "?"

def tag(k, v): return f'[{k} "{str(v).replace(chr(34), chr(39))}"]\n'

for line in open(sys.argv[1], encoding="utf-8"):
    if not line.strip(): continue
    tid, posted = (line.split() + [""])[:2]
    path = os.path.join(OUT, f"{tid}.pgn")
    if os.path.exists(path): continue
    t = get(f"{tid}/tournament.json")
    if not t: print("not found:", tid, flush=True); continue
    out, n = [], 0
    for r, rd in enumerate(t.get("rounds") or [], 1):
        idx = get(f"{tid}/round-{r}/index.json") or {}
        for g, pr in enumerate(idx.get("pairings") or [], 1):
            w, b = pr.get("white") or {}, pr.get("black") or {}
            game = get(f"{tid}/round-{r}/game-{g}.json?poll") or {}
            moves = [m.split()[0] for m in game.get("moves") or [] if m]
            res = RES.get(game.get("result")) or (pr.get("result") if pr.get("result") in ("1-0", "0-1", "1/2-1/2") else None)
            if len(moves) < 2 or not res: continue
            # The day the game started; a link posted in the chat dates it when the board sent no time.
            day = datetime.datetime.fromtimestamp(game["firstMove"] / 1000, datetime.timezone(datetime.timedelta(hours=3))).strftime("%Y.%m.%d") if game.get("firstMove") else posted.replace("-", ".")
            text = " ".join((f"{k // 2 + 1}. " if k % 2 == 0 else "") + m for k, m in enumerate(moves))
            out.append(tag("Event", t.get("name") or tid) + tag("Site", t.get("location") or "") + tag("Date", day) + tag("Round", f"{r}.{g}")
                       + tag("White", name(w)) + tag("Black", name(b)) + tag("Result", res)
                       + (tag("WhiteFideId", w["fideid"]) if w.get("fideid") else "") + (tag("BlackFideId", b["fideid"]) if b.get("fideid") else "")
                       + tag("WhiteTitle", w.get("title") or "") + tag("BlackTitle", b.get("title") or "") + f"\n{text} {res}\n")
            n += 1
    if out: open(path, "w", encoding="utf-8").write("\n".join(out))
    print(f"{t.get('name')}: {n} games", flush=True)
