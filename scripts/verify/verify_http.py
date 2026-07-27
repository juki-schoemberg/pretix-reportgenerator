"""Verification: log into the running dev server and check the demo data is visible."""
import re
import sys

import requests

BASE = "http://localhost:8000"
EMAIL = "admin@localhost"
PASSWORD = "admin"

s = requests.Session()
fails = []


def check(label, cond, extra=""):
    print("{} {}{}".format("PASS" if cond else "FAIL", label, (" -- " + extra) if extra else ""))
    if not cond:
        fails.append(label)


# 1) unauthenticated /control/ redirects to the login page
r = s.get(BASE + "/control/", allow_redirects=False)
check("GET /control/ unauthenticated -> 302 to login", r.status_code == 302,
      "status={} location={}".format(r.status_code, r.headers.get("Location")))

# 2) login page
r = s.get(BASE + "/control/login")
check("GET /control/login -> 200", r.status_code == 200, "status={}".format(r.status_code))
m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r.text)
check("CSRF token in login form", bool(m))
token = m.group(1) if m else ""

# 3) log in
r = s.post(
    BASE + "/control/login",
    data={"csrfmiddlewaretoken": token, "email": EMAIL, "password": PASSWORD},
    headers={"Referer": BASE + "/control/login"},
    allow_redirects=True,
)
check("POST login with documented credentials", r.status_code == 200 and "/control/login" not in r.url,
      "final url={} status={}".format(r.url, r.status_code))

# 4) /control/ now returns 200
r = s.get(BASE + "/control/")
check("GET /control/ logged in -> 200", r.status_code == 200, "status={}".format(r.status_code))
body = r.text

# 5) both events visible in the event list
r = s.get(BASE + "/control/events/")
check("GET /control/events/ -> 200", r.status_code == 200)
events_html = r.text
check("event demo-event listed", "demo-event" in events_html)
check("event demo-serie listed", "demo-serie" in events_html)

# 6) order list of the single-date event contains rows
r = s.get(BASE + "/control/event/demo/demo-event/orders/")
check("GET order list demo-event -> 200", r.status_code == 200)
n_rows = len(re.findall(r"/control/event/demo/demo-event/orders/[A-Z0-9]{5}/", r.text))
check("order list has links to orders", n_rows > 0, "{} order links on page 1".format(n_rows))

# 7) sub-events of the series
r = s.get(BASE + "/control/event/demo/demo-serie/subevents/")
check("GET sub-event list demo-serie -> 200", r.status_code == 200)
check("sub-events listed", "Workshop" in r.text)

# 8) questions page
r = s.get(BASE + "/control/event/demo/demo-event/questions/")
check("GET questions demo-event -> 200", r.status_code == 200)
check("questions listed", "Alter" in r.text or "ALTER" in r.text)

# 9) the export page of the exporter infrastructure the plugin builds on
r = s.get(BASE + "/control/event/demo/demo-serie/orders/export/")
check("GET export page demo-serie -> 200", r.status_code == 200)
check("built-in orderlist exporter offered", "orderlist" in r.text)

print()
if fails:
    print("{} check(s) FAILED: {}".format(len(fails), ", ".join(fails)))
    sys.exit(1)
print("all HTTP checks passed")
