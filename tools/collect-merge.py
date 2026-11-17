#!/usr/bin/env python3
"""Merge shard PGNs: python3 collect-merge.py PARTS_DIR OUT_DIR. Dedupes by game id; writes OUT/pgn/<tnr>.pgn and OUT/cr-games.jsonl."""
import sys, os, re, glob, json, hashlib
parts, out = sys.argv[1:3]; os.makedirs(out + "/pgn", exist_ok=True)
seen, n_in = set(), 0
H = lambda g, k: (re.search(r'\[%s "([^"]*)"\]' % k, g) or [0, ""])[1]
with open(out + "/cr-games.jsonl", "w", encoding="utf-8") as jl:
    for f in sorted(glob.glob(parts + "/**/*.pgn", recursive=True)):
        keep = []
        for g in re.split(r"\n(?=\[Event )", open(f, encoding="utf-8", errors="replace").read().strip()):
            if not g.startswith("[Event"): continue
            n_in += 1; moves = re.sub(r"\[.*?\]\s*", "", g).strip()
            gid = hashlib.sha1(f'{H(g,"White")}|{H(g,"Black")}|{H(g,"Date")}|{moves[:80]}'.encode()).hexdigest()[:8]
            if gid in seen: continue
            seen.add(gid); keep.append(g)
            jl.write(json.dumps({"id": gid, "tnr": os.path.basename(f)[:-4], "w": H(g, "White"), "b": H(g, "Black"), "date": H(g, "Date"), "result": H(g, "Result"), "event": H(g, "Event")}) + "\n")
        if keep: open(f'{out}/pgn/{os.path.basename(f)}', "a", encoding="utf-8").write("\n\n".join(keep) + "\n")
print(n_in, "games read,", len(seen), "unique")
