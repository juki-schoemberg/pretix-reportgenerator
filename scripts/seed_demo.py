#!/usr/bin/env python
"""
Demo data for the pretix-custom-reports development environment.

Creates one organizer with two events -- a single-date event and an event series
with sub-events -- products, questions of every type and roughly 200 orders with
a realistic spread of statuses, payments, invoice addresses, vouchers, check-ins
and (deliberately) unanswered questions.

The gaps are intentional. A report builder that is only tested against clean data
always looks correct; the wrong rows appear on canceled orders, missing invoice
addresses and unanswered questions.

Usage (venv must be active, see scripts/start-dev.sh):

    python scripts/seed_demo.py --reset
    python scripts/seed_demo.py --reset --orders 60     # faster, for quick loops
    python scripts/seed_demo.py --list                  # only print a summary

Every API used here was verified against the pinned pretix source in ../pretix.
"""

import argparse
import os
import random
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

# --------------------------------------------------------------------------- #
# Django bootstrap
# --------------------------------------------------------------------------- #

REPO_DIR = Path(__file__).resolve().parent.parent
WORK_DIR = REPO_DIR.parent
PRETIX_SRC = WORK_DIR / "pretix" / "src"

if not PRETIX_SRC.is_dir():
    sys.exit(
        "pretix source not found at {}.\n"
        "Expected layout: <work dir>/{{venv, pretix, data, {}}}".format(
            PRETIX_SRC, REPO_DIR.name
        )
    )

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pretix.settings")
os.environ.setdefault("PRETIX_CONFIG_FILE", str(PRETIX_SRC / "pretix.cfg"))
sys.path.insert(0, str(PRETIX_SRC))

import django  # noqa: E402

django.setup()

from django.core.files.base import ContentFile  # noqa: E402
from django.db import transaction  # noqa: E402
from django.utils.timezone import get_current_timezone, make_aware  # noqa: E402
from django_scopes import scopes_disabled  # noqa: E402

from pretix.base.models import (  # noqa: E402
    Checkin, Event, Invoice, InvoiceAddress, Order, OrderFee, OrderPayment,
    OrderPosition, OrderRefund, Organizer, Question, QuestionAnswer, User,
    Voucher,
)
from pretix.base.models.invoices import InvoiceLine  # noqa: E402
from pretix.base.models.orders import Transaction  # noqa: E402

ORGANIZER_SLUG = "demo"
EVENT_SINGLE = "demo-event"
EVENT_SERIES = "demo-serie"
DEFAULT_SEED = 20260727
DEFAULT_ORDERS = 200

PLUGINS = "pretix.plugins.banktransfer,pretix.plugins.ticketoutputpdf,pretix.plugins.statistics"

# --------------------------------------------------------------------------- #
# German test data pools
# --------------------------------------------------------------------------- #

FIRST_NAMES = [
    "Anna", "Lukas", "Sophie", "Maximilian", "Marie", "Paul", "Emma", "Jonas",
    "Hannah", "Felix", "Lena", "Elias", "Mia", "Noah", "Laura", "Leon",
    "Julia", "Finn", "Katharina", "Moritz", "Johanna", "Tobias", "Clara",
    "Sebastian", "Nele", "Philipp", "Charlotte", "Jan", "Franziska", "Niklas",
    "Ingrid", "Hans-Peter", "Renate", "Wolfgang", "Ursula", "Dieter", "Gisela",
    "Ayse", "Mehmet", "Nguyen", "Zoe",
]

LAST_NAMES = [
    "Müller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer", "Wagner",
    "Becker", "Schulz", "Hoffmann", "Schäfer", "Koch", "Bauer", "Richter",
    "Klein", "Wolf", "Schröder", "Neumann", "Schwarz", "Zimmermann", "Braun",
    "Krüger", "Hofmann", "Hartmann", "Lange", "Schmitt", "Werner", "Krause",
    "Meier", "Lehmann", "von Grünberg", "Öztürk", "Nowak",
]

TITLES = ["", "", "", "", "", "", "", "Dr.", "Prof. Dr.", "Dipl.-Ing."]

COMPANIES = [
    ("Stadtwerke Musterstadt GmbH", "DE811234567"),
    ("Bäckerei Kranz e.K.", ""),
    ("Nordlicht Software AG", "DE129876543"),
    ("Kulturverein Alte Mühle e.V.", ""),
    ("Weißenburger Maschinenbau GmbH & Co. KG", "DE145678912"),
    ("Praxis Dr. Ahrens", ""),
    ("Blaupause Werbeagentur UG (haftungsbeschränkt)", "DE167891234"),
    ("Fahrschule Sonnenberg", ""),
    ("Hanseatische Versicherung AG", "DE178912345"),
    ("Öko-Hof Lindenhain GbR", ""),
]

ADDRESSES = [
    ("Bahnhofstraße 12", "80335", "München", "DE"),
    ("Lindenallee 4a", "20095", "Hamburg", "DE"),
    ("Am Sportplatz 7", "04109", "Leipzig", "DE"),
    ("Gartenweg 19", "50667", "Köln", "DE"),
    ("Kirchplatz 2", "99084", "Erfurt", "DE"),
    ("Hauptstraße 118", "70173", "Stuttgart", "DE"),
    ("Zur alten Schmiede 3", "26122", "Oldenburg", "DE"),
    ("Mühlenstraße 45", "01067", "Dresden", "DE"),
    ("Schillerstraße 8", "90402", "Nürnberg", "DE"),
    ("Rosenweg 23", "24103", "Kiel", "DE"),
    ("Feldbergstraße 61", "60323", "Frankfurt am Main", "DE"),
    ("Im Winkel 5", "48143", "Münster", "DE"),
    ("Karl-Marx-Allee 90", "10243", "Berlin", "DE"),
    ("Ringstraße 14", "6020", "Innsbruck", "AT"),
    ("Bahnhofplatz 1", "8001", "Zürich", "CH"),
]

MAIL_DOMAINS = ["example.org", "example.com", "mailinator.test", "beispiel.de"]

COMMENTS = [
    "Kunde hat telefonisch um Rechnung per Post gebeten.",
    "Sammelbestellung Abteilung Vertrieb.",
    "Ermäßigung geprüft, Nachweis liegt vor.",
    "Zahlungserinnerung am Montag verschickt.",
    "",
    "",
    "",
]

FREE_TEXTS = [
    "Ich bin auf einen barrierefreien Zugang angewiesen.",
    "Bitte um einen Sitzplatz in der ersten Reihe.\nDanke!",
    "Reise mit dem Zug an, komme evtl. 15 Minuten später.",
    "Keine besonderen Wünsche.",
    "Ich möchte gern beim Aufbau helfen.\nMeldet euch einfach.",
    "Vegetarisches Essen bitte auch am Vorabend.",
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def aware(d, h=10, m=0):
    return make_aware(datetime.combine(d, time(h, m)), get_current_timezone())


class Seeder:
    def __init__(self, rng, n_orders, quiet=False):
        self.rng = rng
        self.n_orders = n_orders
        self.quiet = quiet
        self.today = date.today()

    def log(self, msg):
        if not self.quiet:
            print(msg, flush=True)

    # -- people ---------------------------------------------------------- #

    def name_parts(self):
        return {
            "_scheme": "title_given_family",
            "title": self.rng.choice(TITLES),
            "given_name": self.rng.choice(FIRST_NAMES),
            "family_name": self.rng.choice(LAST_NAMES),
        }

    def email_for(self, parts):
        def clean(s):
            table = str.maketrans(
                {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe",
                 "Ü": "ue", "ş": "s", "ğ": "g", "ı": "i", "đ": "d", "ć": "c",
                 "ë": "e", " ": "", "-": "", ".": ""}
            )
            return s.lower().translate(table)

        return "{}.{}{}@{}".format(
            clean(parts["given_name"]),
            clean(parts["family_name"]),
            self.rng.choice(["", "", "", str(self.rng.randint(2, 99))]),
            self.rng.choice(MAIL_DOMAINS),
        )

    # -- structure ------------------------------------------------------- #

    def create_organizer(self):
        o = Organizer.objects.create(name="Demo Veranstalter GmbH", slug=ORGANIZER_SLUG)
        self.log("organizer  {} ({})".format(o.name, o.slug))
        self.create_team(o)
        return o

    def create_team(self, organizer):
        """
        Without a team membership even a superuser sees no events in the backend --
        pretix derives backend permissions from Team objects, not from is_staff.
        (Team.all_event_permissions / all_organizer_permissions, see
        ../pretix/src/pretix/base/models/organizer.py)
        """
        team = organizer.teams.create(
            name="Demo-Team (alle Rechte)",
            all_events=True,
            all_event_permissions=True,
            all_organizer_permissions=True,
        )
        users = list(User.objects.filter(is_active=True))
        if users:
            team.members.add(*users)
        self.log("team       {} -- members: {}".format(
            team.name, ", ".join(u.email for u in users) or "(no users yet!)"))
        return team

    def _event_settings(self, event, invoices=True):
        event.settings.set("timezone", "Europe/Berlin")
        event.settings.set("locales", ["de", "en"])
        event.settings.set("locale", "de")
        event.settings.set("attendee_names_asked", True)
        event.settings.set("attendee_names_required", False)
        event.settings.set("attendee_emails_asked", True)
        event.settings.set("name_scheme", "title_given_family")
        event.settings.set("waiting_list_enabled", True)
        event.settings.set("invoice_address_asked", True)
        event.settings.set("invoice_address_vatid", True)
        event.settings.set("invoice_generate", "paid" if invoices else "False")
        event.settings.set("invoice_numbers_consecutive", True)

    def create_single_event(self, organizer):
        ev = Event.objects.create(
            organizer=organizer,
            name="Demo Konferenz 2026",
            slug=EVENT_SINGLE,
            date_from=aware(self.today + timedelta(days=75), 9, 0),
            date_to=aware(self.today + timedelta(days=76), 17, 0),
            presale_start=aware(self.today - timedelta(days=60), 8, 0),
            presale_end=aware(self.today + timedelta(days=70), 23, 59),
            location="Alte Kongresshalle\nBavariapark 1\n80339 München",
            geo_lat=48.1310,
            geo_lon=11.5460,
            currency="EUR",
            is_public=True,
            live=True,
            plugins=PLUGINS,
            has_subevents=False,
        )
        self._event_settings(ev)
        self.log("event      {} (single date)".format(ev.slug))
        return ev

    def create_series_event(self, organizer):
        ev = Event.objects.create(
            organizer=organizer,
            name="Demo Workshop-Reihe",
            slug=EVENT_SERIES,
            date_from=aware(self.today + timedelta(days=30), 18, 0),
            currency="EUR",
            is_public=True,
            live=True,
            plugins=PLUGINS,
            has_subevents=True,
        )
        self._event_settings(ev)

        titles = [
            ("Workshop I: Grundlagen der Auswertung", 30, "VHS Musterstadt, Raum 1.02"),
            ("Workshop II: Filter und Bedingungen", 44, "VHS Musterstadt, Raum 1.02"),
            ("Workshop III: Exporte terminieren", 58, "VHS Musterstadt, Raum 2.11"),
            ("Workshop IV: Vorlagen und Wiederverwendung", 72, "Online (Videokonferenz)"),
            ("Workshop V: Sonderfälle und Datenlücken", 86, "VHS Musterstadt, Raum 1.02"),
        ]
        subevents = []
        for title, offset, loc in titles:
            se = ev.subevents.create(
                name=title,
                date_from=aware(self.today + timedelta(days=offset), 18, 0),
                date_to=aware(self.today + timedelta(days=offset), 21, 0),
                presale_start=aware(self.today - timedelta(days=30), 8, 0),
                location=loc,
                active=True,
                is_public=True,
            )
            subevents.append(se)
        self.log("event      {} (series with {} dates)".format(ev.slug, len(subevents)))
        return ev, subevents

    def create_tax_rules(self, event):
        return {
            19: event.tax_rules.create(name="MwSt. 19 %", rate=Decimal("19.00"),
                                       price_includes_tax=True, code="S/standard", default=True),
            7: event.tax_rules.create(name="MwSt. 7 %", rate=Decimal("7.00"),
                                      price_includes_tax=True, code="S/reduced"),
            0: event.tax_rules.create(name="Steuerfrei", rate=Decimal("0.00"),
                                      price_includes_tax=True, code="E"),
        }

    def create_products_single(self, event, tax):
        cat_tickets = event.categories.create(name="Tickets", position=1)
        cat_workshops = event.categories.create(name="Workshops", position=2)
        cat_merch = event.categories.create(name="Merchandise", position=3)
        cat_food = event.categories.create(name="Verpflegung", position=4)

        items = {}
        items["standard"] = event.items.create(
            name="Standardticket", category=cat_tickets, default_price=Decimal("89.00"),
            tax_rule=tax[19], admission=True, personalized=True, position=1, active=True,
            description="Zugang zu allen Vorträgen an beiden Tagen.",
        )
        items["reduced"] = event.items.create(
            name="Ermäßigtes Ticket", category=cat_tickets, default_price=Decimal("49.00"),
            tax_rule=tax[19], admission=True, personalized=True, position=2, active=True,
            description="Für Studierende, Auszubildende und Rentner:innen.",
        )
        # limited contingent
        items["early"] = event.items.create(
            name="Frühbucherticket", category=cat_tickets, default_price=Decimal("69.00"),
            tax_rule=tax[19], admission=True, personalized=True, position=3, active=True,
        )
        # workshop with small contingent
        items["workshop"] = event.items.create(
            name="Workshop: Reporting in der Praxis", category=cat_workshops,
            default_price=Decimal("39.00"), tax_rule=tax[19], admission=False,
            personalized=True, position=4, active=True,
        )
        # variations
        items["shirt"] = event.items.create(
            name="Konferenz-T-Shirt", category=cat_merch, default_price=Decimal("24.90"),
            tax_rule=tax[19], admission=False, personalized=False, position=5, active=True,
        )
        for pos, (val, price) in enumerate([
            ("S", "24.90"), ("M", "24.90"), ("L", "24.90"), ("XL", "26.90"), ("XXL", "28.90"),
        ], start=1):
            items["shirt"].variations.create(value=val, default_price=Decimal(price),
                                             position=pos, active=True)
        # reduced tax rate
        items["book"] = event.items.create(
            name="Tagungsband (gedruckt)", category=cat_merch, default_price=Decimal("19.90"),
            tax_rule=tax[7], admission=False, personalized=False, position=6, active=True,
        )
        # free product, tax free
        items["coffee"] = event.items.create(
            name="Kaffeegutschein (gratis)", category=cat_food, default_price=Decimal("0.00"),
            tax_rule=tax[0], admission=False, personalized=False, position=7, active=True,
        )

        q_all = event.quotas.create(name="Konferenz Gesamt", size=500)
        for k in ("standard", "reduced", "early"):
            q_all.items.add(items[k])
        q_early = event.quotas.create(name="Frühbucher (begrenzt)", size=25)
        q_early.items.add(items["early"])
        q_ws = event.quotas.create(name="Workshop (begrenzt)", size=15)
        q_ws.items.add(items["workshop"])
        q_shirt = event.quotas.create(name="T-Shirts", size=120)
        q_shirt.items.add(items["shirt"])
        for v in items["shirt"].variations.all():
            q_shirt.variations.add(v)
        q_rest = event.quotas.create(name="Merch & Verpflegung (unbegrenzt)", size=None)
        q_rest.items.add(items["book"])
        q_rest.items.add(items["coffee"])

        event.checkin_lists.create(name="Eingang Haupthalle", all_products=True)
        cl = event.checkin_lists.create(name="Nur Workshops", all_products=False)
        cl.limit_products.add(items["workshop"])

        self.log("products   {}: {} items, {} variations, {} quotas".format(
            event.slug, event.items.count(),
            sum(i.variations.count() for i in event.items.all()), event.quotas.count()))
        return items

    def create_products_series(self, event, tax, subevents):
        cat_tickets = event.categories.create(name="Workshop-Tickets", position=1)
        cat_material = event.categories.create(name="Material", position=2)

        items = {}
        items["ws"] = event.items.create(
            name="Workshop-Ticket", category=cat_tickets, default_price=Decimal("59.00"),
            tax_rule=tax[19], admission=True, personalized=True, position=1, active=True,
        )
        items["ws_red"] = event.items.create(
            name="Workshop-Ticket ermäßigt", category=cat_tickets,
            default_price=Decimal("35.00"), tax_rule=tax[19], admission=True,
            personalized=True, position=2, active=True,
        )
        items["material"] = event.items.create(
            name="Materialpaket", category=cat_material, default_price=Decimal("14.00"),
            tax_rule=tax[7], admission=False, personalized=False, position=3, active=True,
        )
        for pos, (val, price) in enumerate([("Digital", "0.00"), ("Gedruckt", "14.00")], start=1):
            items["material"].variations.create(value=val, default_price=Decimal(price),
                                                position=pos, active=True)
        items["snack"] = event.items.create(
            name="Abendsnack (gratis)", category=cat_material, default_price=Decimal("0.00"),
            tax_rule=tax[0], admission=False, personalized=False, position=4, active=True,
        )

        for i, se in enumerate(subevents):
            # one date deliberately has a very small contingent
            size = 8 if i == 2 else 30
            q = event.quotas.create(name="Plätze {}".format(se.date_from.date().isoformat()),
                                    size=size, subevent=se)
            q.items.add(items["ws"])
            q.items.add(items["ws_red"])
            qm = event.quotas.create(name="Material {}".format(se.date_from.date().isoformat()),
                                     size=None, subevent=se)
            qm.items.add(items["material"])
            qm.items.add(items["snack"])
            for v in items["material"].variations.all():
                qm.variations.add(v)
            # price override on one date
            if i == 3:
                se.subeventitem_set.create(item=items["ws"], price=Decimal("49.00"))

        event.checkin_lists.create(name="Einlass (alle Termine)", all_products=True)
        for se in subevents[:2]:
            event.checkin_lists.create(
                name="Einlass {}".format(se.date_from.date().isoformat()),
                all_products=True, subevent=se)

        self.log("products   {}: {} items, {} variations, {} quotas".format(
            event.slug, event.items.count(),
            sum(i.variations.count() for i in event.items.all()), event.quotas.count()))
        return items

    # -- questions ------------------------------------------------------- #

    def create_questions_single(self, event, items):
        qs = {}

        def mk(identifier, text, type, required=False, position=0, for_items=None, help_text=""):
            q = event.questions.create(
                question=text, type=type, required=required, identifier=identifier,
                position=position, help_text=help_text,
            )
            for it in (for_items if for_items is not None else []):
                q.items.add(it)
            qs[identifier] = q
            return q

        all_admission = [items["standard"], items["reduced"], items["early"]]

        mk("FIRMA", "Firma (für das Namensschild)", Question.TYPE_STRING,
           required=False, position=1, for_items=all_admission)
        mk("ANMERKUNG", "Anmerkungen zur Anmeldung", Question.TYPE_TEXT,
           required=False, position=2, for_items=all_admission + [items["workshop"]])
        mk("ALTER", "Alter", Question.TYPE_NUMBER,
           required=True, position=3, for_items=all_admission)

        q = mk("SHIRTGROESSE", "Gewünschte T-Shirt-Größe", Question.TYPE_CHOICE,
               required=True, position=4, for_items=[items["shirt"]])
        for ident, ans in [("SG_S", "S"), ("SG_M", "M"), ("SG_L", "L"),
                           ("SG_XL", "XL"), ("SG_XXL", "XXL")]:
            q.options.create(answer=ans, identifier=ident)

        q = mk("ESSEN", "Besondere Essenswünsche", Question.TYPE_CHOICE_MULTIPLE,
               required=False, position=5, for_items=all_admission)
        for ident, ans in [("ES_VEG", "vegetarisch"), ("ES_VGN", "vegan"),
                           ("ES_LAK", "laktosefrei"), ("ES_GLU", "glutenfrei"),
                           ("ES_HAL", "halal")]:
            q.options.create(answer=ans, identifier=ident)

        mk("ANREISE", "Tag der Anreise", Question.TYPE_DATE,
           required=False, position=6, for_items=all_admission)
        mk("ANKUNFTSZEIT", "Voraussichtliche Ankunftszeit", Question.TYPE_TIME,
           required=False, position=7, for_items=all_admission)
        mk("ABHOLUNG", "Wunschtermin Ticketabholung", Question.TYPE_DATETIME,
           required=False, position=8, for_items=[items["book"]])
        mk("NEWSLETTER", "Newsletter abonnieren", Question.TYPE_BOOLEAN,
           required=False, position=9, for_items=all_admission)
        mk("AGB", "Teilnahmebedingungen akzeptiert", Question.TYPE_BOOLEAN,
           required=True, position=10, for_items=all_admission)
        mk("AUSWEIS", "Nachweis für die Ermäßigung", Question.TYPE_FILE,
           required=False, position=11, for_items=[items["reduced"]])
        mk("HERKUNFTSLAND", "Herkunftsland", Question.TYPE_COUNTRYCODE,
           required=False, position=12, for_items=all_admission)
        mk("MOBIL", "Mobilnummer für Rückfragen", Question.TYPE_PHONENUMBER,
           required=False, position=13, for_items=all_admission)

        self.log("questions  {}: {} questions, {} distinct types".format(
            event.slug, len(qs), len({q.type for q in qs.values()})))
        return qs

    def create_questions_series(self, event, items):
        qs = {}
        tickets = [items["ws"], items["ws_red"]]

        q = event.questions.create(question="Vorkenntnisse", type=Question.TYPE_CHOICE,
                                   required=True, identifier="VORKENNTNISSE", position=1)
        for ident, ans in [("VK_KEINE", "keine"), ("VK_ETWAS", "etwas"),
                           ("VK_VIEL", "fortgeschritten")]:
            q.options.create(answer=ans, identifier=ident)
        for it in tickets:
            q.items.add(it)
        qs["VORKENNTNISSE"] = q

        q = event.questions.create(question="Nimmst du online teil?", type=Question.TYPE_BOOLEAN,
                                   required=False, identifier="ONLINE", position=2)
        for it in tickets:
            q.items.add(it)
        qs["ONLINE"] = q

        q = event.questions.create(question="Fragen im Vorfeld", type=Question.TYPE_TEXT,
                                   required=False, identifier="FRAGEN_VORAB", position=3)
        for it in tickets:
            q.items.add(it)
        qs["FRAGEN_VORAB"] = q

        q = event.questions.create(question="Anzahl Begleitpersonen", type=Question.TYPE_NUMBER,
                                   required=False, identifier="BEGLEITUNG", position=4)
        for it in tickets:
            q.items.add(it)
        qs["BEGLEITUNG"] = q

        self.log("questions  {}: {} questions".format(event.slug, len(qs)))
        return qs

    # -- vouchers -------------------------------------------------------- #

    def create_vouchers(self, event, items, subevent=None):
        vouchers = []
        specs = [
            ("PRESSE2026", "presse", "set", Decimal("0.00"), 5),
            ("PARTNER10", "partner", "subtract", Decimal("10.00"), 20),
            ("TREUERABATT", "treue", "percent", Decimal("15.00"), 20),
            ("SPEAKERFREI", "speaker", "set", Decimal("0.00"), 3),
        ]
        first_item = next(iter(items.values()))
        for code, tag, mode, value, usages in specs:
            vouchers.append(Voucher.objects.create(
                event=event, code="{}-{}".format(code, event.slug.upper()[:4]),
                max_usages=usages, price_mode=mode, value=value, tag=tag,
                valid_until=aware(self.today + timedelta(days=60), 23, 59),
                item=first_item, subevent=subevent,
                comment="Vom Seed-Skript angelegt",
            ))
        self.log("vouchers   {}: {}".format(event.slug, len(vouchers)))
        return vouchers

    # -- answers --------------------------------------------------------- #

    def answer_questions(self, position, item_questions):
        """Answer a random subset. Deliberately leaves gaps, including required ones."""
        rng = self.rng
        created = 0
        for q in item_questions:
            # 22 % of the answers are missing on purpose
            if rng.random() < 0.22:
                continue
            answer = None
            options = []
            file_content = None

            if q.type == Question.TYPE_STRING:
                answer = rng.choice([c[0] for c in COMPANIES] + ["", "freiberuflich"])
                if not answer:
                    continue
            elif q.type == Question.TYPE_TEXT:
                answer = rng.choice(FREE_TEXTS)
            elif q.type == Question.TYPE_NUMBER:
                answer = str(rng.randint(16, 78))
            elif q.type == Question.TYPE_BOOLEAN:
                answer = "True" if rng.random() < 0.55 else "False"
            elif q.type == Question.TYPE_CHOICE:
                opts = list(q.options.all())
                if not opts:
                    continue
                chosen = rng.choice(opts)
                options = [chosen]
                answer = str(chosen.answer)
            elif q.type == Question.TYPE_CHOICE_MULTIPLE:
                opts = list(q.options.all())
                if not opts:
                    continue
                n = rng.randint(1, min(3, len(opts)))
                options = rng.sample(opts, n)
                answer = ", ".join(str(o.answer) for o in options)
            elif q.type == Question.TYPE_DATE:
                answer = (self.today + timedelta(days=rng.randint(60, 80))).isoformat()
            elif q.type == Question.TYPE_TIME:
                answer = "{:02d}:{:02d}:00".format(rng.randint(7, 21), rng.choice([0, 15, 30, 45]))
            elif q.type == Question.TYPE_DATETIME:
                d = self.today + timedelta(days=rng.randint(60, 80))
                answer = "{}T{:02d}:00:00+02:00".format(d.isoformat(), rng.randint(9, 18))
            elif q.type == Question.TYPE_COUNTRYCODE:
                answer = rng.choice(["DE", "DE", "DE", "AT", "CH", "NL", "PL"])
            elif q.type == Question.TYPE_PHONENUMBER:
                answer = "+49 151 {}".format(rng.randint(1000000, 9999999))
            elif q.type == Question.TYPE_FILE:
                fname = "nachweis-{}.txt".format(rng.randint(1000, 9999))
                file_content = (fname, "Ermäßigungsnachweis (Demo-Datei)\n")
                answer = fname
            else:
                continue

            qa = QuestionAnswer(question=q, orderposition=position, answer=answer or "")
            if file_content:
                qa.file.save(file_content[0], ContentFile(file_content[1].encode()), save=False)
            qa.save()
            if options:
                qa.options.add(*options)
            created += 1
        return created

    # -- orders ---------------------------------------------------------- #

    def _pick_products(self, items, big_order):
        """Return a list of (item, variation, price) tuples."""
        rng = self.rng
        pool = []
        for it in items.values():
            variations = list(it.variations.all())
            weight = 4 if it.admission else 2
            if it.default_price == Decimal("0.00"):
                weight = 1
            for _ in range(weight):
                pool.append((it, variations))

        n = rng.randint(6, 12) if big_order else rng.choices([1, 2, 3, 4], [55, 25, 13, 7])[0]
        out = []
        for _ in range(n):
            it, variations = rng.choice(pool)
            var = rng.choice(variations) if variations else None
            price = var.default_price if (var and var.default_price is not None) else it.default_price
            out.append((it, var, Decimal(price)))
        return out

    def create_orders(self, event, items, questions, vouchers, subevents, n):
        rng = self.rng
        channel = event.organizer.sales_channels.get(identifier="web")
        checkin_lists = list(event.checkin_lists.filter(subevent__isnull=True))
        questions_by_item = {}
        for q in questions.values():
            for it in q.items.all():
                questions_by_item.setdefault(it.pk, []).append(q)

        # status mix: pending / paid / expired / canceled (+ refund and partial payment flavours)
        plan = (
            ["paid"] * int(n * 0.44)
            + ["paid_partial"] * max(1, int(n * 0.05))
            + ["paid_refunded_partly"] * max(1, int(n * 0.04))
            + ["pending"] * int(n * 0.17)
            + ["expired"] * int(n * 0.09)
            + ["canceled"] * int(n * 0.11)
            + ["canceled_refunded"] * max(1, int(n * 0.07))
            + ["pending_approval"] * max(1, int(n * 0.03))
        )
        while len(plan) < n:
            plan.append("paid")
        plan = plan[:n]
        rng.shuffle(plan)

        counters = {}
        invoices_generated = 0
        answers_total = 0
        checkins_total = 0

        for idx, kind in enumerate(plan):
            with transaction.atomic():
                counters[kind] = counters.get(kind, 0) + 1
                age_days = rng.randint(1, 55)
                created = aware(self.today - timedelta(days=age_days),
                                rng.randint(8, 22), rng.choice([3, 17, 29, 41, 58]))
                parts = self.name_parts()
                big = rng.random() < 0.06
                products = self._pick_products(items, big)
                subevent = rng.choice(subevents) if subevents else None

                status = {
                    "paid": Order.STATUS_PAID,
                    "paid_partial": Order.STATUS_PENDING,
                    "paid_refunded_partly": Order.STATUS_PAID,
                    "pending": Order.STATUS_PENDING,
                    "pending_approval": Order.STATUS_PENDING,
                    "expired": Order.STATUS_EXPIRED,
                    "canceled": Order.STATUS_CANCELED,
                    "canceled_refunded": Order.STATUS_CANCELED,
                }[kind]

                fee = Decimal("0.00")
                add_fee = rng.random() < 0.18
                if add_fee:
                    fee = Decimal("2.50")
                total = sum((p for _, _, p in products), Decimal("0.00")) + fee

                order = Order(
                    event=event,
                    status=status,
                    email=None if rng.random() < 0.04 else self.email_for(parts),
                    locale="de" if rng.random() < 0.85 else "en",
                    datetime=created,
                    expires=created + timedelta(days=14),
                    total=total,
                    sales_channel=channel,
                    require_approval=(kind == "pending_approval"),
                    testmode=rng.random() < 0.025,
                    comment=rng.choice(COMMENTS),
                    checkin_attention=rng.random() < 0.05,
                    custom_followup_at=(self.today + timedelta(days=rng.randint(1, 40))
                                        if rng.random() < 0.05 else None),
                )
                if status == Order.STATUS_CANCELED:
                    order.cancellation_date = created + timedelta(days=rng.randint(1, 10))
                order.save()

                # ---- invoice address: 34 % have none at all
                r = rng.random()
                if r >= 0.34:
                    business = r < 0.60
                    street, zipcode, city, country = rng.choice(ADDRESSES)
                    company, vat = rng.choice(COMPANIES) if business else ("", "")
                    InvoiceAddress.objects.create(
                        order=order,
                        is_business=business,
                        company=company,
                        name_parts=parts,
                        street=street,
                        zipcode=zipcode,
                        city=city,
                        country=country,
                        vat_id=vat if business and rng.random() < 0.7 else "",
                        vat_id_validated=bool(vat) and business and rng.random() < 0.5,
                        internal_reference=("Kostenstelle {}".format(rng.randint(1000, 9999))
                                            if business and rng.random() < 0.4 else ""),
                    )

                # ---- positions
                voucher = rng.choice(vouchers) if (vouchers and rng.random() < 0.17) else None
                for pos_id, (item, variation, price) in enumerate(products, start=1):
                    op_voucher = voucher if (voucher and pos_id == 1 and voucher.item_id == item.pk) else None
                    op = OrderPosition(
                        order=order,
                        positionid=pos_id,
                        item=item,
                        variation=variation,
                        subevent=subevent,
                        price=price,
                        voucher=op_voucher,
                        attendee_name_parts=self.name_parts() if item.personalized else {},
                        attendee_email=(self.email_for(parts) if item.personalized and rng.random() < 0.4
                                        else None),
                        company=(rng.choice(COMPANIES)[0] if rng.random() < 0.12 else None),
                    )
                    op.save()
                    if op_voucher:
                        voucher.redeemed = (voucher.redeemed or 0) + 1
                        voucher.save(update_fields=["redeemed"])

                    # ---- answers (some positions get none at all)
                    item_qs = questions_by_item.get(item.pk, [])
                    if item_qs and rng.random() < 0.78:
                        answers_total += self.answer_questions(op, item_qs)

                    # ---- check-ins for paid orders
                    if (status == Order.STATUS_PAID and checkin_lists and item.admission
                            and rng.random() < 0.45):
                        cl = rng.choice(checkin_lists)
                        Checkin.objects.create(
                            position=op, list=cl,
                            datetime=aware(self.today - timedelta(days=rng.randint(0, 3)),
                                           rng.randint(8, 19), rng.randint(0, 59)),
                            type=Checkin.TYPE_ENTRY,
                            auto_checked_in=rng.random() < 0.1,
                        )
                        checkins_total += 1

                if add_fee:
                    OrderFee.objects.create(
                        order=order, fee_type=OrderFee.FEE_TYPE_PAYMENT, value=fee,
                        description="Zahlungsgebühr", tax_rule=None,
                    )

                # ---- payments / refunds
                provider = "free" if total == Decimal("0.00") else rng.choice(["banktransfer", "manual"])
                if kind in ("paid", "paid_refunded_partly", "canceled_refunded"):
                    OrderPayment.objects.create(
                        order=order, provider=provider, amount=total,
                        state=OrderPayment.PAYMENT_STATE_CONFIRMED,
                        payment_date=created + timedelta(days=rng.randint(0, 6)),
                    )
                elif kind == "paid_partial":
                    half = (total / 2).quantize(Decimal("0.01"))
                    OrderPayment.objects.create(
                        order=order, provider="banktransfer", amount=half,
                        state=OrderPayment.PAYMENT_STATE_CONFIRMED,
                        payment_date=created + timedelta(days=rng.randint(1, 8)),
                    )
                    OrderPayment.objects.create(
                        order=order, provider="banktransfer", amount=total - half,
                        state=OrderPayment.PAYMENT_STATE_PENDING,
                    )
                elif kind == "pending":
                    if rng.random() < 0.5:
                        OrderPayment.objects.create(
                            order=order, provider="banktransfer", amount=total,
                            state=OrderPayment.PAYMENT_STATE_PENDING,
                        )
                elif kind == "expired":
                    if rng.random() < 0.3:
                        OrderPayment.objects.create(
                            order=order, provider="banktransfer", amount=total,
                            state=OrderPayment.PAYMENT_STATE_FAILED,
                        )

                if kind == "canceled_refunded" and total > 0:
                    OrderRefund.objects.create(
                        order=order, provider=provider, amount=total,
                        state=OrderRefund.REFUND_STATE_DONE,
                        source=OrderRefund.REFUND_SOURCE_ADMIN,
                        execution_date=created + timedelta(days=rng.randint(7, 20)),
                    )
                elif kind == "paid_refunded_partly" and total > Decimal("10.00"):
                    OrderRefund.objects.create(
                        order=order, provider=provider,
                        amount=(total / 4).quantize(Decimal("0.01")),
                        state=OrderRefund.REFUND_STATE_DONE,
                        source=OrderRefund.REFUND_SOURCE_BUYER,
                        execution_date=created + timedelta(days=rng.randint(7, 20)),
                    )

                order.create_transactions()

                # ---- invoices for a slice of the paid orders (PDF rendering is slow)
                if (status == Order.STATUS_PAID and invoices_generated < min(30, max(5, n // 6))
                        and hasattr(order, "invoice_address")):
                    from pretix.base.services.invoices import generate_invoice
                    try:
                        generate_invoice(order)
                        invoices_generated += 1
                    except Exception as e:  # never fail the whole seed because of an invoice
                        self.log("  ! invoice for {} failed: {}".format(order.code, e))

                if not self.quiet and (idx + 1) % 25 == 0:
                    print("  ... {}/{} orders".format(idx + 1, len(plan)), flush=True)

        self.log("orders     {}: {} orders, {} answers, {} check-ins, {} invoices".format(
            event.slug, len(plan), answers_total, checkins_total, invoices_generated))
        self.log("           mix: {}".format(
            ", ".join("{}={}".format(k, v) for k, v in sorted(counters.items()))))


# --------------------------------------------------------------------------- #
# reset
# --------------------------------------------------------------------------- #

def purge(quiet=False):
    """Remove the demo organizer and everything below it, in FK-safe order."""
    try:
        organizer = Organizer.objects.get(slug=ORGANIZER_SLUG)
    except Organizer.DoesNotExist:
        return False

    for event in list(organizer.events.all()):
        Checkin.all.filter(list__event=event).delete()
        QuestionAnswer.objects.filter(orderposition__order__event=event).delete()
        Transaction.objects.filter(order__event=event).delete()
        InvoiceLine.objects.filter(invoice__event=event).delete()
        Invoice.objects.filter(event=event).delete()
        OrderPosition.all.filter(order__event=event).update(addon_to=None)
        OrderPosition.all.filter(order__event=event).delete()
        OrderFee.all.filter(order__event=event).delete()
        OrderRefund.objects.filter(order__event=event).delete()
        OrderPayment.objects.filter(order__event=event).delete()
        event.orders.all().delete()
        event.checkin_lists.all().delete()
        event.questions.all().delete()
        event.quotas.all().delete()
        event.delete_sub_objects()          # carts, vouchers, items, subevents
        event.categories.all().delete()
        event.tax_rules.all().delete()
        event.delete()
        if not quiet:
            print("purged     event {}".format(event.slug), flush=True)

    Invoice.objects.filter(organizer=organizer).delete()
    organizer.delete()
    if not quiet:
        print("purged     organizer {}".format(ORGANIZER_SLUG), flush=True)
    return True


# --------------------------------------------------------------------------- #
# summary
# --------------------------------------------------------------------------- #

def summary():
    try:
        organizer = Organizer.objects.get(slug=ORGANIZER_SLUG)
    except Organizer.DoesNotExist:
        print("no organizer '{}' -- run: python scripts/seed_demo.py --reset".format(ORGANIZER_SLUG))
        return 1
    print("organizer: {} ({})".format(organizer.name, organizer.slug))
    for team in organizer.teams.all():
        print("  team {} -- all_events={} members={}".format(
            team.name, team.all_events,
            ", ".join(u.email for u in team.members.all()) or "(none)"))
    for event in organizer.events.all().order_by("slug"):
        orders = Order.objects.filter(event=event)
        print("  event {}  series={}".format(event.slug, event.has_subevents))
        print("    subevents  {}".format(event.subevents.count()))
        print("    items      {} (+{} variations) in {} categories".format(
            event.items.count(),
            sum(i.variations.count() for i in event.items.all()),
            event.categories.count()))
        print("    quotas     {}".format(event.quotas.count()))
        print("    questions  {}  types={}".format(
            event.questions.count(),
            ",".join(sorted({q.type for q in event.questions.all()}))))
        print("    vouchers   {}".format(event.vouchers.count()))
        print("    orders     {} total".format(orders.count()))
        for st, label in (("n", "pending"), ("p", "paid"), ("e", "expired"), ("c", "canceled")):
            print("      {:<9} {}".format(label, orders.filter(status=st).count()))
        print("      positions    {}".format(
            OrderPosition.objects.filter(order__event=event).count()))
        print("      answers      {}".format(
            QuestionAnswer.objects.filter(orderposition__order__event=event).count()))
        print("      no inv.addr  {}".format(orders.filter(invoice_address__isnull=True).count()))
        print("      check-ins    {}".format(
            Checkin.objects.filter(position__order__event=event).count()))
        print("      payments {} / refunds {}".format(
            OrderPayment.objects.filter(order__event=event).count(),
            OrderRefund.objects.filter(order__event=event).count()))
        print("      invoices     {}".format(Invoice.objects.filter(event=event).count()))
    return 0


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main():
    p = argparse.ArgumentParser(description="Create demo data for pretix-custom-reports")
    p.add_argument("--reset", action="store_true",
                   help="delete the '{}' organizer with all events first".format(ORGANIZER_SLUG))
    p.add_argument("--orders", type=int, default=DEFAULT_ORDERS,
                   help="total number of orders across both events (default {})".format(DEFAULT_ORDERS))
    p.add_argument("--seed", type=int, default=DEFAULT_SEED,
                   help="random seed (default {})".format(DEFAULT_SEED))
    p.add_argument("--list", action="store_true", help="only print a summary of existing demo data")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    with scopes_disabled():
        if args.list:
            return summary()

        if Organizer.objects.filter(slug=ORGANIZER_SLUG).exists() and not args.reset:
            print("Organizer '{}' already exists. Use --reset to recreate it.".format(ORGANIZER_SLUG))
            return 1

        if args.reset:
            purge(quiet=args.quiet)

        rng = random.Random(args.seed)
        seeder = Seeder(rng, args.orders, quiet=args.quiet)
        if not args.quiet:
            print("seeding with seed={} orders={}".format(args.seed, args.orders), flush=True)

        organizer = seeder.create_organizer()

        # --- single date event
        ev1 = seeder.create_single_event(organizer)
        tax1 = seeder.create_tax_rules(ev1)
        items1 = seeder.create_products_single(ev1, tax1)
        q1 = seeder.create_questions_single(ev1, items1)
        v1 = seeder.create_vouchers(ev1, items1)

        # --- event series
        ev2, subevents = seeder.create_series_event(organizer)
        tax2 = seeder.create_tax_rules(ev2)
        items2 = seeder.create_products_series(ev2, tax2, subevents)
        q2 = seeder.create_questions_series(ev2, items2)
        v2 = seeder.create_vouchers(ev2, items2, subevent=subevents[0])

        n1 = int(args.orders * 0.65)
        n2 = args.orders - n1
        seeder.create_orders(ev1, items1, q1, v1, None, n1)
        seeder.create_orders(ev2, items2, q2, v2, subevents, n2)

        print(flush=True)
        return summary()


if __name__ == "__main__":
    sys.exit(main())
