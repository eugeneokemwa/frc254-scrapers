#!/usr/bin/env python3
"""Offline: with requests mocked, --max-requests must count retries and the PGN POST, exit 4 and leave cache/cr-tnr/.budget-used;
a clean exit records the count in .requests-used. Never touches the network."""
import os, shutil, subprocess, sys, tempfile
src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scrape-cr-tournaments.py")

def run(mock, only, cap):
    d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "tools")); s = os.path.join(d, "tools", "s.py"); shutil.copy(src, s)
    open(os.path.join(d, "drv.py"), "w").write('''import runpy, sys, requests
class R:
    text = "<html>nothing</html>"; status_code = 200; url = "http://x/y"
class L(R): text = '<a href="PartieSuche.aspx?lan=1&amp;id=5&amp;tnr=1&amp;art=3"></a><form action="z"></form>'
''' + mock + '''
sys.argv = ["s.py", "--only", %r, "--list", "/nonexistent", "--threads", "1", "--max-requests", %r]
runpy.run_path(%r)
''' % (only, str(cap), s))
    p = subprocess.run([sys.executable, os.path.join(d, "drv.py")], capture_output=True, text=True, timeout=60)
    out = os.path.join(d, "tools", "cache", "cr-tnr"); rd = lambda f: open(os.path.join(out, f)).read() if os.path.exists(os.path.join(out, f)) else None
    r = (p.returncode, rd(".budget-used") is not None, rd(".requests-used"), p.stderr[-300:]); shutil.rmtree(d); return r

# plain: 3 tournaments, 1 get each, cap 2 -> third get exits 4
rc, flag, _, err = run("requests.Session.get = lambda self, *a, **k: R()", "1,2,3", 2); assert rc == 4 and flag, (rc, flag, err)
# retries: one tournament whose get always fails = 3 real attempts; cap 2 must stop it (old code counted once)
rc, flag, _, err = run("def bad(self, *a, **k): raise requests.RequestException()\nrequests.Session.get = bad", "1", 2); assert rc == 4 and flag, (rc, flag, err)
# POST: 2 gets + 1 post = 3 requests; cap 2 must stop it (old code never counted the post)
rc, flag, _, err = run("requests.Session.get = lambda self, *a, **k: L()\nrequests.Session.post = lambda self, *a, **k: R()", "1", 2); assert rc == 4 and flag, (rc, flag, err)
# under budget: clean exit, no marker, count recorded
rc, flag, used, err = run("requests.Session.get = lambda self, *a, **k: L()\nrequests.Session.post = lambda self, *a, **k: R()", "1", 9); assert rc == 0 and not flag and used == "3", (rc, flag, used, err)
print("ok")
