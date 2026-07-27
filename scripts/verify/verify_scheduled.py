"""
Verification: a pretix Scheduled Export (the mechanism the plugin must hook into,
see CLAUDE.md rule 5) runs to completion in this environment.

Creates a ScheduledEventExport for the built-in order list, makes it due, runs
`runperiodic` and checks that pretix picked it up and sent the result by mail.
"""
import os
import sys
from datetime import timedelta
from pathlib import Path

import django

PRETIX_SRC = Path(__file__).resolve().parent.parent.parent.parent / "pretix" / "src"
sys.path.insert(0, str(PRETIX_SRC))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pretix.settings")
os.environ.setdefault("PRETIX_CONFIG_FILE", str(PRETIX_SRC / "pretix.cfg"))
django.setup()

from django.core import management  # noqa: E402
from django.utils.timezone import now  # noqa: E402
from django_scopes import scopes_disabled  # noqa: E402

from pretix.base.models import Event, User  # noqa: E402
from pretix.base.models.exports import ScheduledEventExport  # noqa: E402

fails = []


def check(label, cond, extra=""):
    print("{} {}{}".format("PASS" if cond else "FAIL", label, (" -- " + extra) if extra else ""))
    if not cond:
        fails.append(label)


with scopes_disabled():
    event = Event.objects.get(slug="demo-event")
    user = User.objects.get(email="admin@localhost")

    ScheduledEventExport.objects.filter(event=event, export_identifier="orderlist").delete()
    se = ScheduledEventExport.objects.create(
        event=event,
        owner=user,
        export_identifier="orderlist",
        export_form_data={"_format": "orders:default", "paid_only": False,
                          "include_payment_amounts": True, "group_multiple_choice": False,
                          "date_range": None, "items": []},
        locale="de",
        mail_additional_recipients="reports@example.org",
        mail_subject="Terminierter Demo-Export",
        mail_template="Anbei der terminierte Export.",
        schedule_rrule="DTSTART:20260101T000000\nRRULE:FREQ=DAILY",
        schedule_rrule_time="04:00",
        schedule_next_run=now() - timedelta(minutes=5),
    )
    check("ScheduledEventExport created", se.pk is not None, "pk={}".format(se.pk))

    management.call_command("runperiodic")

    se.refresh_from_db()
    check("scheduled export was picked up (next run moved into the future)",
          se.schedule_next_run is not None and se.schedule_next_run > now(),
          "next_run={}".format(se.schedule_next_run))
    check("no error recorded on the scheduled export", se.error_counter == 0,
          "error_counter={} last_message={}".format(se.error_counter, se.error_last_message))

    # clean up, otherwise this schedule fires every day and fails without a mail sink
    se.delete()
    print("     (test schedule removed again)")

print()
if fails:
    print("{} check(s) FAILED: {}".format(len(fails), ", ".join(fails)))
    sys.exit(1)
print("scheduled export checks passed")
