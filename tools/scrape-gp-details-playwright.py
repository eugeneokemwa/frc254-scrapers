#!/usr/bin/env python3
"""Playwright port of the site's scrape-gp-details-kiwi.py, for GitHub Actions.
Fetches the chess-results "tournament details" panel for the given events (one at a time, with pauses)
and merges them into assets/gp-details.js (the live copy downloaded by the workflow).
Usage: scrape-gp-details-playwright.py 1538715,1538715_ladies   (an item may be key=tnr)
"""
import datetime as dt, json, os, re, sys, time

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "gp-details.js")
# Ladies sections are separate chess-results tournaments; key is the site's event id.
TNR = {"1538715_ladies": "1540970"}
BASE = "https://chess-results.com/tnr{id}.aspx?lan=1"
PAUSE = 8
FIELDS = {"federation": "federation", "tournament director": "director", "chief arbiter": "chiefArbiter",
          "deputy chief arbiter": "deputyArbiter", "arbiter": "arbiters", "location": "location",
          "venue": "location", "number of rounds": "rounds", "tournament type": "format",
          "rating calculation": "ratingCalc", "date": "dates", "organizer": "organizer"}


def clean_people(value):
    value = re.sub(r"\.\.\..*?All arbiters", "", value, flags=re.I).strip(" ;")
    out = []
    for part in value.split(";"):
        part = re.sub(r"\s+", " ", part).strip()
        if not part:
            continue
        m = re.search(r"\b(\d{7,9})\s*$", part)
        out.append({"name": part[:m.start()].strip() if m else part, "fideId": m.group(1) if m else None})
    return out


def parse_text(txt):
    """Same as the Kiwi script: innerText gives `label<TAB>value` lines."""
    data = {}
    for line in txt.split("\n"):
        if "\t" not in line:
            continue
        label, _, value = line.partition("\t")
        label = re.sub(r"\s+", " ", label).strip().lower().rstrip(":")
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            continue
        if label.startswith("time control"):
            kind = re.search(r"\((.*?)\)", label)
            data["timeControl"] = value
            if kind:
                data["timeControlKind"] = kind.group(1).title()
            continue
        if "average age" in label:
            m = re.match(r"(\d+)\s*/\s*(\d+)", value)
            if m:
                data["avgRating"], data["avgAge"] = int(m.group(1)), int(m.group(2))
            continue
        key = FIELDS.get(label)
        if not key:
            continue
        if key in ("arbiters", "chiefArbiter", "deputyArbiter", "director"):
            people = clean_people(value)
            data[key] = people if key == "arbiters" else (people[0] if people else None)
        elif key == "rounds":
            m = re.match(r"\d+", value)
            if m:
                data[key] = int(m.group())
        else:
            data[key] = value
    return data


def scrape(page, tnr):
    page.goto(BASE.format(id=tnr), wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    text = lambda: page.inner_text("body")
    if "Chief Arbiter" not in text():
        link = page.locator("a", has_text=re.compile("tournament details", re.I))
        if not link.count():
            # Archived events hide links behind this button.
            if page.locator("#cb_alleDetails").count():
                page.click("#cb_alleDetails")
                page.wait_for_timeout(4000)
        if not link.count():
            raise ValueError("no 'Show tournament details' link")
        link.first.click()
        page.wait_for_function(
            "document.body.innerText.includes('Chief Arbiter') && document.body.innerText.includes('Time control')",
            timeout=45000)
    detail = parse_text(text())
    if not detail:
        raise ValueError("details panel did not render")
    return detail


def main():
    from playwright.sync_api import sync_playwright
    items = []
    for s in sys.argv[1].split(","):
        key, _, tnr = s.strip().partition("=")
        if key:
            items.append((key, tnr or TNR.get(key) or key.split("_")[0]))
    raw = open(OUT, encoding="utf-8").read()
    details = json.loads(raw[raw.index("{"):raw.rindex(";")]).get("details", {})
    print(f"live file has {len(details)} entries", file=sys.stderr)
    failed = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(locale="en-US")
        for i, (key, tnr) in enumerate(items):
            if i:
                time.sleep(PAUSE)
            for attempt in (1, 2):
                page = ctx.new_page()
                try:
                    d = scrape(page, tnr)
                    d["sourceId"] = tnr
                    d["chessResultsUrl"] = f"https://chess-results.com/tnr{tnr}.aspx?lan=1"
                    details[key] = d
                    print(f"{key} (tnr{tnr}) ok: {sorted(d)}", file=sys.stderr)
                    break
                except Exception as e:  # noqa: BLE001
                    print(f"{key} attempt {attempt} failed: {e}", file=sys.stderr)
                    if attempt == 2:
                        failed.append(key)
                    time.sleep(5)
                finally:
                    page.close()
        browser.close()
    if len(failed) == len(items):
        return 1
    gen = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    body = json.dumps({"generated": gen, "source": "chess-results.com", "details": details},
                      ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("/* ========================================================================\n"
                "   FRC254 — Grand Prix tournament details. GENERATED, DO NOT EDIT.\n"
                "   Rebuild with: python3 tools/scrape-gp-details-kiwi.py (needs Kiwi+adb)\n"
                f"   {len(details)} tournaments · scraped {gen}\n"
                "   Source: chess-results.com tournament detail panels.\n"
                "   ===================================================================== */\n"
                f"export const GP_DETAILS = {body};\n")
    print(f"wrote {OUT}; failed: {failed}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
