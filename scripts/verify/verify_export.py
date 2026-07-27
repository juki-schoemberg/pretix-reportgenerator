"""
Verification: run the built-in order list exporter through the *web* path
(the same path pretix-custom-reports will use) and check the CSV has rows.

Without a celery broker HAS_CELERY is False, so ExportDoView.do() executes the
task eagerly inside the request and answers with a redirect to the download URL.
"""
import io
import re
import sys
import zipfile

import requests

BASE = "http://localhost:8000"
EMAIL = "admin@localhost"
PASSWORD = "admin"
EV = "demo-event"

s = requests.Session()
fails = []


def check(label, cond, extra=""):
    print("{} {}{}".format("PASS" if cond else "FAIL", label, (" -- " + extra) if extra else ""))
    if not cond:
        fails.append(label)


def csrf(url):
    r = s.get(url)
    m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r.text)
    return (m.group(1) if m else ""), r


# login
token, _ = csrf(BASE + "/control/login")
s.post(BASE + "/control/login",
       data={"csrfmiddlewaretoken": token, "email": EMAIL, "password": PASSWORD},
       headers={"Referer": BASE + "/control/login"})

for event, sheet in ((EV, "orders"), ("demo-serie", "positions")):
    url = "/control/event/demo/{}/orders/export/".format(event)
    token, r = csrf(BASE + url + "?identifier=orderlist")
    check("export form page {} -> 200".format(event), r.status_code == 200)

    r = s.post(
        BASE + url + "do",
        data={
            "csrfmiddlewaretoken": token,
            "exporter": "orderlist",
            "orderlist-_format": "{}:default".format(sheet),
            "orderlist-paid_only": "",
            "orderlist-include_payment_amounts": "on",
        },
        headers={"Referer": BASE + url},
        allow_redirects=True,
    )
    check("POST export/do {} ({}) -> 200".format(event, sheet), r.status_code == 200,
          "final url={}".format(r.url))
    check("download URL reached for {}".format(event), "/download/" in r.url or "cachedfile" in r.url,
          r.url)

    ctype = r.headers.get("Content-Type", "")
    body = r.content
    if "zip" in ctype:
        zf = zipfile.ZipFile(io.BytesIO(body))
        names = zf.namelist()
        body = zf.read(names[0])
        print("     (zip with {})".format(names))
    text = body.decode("utf-8-sig", errors="replace")
    lines = [line for line in text.splitlines() if line.strip()]
    check("export for {} has a header row".format(event), len(lines) >= 1,
          "content-type={}".format(ctype))
    check("export for {} has data rows".format(event), len(lines) > 1,
          "{} lines total".format(len(lines)))
    print("     first data line: {}".format(lines[1][:160] if len(lines) > 1 else "(none)"))

print()
if fails:
    print("{} check(s) FAILED: {}".format(len(fails), ", ".join(fails)))
    sys.exit(1)
print("all export checks passed")
