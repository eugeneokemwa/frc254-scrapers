#!/usr/bin/env python3
"""Monthly FIDE ratings (standard, rapid, blitz) of the players in our games, from FIDE's archived lists
(https://ratings.fide.com/download/<kind>_<mon><yy>frl_xml.zip, ~10-40 MB each, one request each; the current month is
<kind>_rating_list_xml.zip). For "rating at the time of the game": build-gp-games.py reads the merged file into assets/games/elo.json.
Heavy (gigabytes of downloads): run it on the runner (workflow "FIDE history"), never the phone.
Usage: fide-history.py --ids ids.txt --months 2020-01:2026-11 [--list 2020-01,2021-05] [--shard K/N] --out hist.json
       fide-history.py --merge hist-*.json --out tools/cache/fide-history.json
Output: {"YYYY-MM": {"standard": {id: rating}, "rapid": {...}, "blitz": {...}}}"""
import argparse, datetime as dt, json, os, sys, time, zipfile
import xml.etree.ElementTree as ET
import requests

KINDS = ("standard", "rapid", "blitz")
ap = argparse.ArgumentParser()
ap.add_argument("--ids"); ap.add_argument("--months", default="2020-01:2026-11"); ap.add_argument("--list", default=""); ap.add_argument("--shard", default="1/1")
ap.add_argument("--merge", nargs="*"); ap.add_argument("--out", required=True)
a = ap.parse_args()

if a.merge:
    out = {}
    for f in a.merge:
        for m, v in json.load(open(f)).items(): out.setdefault(m, {}).update(v)
    json.dump(out, open(a.out, "w"), separators=(",", ":"), sort_keys=True)
    print(len(out), "months merged:", ", ".join(f"{m} {'/'.join(k[0] for k in out[m])}" for m in sorted(out)))
    sys.exit()

ids = set(open(a.ids).read().split())
lo, hi = a.months.split(":")
months, d = [], dt.date(int(lo[:4]), int(lo[5:]), 1)
while d <= dt.date(int(hi[:4]), int(hi[5:]), 1):
    months.append(d); d = (d + dt.timedelta(days=32)).replace(day=1)
if a.list: months = [dt.date(int(m[:4]), int(m[5:7]), 1) for m in a.list.split(",") if m]   # just these months (a rerun of the gaps)
k, n = map(int, a.shard.split("/")); months = months[k - 1::n]
newest = dt.date.today().replace(day=1)

def fetch(url, path):
    for i in range(4):
        try:
            t0 = time.time()
            with requests.get(url, stream=True, timeout=(30, 60), headers={"User-Agent": "Mozilla/5.0"}) as r:
                if r.status_code == 404: return False
                r.raise_for_status()
                with open(path, "wb") as fh:
                    for c in r.iter_content(1 << 20):
                        fh.write(c)
                        if time.time() - t0 > 600: raise requests.ConnectionError("stalled: over 10 minutes")   # FIDE's server sometimes trickles forever
            if zipfile.is_zipfile(path): return True
        except requests.RequestException as e: print("  retry", url, e, flush=True)
        time.sleep(10 * (i + 1))
    return False

out = {}
for d in months:
    key = f"{d:%Y-%m}"
    for kind in KINDS:
        urls = [f"https://ratings.fide.com/download/{kind}_{d.strftime('%b').lower()}{d:%y}frl_xml.zip"]
        if d == newest: urls.append(f"https://ratings.fide.com/download/{kind}_rating_list_xml.zip")
        path = f"/tmp/{kind}-{key}.zip"
        if not any(fetch(u, path) for u in urls): print(key, kind, "not found", flush=True); continue
        got = {}
        z = zipfile.ZipFile(path)
        with z.open(z.namelist()[0]) as f:
            for _, el in ET.iterparse(f, events=("end",)):
                if el.tag != "player": continue
                i = (el.findtext("fideid") or "").strip()
                if i in ids:
                    r = (el.findtext("rating") or "").strip()
                    if r.isdigit() and int(r): got[i] = int(r)
                el.clear()
        os.remove(path)
        out.setdefault(key, {})[kind] = got
        print(key, kind, len(got), flush=True)
        json.dump(out, open(a.out, "w"), separators=(",", ":"))   # keep what is done if a later download dies
        time.sleep(2)
