#!/usr/bin/env python3
"""Whole-tournament PGNs from chess-results.com for Kenyan events (the tournaments on the player cards, assets/gp-player-tournaments.js,
that have at least 3 of our players and at least a quarter of the field). A tournament page offers "There are N games available
for download" when the organiser uploaded moves; that page posts back a PGN of every game, one request per tournament.
Tournaments with only a PGN link per game (no download-all link) are read round by round: the pairings pages give White, Black,
result and the game id of every board, and each game page gives the moves. Writes tools/cache/cr-tnr/<tournament id>.pgn, and lists tournaments with no games in tools/cache/cr-tnr/none.txt (skipped next time).
--abroad: the other tournaments our players played (abroad, or a big open where they were few): only their own games are kept, and a
tournament with per-game links only fetches the boards of our players. Writes tools/cache/cr-tnr/abroad/<id>.pgn (and its none.txt).
Usage: scrape-cr-tournaments.py [--abroad] [--threads 4] [--min-players 3] [--share 0.25] [--all] [--only id,id] [--list FILE...] [--extra id,id] [--shard K/N] [--max-requests N]
  (--all drops the Kenyan filter; --list: player-tournament files, default assets/gp-player-tournaments.js; --extra: tournaments to check
   besides, such as links in the WhatsApp chats; --shard K/N: every N-th tournament, so N Actions runners, each its own IP, share the work; --max-requests N: after N requests touch cache/cr-tnr/.budget-used and exit 4)"""
import argparse, html, json, os, re, sys, threading, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import requests

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, "..")
OUT = os.path.join(HERE, "cache", "cr-tnr"); NONE = os.path.join(OUT, "none.txt")
ap = argparse.ArgumentParser(); ap.add_argument("--threads", type=int, default=4); ap.add_argument("--min-players", type=int, default=3)
ap.add_argument("--share", type=float, default=0.25); ap.add_argument("--all", action="store_true"); ap.add_argument("--only", default="")
ap.add_argument("--list", nargs="+", default=[os.path.join(ROOT, "assets", "gp-player-tournaments.js")]); ap.add_argument("--extra", default=""); ap.add_argument("--shard", default="")
ap.add_argument("--abroad", action="store_true"); ap.add_argument("--max-requests", type=int, default=0)   # 0 = no cap
a = ap.parse_args()
if a.abroad: OUT = os.path.join(OUT, "abroad"); NONE = os.path.join(OUT, "none.txt")
os.makedirs(OUT, exist_ok=True)

D = {"players": {}, "tournaments": {}}
for path in a.list:
    if not os.path.exists(path): continue
    x = json.loads(re.search(r"=\s*(\{.*\})\s*;?\s*$", open(path, encoding="utf-8").read(), re.S).group(1))
    D["tournaments"].update(x["tournaments"])
    for k, v in x["players"].items(): D["players"].setdefault(k, []).extend(v)
ours = {}
for lst in D["players"].values():
    for t in set(x[0] for x in lst): ours[t] = ours.get(t, 0) + 1
kenyan = lambda t, v: ours.get(t, 0) >= a.min_players and ours.get(t, 0) / max(v["np"], 1) >= a.share
todo = [t for t, v in D["tournaments"].items() if ((ours.get(t, 0) and not kenyan(t, v)) if a.abroad else (a.all or kenyan(t, v)))]
# --abroad: whose games to keep, by name (the pairings and PGNs carry names, not FIDE ids): our players in that tournament
words = lambda n: frozenset(re.sub(r"[^a-z ]", " ", str(n).lower()).split())
PEOPLE = {}
if a.abroad:
    pj = os.path.join(ROOT, "assets", "gp-players.js")
    PEOPLE = {k: words(v.get("n", "")) for k, v in json.loads(re.search(r"=\s*(\{.*\})\s*;?\s*$", open(pj, encoding="utf-8").read(), re.S).group(1))["people"].items()}
in_t = {}
for fid, lst in D["players"].items():
    for x in lst: in_t.setdefault(x[0], set()).add(fid)
def ours_name(t, name):
    w = words(name)
    return any(PEOPLE.get(f) and (PEOPLE[f] == w or (len(PEOPLE[f] & w) >= 2 and (PEOPLE[f] <= w or w <= PEOPLE[f]))) for f in in_t.get(t, ()))
def keep_ours(t, text):
    """Only the games of a PGN with one of our players in it."""
    games = re.split(r"\n(?=\[Event )", text.strip())
    keep = [g for g in games if any(ours_name(t, m.group(1)) for m in re.finditer(r'\[(?:White|Black) "([^"]*)"\]', g))]
    return "\n\n".join(keep) + ("\n" if keep else "")
none = set(open(NONE).read().split()) if os.path.exists(NONE) else set()
if a.abroad:   # already tried as a whole tournament: no games there, or its whole PGN is in the main folder
    main = os.path.dirname(OUT); mn = os.path.join(main, "none.txt")
    none |= set(open(mn).read().split()) if os.path.exists(mn) else set()
    none |= {f[:-4] for f in os.listdir(main) if f.endswith(".pgn")}
todo = [t for t in todo if t not in none and not os.path.exists(os.path.join(OUT, t + ".pgn"))]
todo += [t for t in a.extra.split(",") if t and t not in todo and t not in none and not os.path.exists(os.path.join(OUT, t + ".pgn"))]
for t in todo: D["tournaments"].setdefault(t, {"n": f"Tournament {t}", "rd": 0, "np": 0})   # a chat link no list has: name and rounds read from the pages
if a.only: todo = a.only.split(",")
if a.shard:
    k, n = map(int, a.shard.split("/")); todo = sorted(todo, key=int)[k - 1::n]
print(len(todo), "tournaments to check", flush=True)

def limited(r):
    """chess-results answers 200 with a one-line "daily limit exceeded" page once an IP passes 2,000 requests. Reading that as
    "no games" would put every tournament on none.txt for good, so stop instead (nothing else is written for this run)."""
    if r is not None and "exceeded! Your daily limit" in r.text[:2000]:
        print("chess-results has rate-limited this IP (2,000 page requests a day); stopping without marking anything as having no games", file=sys.stderr, flush=True)
        os._exit(3)
    return r

NREQ = 0; NLOCK = threading.Lock()
def get(sess, url, **kw):
    global NREQ
    if a.max_requests:   # budget spent: leave a marker (the workflow reads it) and stop before the next request
        with NLOCK:
            NREQ += 1
            over = NREQ > a.max_requests
        if over:
            open(os.path.join(HERE, "cache", "cr-tnr", ".budget-used"), "w").close()
            print(f"request budget of {a.max_requests} used; stopping", file=sys.stderr, flush=True)
            os._exit(4)
    for k in range(3):
        try: return limited(sess.get(url, timeout=60, **kw))
        except requests.RequestException: pass
    return None

RES = {"1 - 0": "1-0", "0 - 1": "0-1", "½ - ½": "1/2-1/2"}

NAMEISH = re.compile(r"[a-z]|,")   # a name has a lower-case letter or a comma; ratings, points, titles and federations do not

def name_near(cells, i, step):
    """The nearest cell from the result outwards that reads like a name (pairings tables differ: rating, points, title or federation
    columns can sit between the name and the result)."""
    k = i + step
    while 0 <= k < len(cells):
        c = cells[k]
        if len(c) >= 4 and NAMEISH.search(c): return c
        k += step
    return ""

def cells_of(row, tags="td"):
    return [html.unescape(re.sub(r"<[^>]*>", "", c)).strip() for c in re.findall(rf"<(?:{tags})[^>]*>(.*?)</(?:{tags})>", row, re.S)]

def boards(page, rd):
    """The boards of a pairings page: (round, White, Black, result, game id or None). The table's header row names the columns ("White",
    "Black", "Result"), which differ by tournament (title, "Typ" group, club, rating and points columns come and go), so the names are
    read from the columns the header gives. A round robin lists every round on one page under "Round N on <date>" rows."""
    out, col = [], None
    for cls, row in re.findall(r'<tr class="(CRg1b|CRg1|CRg2)[^"]*">(.*?)</tr>', page, re.S):
        if cls == "CRg1b":
            head = cells_of(row, "th|td")
            m = re.match(r"Round (\d+)\b", head[0]) if len(head) == 1 else None
            if m: rd = int(m.group(1))
            elif "White" in head and "Black" in head and "Result" in head: col = (head.index("White"), head.index("Black"), head.index("Result"), len(head))
            continue
        c = cells_of(row)
        link = re.search(r'PartieSuche\.aspx\?art=36&amp;id=(\d+)', row)
        if col and len(c) == col[3] and c[col[2]] in RES: w, b, res = c[col[0]], c[col[1]], c[col[2]]
        else:   # no header to go by: the nearest name-like cells either side of the result
            i = next((k for k, x in enumerate(c) if x in RES), None)
            if i is None: continue
            w, b, res = name_near(c, i, -1), name_near(c, i, 1), c[i]
        if w and b: out.append((rd, w, b, res, link.group(1) if link else None))
    return out

def by_game(sess, t, rounds):
    """PGN text of a tournament built from per-game links; "" if the pairings list none. A game is taken once, however many round pages
    list it (a round robin's page repeats every round)."""
    out, seen = [], set()
    for rd in range(1, (rounds or 15) + 1):   # rounds unknown (a chat link): until a round has no pairings
        r = get(sess, f"https://chess-results.com/tnr{t}.aspx?lan=1&art=2&rd={rd}&turdet=NO")
        if r is None: continue
        if not rounds and not re.search(r'<tr class="CRg[12]">', r.text): break
        for brd, white, black, res, gid in boards(r.text, rd):
            if not gid or gid in seen: continue
            if a.abroad and not (ours_name(t, white) or ours_name(t, black)): continue   # abroad: only our players' boards
            seen.add(gid)
            g = get(sess, f"https://chess-results.com/PartieSuche.aspx?art=36&id={gid}&lan=1")
            if g is None: continue
            mv = [re.sub(r"^\d+\.\s*", "", html.unescape(x).strip()) for x in re.findall(r'<a class="game0"[^>]*>([^<]+)</a>', g.text)]
            h = re.search(r"<br>(?:(.*?), )?(\d\d)\.(\d\d)\.(\d{4})</p>", g.text, re.S)
            if not mv or not h: continue
            ev, d, mo, y = h.groups(); ev = ev or D["tournaments"][t]["n"]
            text = " ".join((f"{k // 2 + 1}. " if k % 2 == 0 else "") + m for k, m in enumerate(mv))
            out.append(f'[Event "{ev.strip()}"]\n[Date "{y}.{mo}.{d}"]\n[Round "{brd}"]\n[White "{white}"]\n[Black "{black}"]\n[Result "{RES[res]}"]\n\n{text} {RES[res]}\n')
    return "\n".join(out)

def one(t):
    sess = requests.Session(); sess.headers["User-Agent"] = "Mozilla/5.0"
    r = get(sess, f"https://chess-results.com/tnr{t}.aspx?lan=1&art=2&rd=1&turdet=NO")
    if r is None: return t, "error"
    m = re.search(r"PartieSuche\.aspx\?lan=1&amp;id=(\d+)&amp;tnr=%s&amp;art=3" % t, r.text)
    if not m:
        if not re.search(r"PartieSuche\.aspx\?art=36&amp;id=\d+", r.text): return t, "none"
        text = by_game(sess, t, D["tournaments"][t]["rd"] or 0)
        if a.abroad: text = keep_ours(t, text)
        if "[Event " not in text: return t, "none"
        open(os.path.join(OUT, t + ".pgn"), "w", encoding="utf-8").write(text)
        return t, f"{text.count('[Event ')} games (per game)"
    r = get(sess, f"https://chess-results.com/PartieSuche.aspx?lan=1&id={m.group(1)}&tnr={t}&art=3")
    if r is None: return t, "error"
    f = {x.group(1): x.group(2) for x in re.finditer(r'<input type="hidden" name="([^"]+)"[^>]*value="([^"]*)"', r.text)}
    act = re.search(r'<form[^>]*action="([^"]+)"', r.text)
    if not act: return t, "error"
    f.update({"ctl00$P1$cb_DownLoadPGN": "Download as PGN-File", "ctl00$P1$txt_dbkey": t})
    for n in ("txt_von_tag", "txt_bis_tag", "txt_rdvon", "txt_rdbis"): f["ctl00$P1$" + n] = ""
    try: p = limited(sess.post(urllib.parse.urljoin(r.url, act.group(1).replace("&amp;", "&")), data=f, timeout=180))
    except requests.RequestException: return t, "error"
    if p.status_code != 200 or "[Event " not in p.text: return t, "none"
    text = keep_ours(t, p.text) if a.abroad else p.text
    if "[Event " not in text: return t, "none"
    open(os.path.join(OUT, t + ".pgn"), "w", encoding="utf-8").write(text)
    return t, f"{text.count('[Event ')} games"

res = {"none": 0, "error": 0, "ok": 0}
with ThreadPoolExecutor(a.threads) as ex:
    for t, what in ex.map(one, todo):
        if what == "none":
            open(NONE, "a").write(t + "\n"); res["none"] += 1
        elif what == "error": res["error"] += 1
        else: res["ok"] += 1; print(t, D["tournaments"][t]["n"][:50], what, flush=True)
print(res)
