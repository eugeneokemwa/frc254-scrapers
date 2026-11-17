#!/usr/bin/env python3
"""Offline: an --only id that no list names (discovered Olympiad) must not crash the final print (was KeyError). Network mocked."""
import os, subprocess, sys, tempfile
src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scrape-cr-tournaments.py")
d = tempfile.mkdtemp(); s = os.path.join(d, "tools"); os.makedirs(s); open(os.path.join(d, "drv.py"), "w").write('''import runpy, sys, requests
class R:
    text = "<html></html>"; status_code = 200; url = "http://x/y"
class L(R): text = '<a href="PartieSuche.aspx?lan=1&amp;id=5&amp;tnr=77&amp;art=3"></a><form action="z"></form>'
class P(R): text = '[Event "x"]\\n1. e4 *'
requests.Session.get = lambda self, *a, **k: L()
requests.Session.post = lambda self, *a, **k: P()
sys.argv = ["s.py", "--only", "77", "--list", "/nonexistent", "--threads", "1"]
runpy.run_path(%r)
''' % os.path.join(s, "s.py"))
import shutil; shutil.copy(src, os.path.join(s, "s.py"))
p = subprocess.run([sys.executable, os.path.join(d, "drv.py")], capture_output=True, text=True, timeout=60)
assert p.returncode == 0 and "77 77 1 games" in p.stdout, (p.returncode, p.stdout, p.stderr[-400:])
print("ok")
