#!/usr/bin/env python3
"""Scrape every 2026 tournament per Kenyan FIDE id from chess-results.com.

Method: plain HTTP against the ASP.NET WebForms page SpielerSuche.aspx (player
database search). Per id: GET the form (cookie jar, follows the s1/s2/s3 mirror
redirect), copy the hidden __VIEWSTATE fields, POST fideID + tournament end
date 2026-01-01..2026-12-31 with the largest rows-per-page option, then parse
table.CRs2 rows (class CRg1/CRg2). Polite: one request at a time, 1-2 s pause,
backoff retries, resume file (.jsonl) so a restart skips finished ids, aborts
if the site keeps refusing. Ids come from assets/gp-players.js and
assets/gp-data.js (loaded via node). Output: assets/gp-player-tournaments.js.

Usage: python3 tools/scrape-player-tournaments.py [--from 2026-01-01 --to 2026-12-31] [--shard K/N --out FILE | --merge FILE... [--final FILE]]
  --from/--to: tournament end dates searched (default this season); --final: the file written (default assets/gp-player-tournaments.js;
  the 2020-2025 history goes to assets/gp-player-tournaments-history.js)
"""
import json, re, subprocess, sys, time, random, datetime, os
import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESUME = os.path.join(ROOT, ".player-tournaments.resume.jsonl")
URL = "https://chess-results.com/SpielerSuche.aspx?lan=1"
UA = "FRC254-site-bot/1.0 (+https://github.com/eugeneokemwa/frc254-site; Kenyan chess federation player tournament list)"
S = requests.Session()
S.headers["User-Agent"] = UA

JS = """const a=await import('./assets/gp-players.js'),b=await import('./assets/gp-data.js');
const s=new Set();for(const p of a.GP_PLAYERS.national)s.add(String(p.id));
const d=b.GP_SNAPSHOT;for(const k of Object.keys(d.histories||{}))s.add(k);
for(const l of ['open','ladies'])for(const r of (d.rankings[l]||[]))s.add(String(r.id));
try{const c=await import('./assets/gp-inactive.js');for(const p of c.GP_INACTIVE.players)s.add(String(p.id))}catch(e){}
console.log(JSON.stringify([...s].filter(x=>/^\\d+$/.test(x))))"""


def ids():
    return json.loads(subprocess.check_output(["node", "--input-type=module", "-e", JS], cwd=ROOT))


def hidden(soup):
    return {i["name"]: i.get("value", "") for i in soup.select("input[type=hidden]") if i.get("name")}


ARGS = sys.argv[1:]
opt = lambda k, d: ARGS[ARGS.index(k) + 1] if k in ARGS else d
FROM, TO = opt("--from", "2026-01-01"), opt("--to", "2026-12-31")
FINAL = opt("--final", os.path.join(ROOT, "assets/gp-player-tournaments.js"))


def post_form(fide):
    r = S.get(URL, timeout=40)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    f = hidden(soup)
    if "__VIEWSTATE" not in f:
        raise RuntimeError("empty form")
    sel = soup.find("select", {"name": "ctl00$P1$combo_anzahl_zeilen"})
    opts = [o["value"] for o in sel.find_all("option")]
    f.update({"ctl00$P1$txt_fideID": fide, "ctl00$P1$txt_von_tag": FROM,
              "ctl00$P1$txt_bis_tag": TO, "ctl00$P1$cb_suchen": "Search",
              "ctl00$P1$combo_anzahl_zeilen": max(opts, key=lambda v: int(v) if v.isdigit() else -1)})
    r = S.post(r.url, data=f, timeout=60)
    r.raise_for_status()
    return r.text


def parse(html):
    rows = []
    for tr in BeautifulSoup(html, "html.parser").select("table.CRs2 tr.CRg1, table.CRs2 tr.CRg2"):
        tds = tr.find_all("td")
        pl = tr.find("a", href=re.compile(r"art=9"))
        tl = next((x for x in tr.find_all("a", href=re.compile(r"tnr\d+")) if "art=9" not in x["href"]), None)
        if not pl or not tl:
            continue
        i = next(k for k, c in enumerate(tds) if tl in c.find_all("a"))
        end, rk, rd, np_ = (tds[i + j].get_text(strip=True) for j in (1, 2, 3, 4))
        snr = re.search(r"snr=(\d+)", pl["href"])
        rows.append({"t": re.search(r"tnr(\d+)", tl["href"]).group(1), "n": tl.get_text(strip=True),
                     "end": end.replace("/", "-"),
                     "rk": int(rk) if rk.isdigit() else None,
                     "rd": int(rd) if rd.isdigit() else None,
                     "np": int(np_) if np_.isdigit() else None,
                     "snr": int(snr.group(1)) if snr else None})
    return sorted(rows, key=lambda r: r["end"], reverse=True)


def fetch(fide):
    for k in range(5):
        try:
            return parse(post_form(fide))
        except Exception as e:
            print(f"  {fide} attempt {k + 1} failed: {e}", flush=True)
            time.sleep(5 * 2 ** k)
    return None


# Entries someone loaded into chess-results to try Swiss-Manager out (seminars,
# FIDE test uploads). Players in them did not play a tournament, so they are not
# listed. Matched by name; ids cover the ones a name cannot, e.g. "ABC".
TEST_NAME = re.compile(r"\btest(ing)?\b|swiss[- ]?manager", re.I)
TEST_IDS = {"1526260"}


def is_test(row):
    return row["t"] in TEST_IDS or bool(TEST_NAME.search(row["n"]))


def write_final(done):
    """Each tournament is stored once (name, end date, rounds, players) and a
    player is a list of [tournament, place, starting number], which is what
    keeps the file a fraction of the size of repeating those fields per player."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tournaments, players = {}, {}
    for k, rows in sorted(done.items()):
        rows = [r for r in (rows or []) if not is_test(r)]
        if not rows:
            continue
        for r in rows:
            tournaments.setdefault(r["t"], {"n": r["n"], "end": r["end"], "rd": r["rd"], "np": r["np"]})
        players[k] = [[r["t"], r["rk"], r["snr"]] for r in rows]
    data = {"generated": now, "season": int(TO[:4]) if FROM[:4] == TO[:4] else f"{FROM[:4]}-{TO[:4]}", "source": "chess-results.com",
            "tournaments": dict(sorted(tournaments.items())), "players": players}
    with open(FINAL, "w") as f:
        f.write("/* FRC254 player tournaments. GENERATED, DO NOT EDIT. Rebuild with tools/scrape-player-tournaments.py. "
                f"Source: chess-results.com player database, scraped {now}. "
                "tournaments: id -> {n name, end date, rd rounds, np players}; players: FIDE id -> [[tournament id, place or null, starting number]], newest first. */\n"
                f"export const GP_PLAYER_TOURNAMENTS = {json.dumps(data, separators=(',', ':'))};\n")
    print(f"players {len(players)}, tournaments {len(tournaments)}, entries {sum(map(len, players.values()))}")


def read_jsonl(path):
    done = {}
    if os.path.exists(path):
        for l in open(path):
            if l.strip():
                o = json.loads(l)
                done[o["id"]] = o["rows"]
    return done


def main():
    """No arguments: every id, one process, final file written.
    --shard K/N --out FILE: only every N-th id (K from 1), results to FILE (jsonl).
    --merge FILE...: combine shard files into the final file."""
    args = sys.argv[1:]
    if "--merge" in args:   # anywhere among the options: a merge must never fall through to scraping every id from one machine
        done = {}
        for f in args[args.index("--merge") + 1:]:
            if f.startswith("--"): break
            done.update(read_jsonl(f))
        return write_final(done)
    shard = out_path = None
    if "--shard" in args:
        k, n = map(int, args[args.index("--shard") + 1].split("/"))
        shard = (k, n)
    out_path = args[args.index("--out") + 1] if "--out" in args else RESUME
    done = read_jsonl(out_path)
    every = ids()
    if shard:
        every = [x for i, x in enumerate(every) if i % shard[1] == shard[0] - 1]
    todo = [i for i in every if i not in done]
    print(f"{len(done)} done, {len(todo)} to go", flush=True)
    fails, streak = [], 0
    with open(out_path, "a") as out:
        for n, fide in enumerate(todo):
            rows = fetch(fide)
            if rows is None:
                fails.append(fide)
                streak += 1
                if streak >= 5:
                    sys.exit("site refusing requests, stopping; rerun to resume")
            else:
                streak = 0
                done[fide] = rows
                out.write(json.dumps({"id": fide, "rows": rows}) + "\n")
                out.flush()
            if n % 25 == 0:
                print(f"{n}/{len(todo)}", flush=True)
            time.sleep(1 + random.random())
    print(f"searched {len(done) + len(fails)}, failed {fails}")
    if not shard:
        write_final(done)


if __name__ == "__main__":
    main()
