#!/usr/bin/env python3
"""Offline test for discover-olympiads.py against hand-made fixtures (no network). Usage: test-discover-olympiads.py"""
import os, subprocess, sys
D = os.path.dirname(os.path.abspath(__file__)); F = os.path.join(D, "..", "fixtures")
if not os.path.isdir(F): F = os.path.join(D, "..", "tools", "fixtures")
S = os.path.join(D, "discover-olympiads.py")
def run(page, *more): return subprocess.run([os.environ.get("PY", sys.executable), S, "--page", page, "--known", os.path.join(F, "known.js"), *more], capture_output=True, text=True)
r = run(os.path.join(F, "olympiad-search.html"), "--extra", "42, 7")
assert r.returncode == 0, r.stderr
got = r.stdout.split()
# search: 1469895, 1700001, 1700002 (not the art=9 link 1700003); known.js: 1469895 (dup), 1256659; extra: 7, 42; "Open Championship" in known.js is not an Olympiad
assert got == ["7", "42", "1256659", "1469895", "1700001", "1700002"], got
r = run(os.path.join(F, "limit-page.html"))
assert r.returncode == 3 and r.stdout == "", (r.returncode, r.stdout, r.stderr)
print("ok: union =", got, "; limit page exits 3")
