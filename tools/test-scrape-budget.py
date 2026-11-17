#!/usr/bin/env python3
"""Offline: with get() mocked, --max-requests 2 must exit 4 and leave cache/cr-tnr/.budget-used. Never touches the network."""
import os, shutil, subprocess, sys, tempfile
src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scrape-cr-tournaments.py")
d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "tools"))
shutil.copy(src, os.path.join(d, "tools", "s.py"))
drv = os.path.join(d, "drv.py")
open(drv, "w").write('''import runpy, sys, requests
class R: text = "<html>nothing</html>"; status_code = 200; content = b""
requests.Session.get = lambda self, *a, **k: R()   # no real request can leave
sys.argv = ["s.py", "--only", "1,2,3", "--list", "/nonexistent", "--threads", "1", "--max-requests", "2"]
runpy.run_path(sys.argv and %r)
''' % os.path.join(d, "tools", "s.py"))
p = subprocess.run([sys.executable, drv], capture_output=True, text=True, timeout=60)
flag = os.path.join(d, "tools", "cache", "cr-tnr", ".budget-used")
assert p.returncode == 4, (p.returncode, p.stderr[-500:])
assert os.path.exists(flag), "no .budget-used"
shutil.rmtree(d); print("ok")
