#!/usr/bin/env python3
"""Precompute Chess Lab game reviews for every game in assets/games/lib/*.json (the id shards that tools/build-gp-games.py writes) (needs a `stockfish` binary and python-chess).
Usage: build-reviews.py [--shard I/N] [--level quick|standard|deep] [--out lab/reviews] [--only id,id] [--skip reviewed.txt] [--minutes M]
(--skip: a file of game ids already reviewed, one per line, for a runner that does not hold lab/reviews; --minutes: start no new game
 after M minutes, so a runner with a time limit still uploads what it finished)
Writes <out>/<game id>.json.gz (gzipped JSON) in the same shape as Chess Lab's own reviews (tools/chesslab/ is a copy of its `gr/`
package). Games already in <out> are skipped. The review is written from White's side ("you" is White): the site has no
signed-in player. shortcut: no 'similar games' (needs Chess Lab's library), no AI rewrite."""
import argparse, glob, gzip, io, json, os, re, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "chesslab"))
import chess.pgn
from gr import analysis, coach

ap = argparse.ArgumentParser()
ap.add_argument("--shard", default="1/1"); ap.add_argument("--level", default="quick")
ap.add_argument("--out", default=os.path.join(HERE, "..", "lab", "reviews")); ap.add_argument("--only", default=""); ap.add_argument("--skip", default=""); ap.add_argument("--minutes", type=float, default=0)
a = ap.parse_args()
si, sn = map(int, a.shard.split("/"))
games = {}
for f in sorted(glob.glob(os.path.join(HERE, "..", "assets", "games", "lib", "*.json"))):   # every game, also those with no FIDE id
    for r in json.load(open(f, encoding="utf-8")): games[r[0]] = r
ids = sorted(games)
os.makedirs(a.out, exist_ok=True)
have = os.path.join(HERE, "..", "lab", "reviews")   # reviews already committed count as done even when --out is elsewhere
done = set(open(a.skip).read().split()) if a.skip else set()
left = [x for x in ids if x not in done and not os.path.exists(os.path.join(a.out, f"{x}.json.gz")) and not os.path.exists(os.path.join(have, f"{x}.json.gz"))]   # shard what is still to do, so reruns share the work evenly
todo = [x for x in a.only.split(",") if x] or [x for k, x in enumerate(left) if k % sn == si - 1]

def pgn_of(r):
    _, w, b, wf, bf, res, date, ev, rd, moves, _fix = r
    h = [("Event", ev), ("Site", "Kenya"), ("Date", date.replace("-", ".")), ("Round", rd or "?"), ("White", w), ("Black", b), ("Result", res)]
    return "".join(f'[{k} "{v}"]\n' for k, v in h) + "\n" + moves + " " + res + "\n"

T0 = time.time()
for i in todo:
    if a.minutes and time.time() - T0 > a.minutes * 60: print("time budget used; stopping", flush=True); break
    path = os.path.join(a.out, f"{i}.json.gz")
    if os.path.exists(path): continue
    t0 = time.time()
    try:
        game = chess.pgn.read_game(io.StringIO(pgn_of(games[i])))
        review, eng = analysis.analyze(game, a.level, progress=lambda *_: None)
        try: review["puzzles"] = analysis.build_puzzles(eng, review["moves"], True)
        finally: eng.quit()
        h = dict(game.headers)
        names = {"White": h["White"].strip() or "White", "Black": h["Black"].strip() or "Black"}
        review.update(headers=h, names=names, me="white", pgn=str(game), level=a.level)
        review["insights"] = coach.coach(review, True, names)
        review.update(similar={"groups": [], "note": "Similar games are not available on the site."}, id=i, library_id=None, created=time.strftime("%Y-%m-%d %H:%M"))
        with gzip.open(path + ".tmp", "wt", encoding="utf-8", compresslevel=9) as fh: json.dump(review, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(path + ".tmp", path)
        print(i, names["White"], "-", names["Black"], f"{time.time() - t0:.0f}s", flush=True)
    except Exception as e:  # noqa: BLE001 - one bad game must not stop the shard
        import traceback; print(i, "FAILED", e, flush=True); traceback.print_exc(file=sys.stdout)
