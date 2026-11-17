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
# 1700009 "Nairobi Open Rapid" is not an Olympiad and must be dropped
# search: 1469895, 1700001, 1700002 (not the art=9 link 1700003); known.js: 1469895 (dup), 1256659; extra: 7, 42; "Open Championship" in known.js is not an Olympiad
assert got == ["7", "42", "1256659", "1469895", "1700001", "1700002"], got
r = run(os.path.join(F, "limit-page.html"))
assert r.returncode == 3 and r.stdout == "", (r.returncode, r.stdout, r.stderr)
import tempfile
big = os.path.join(tempfile.mkdtemp(), "big.html")
open(big, "w").write("<table class='CRs2'>" + "".join(f"<tr class='CRg1'><td><a href='tnr{i}.aspx'>Olympiad {i}</a></td></tr>" for i in range(1, 302)) + "</table>")
r = run(big); assert r.returncode == 2 and r.stdout == "", (r.returncode, r.stderr[-200:])
print("ok: union =", got, "; limit page exits 3")
