"""HTTP-level load test: real concurrent traffic against the running app.

This complements `tests/test_concurrency.py`, which already proves the booking
invariants hold at the database/service layer (unique constraints, clean
refusals, no double bookings) using in-process threads on independent
connections. What that suite does NOT exercise is the actual web stack: real
HTTP, gunicorn's worker/thread pool, Django sessions, CSRF, and the database
connection pool under concurrent load from many real browsers.

That's what this file is for. It drives the app exactly the way a browser
would: login form -> room board -> book_slot confirmation page ->
confirm_booking POST, with real cookies and a real CSRF token pulled out of
each response.

Setup (once, against a local/staging stack — never production):

    docker compose up -d
    docker compose exec web python manage.py migrate
    docker compose exec web python manage.py createcachetable
    docker compose exec web python manage.py seed_demo --students 100

This creates 100 synthetic students: institutional_id 660000001..660000100
(the same ``66{index:07d}`` format as core/management/commands/seed_demo.py),
password "demo-student-password-1".

The room board only renders reserve links for the selected hour, and the
default hour is the current one — usually "use now", not an advance booking.
Each user therefore opens a future hour (at least two hours ahead, inside the
booking horizon) and posts that cell.

Run — realistic mixed load, 100 concurrent users each browsing and booking a
room of their own choosing:

    locust -f loadtest/locustfile.py --host http://127.0.0.1:8000 \
        -u 100 -r 10 --run-time 5m --headless \
        --csv loadtest/results/mixed

Run — the race scenario: every simulated user targets the SAME room+slot at
once, to check for double-booking / non-500 refusals under real HTTP:

    HOT_SLOT=1 locust -f loadtest/locustfile.py --host http://127.0.0.1:8000 \
        -u 50 -r 50 --run-time 30s --headless \
        --csv loadtest/results/hotslot

A 302 whose Location is my-bookings counts as booked. A 409 counts as a clean
refusal (SLOT_TAKEN and the other refusal codes the confirm view renders).
Anything else — including a 302 to the login page or the grid — is a failure.

Then verify at most one booking was persisted for that slot (see
loadtest/README.md for the verification query).

Or drop --headless and open http://localhost:8089 for the interactive web UI.
"""

from __future__ import annotations

import itertools
import os
import re
import threading
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from locust import HttpUser, between, constant, events, task
from locust.exception import StopUser

LANG = "en"
BANGKOK = ZoneInfo("Asia/Bangkok")
# Stay off the hour that is about to start so a five-minute run cannot turn a
# future reservation into STALE_HOUR (the confirm view renders that as 200).
SLOT_LEAD = timedelta(hours=2)

CSRF_RE = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')
OPERATION_KEY_RE = re.compile(r'name="operation_key" value="([^"]*)"')
# Slot identifiers are YYYYMMDDTHHMM. The confirm URL shares that prefix, so
# the lookahead keeps the reservation link and drops the form action.
BOOK_LINK_RE = re.compile(rf"/{LANG}/book/(\d+)/(\d{{8}}T\d{{4}})/(?!confirm)")
BOARD_HREF_RE = re.compile(r'href="\?date=(\d{4}-\d{2}-\d{2})&(?:amp;)?time=(\d+)"')

DEMO_STUDENT_COUNT = 100
DEMO_PASSWORD = "demo-student-password-1"
HOT_SLOT = os.environ.get("HOT_SLOT", "").strip().lower() in {"1", "true", "yes"}

# Thread-safe round-robin so N locust users spread across the 100 seeded
# students instead of everyone logging in as student 1. Raise
# DEMO_STUDENT_COUNT (and re-run seed_demo --students <n>) to test with a
# bigger roster.
_student_ids = itertools.cycle(f"66{index:07d}" for index in range(1, DEMO_STUDENT_COUNT + 1))
_student_ids_lock = threading.Lock()

# Open (room_id, slot_key) pairs discovered once from the room board.
_open_slots: list[tuple[str, str]] | None = None
_open_slots_lock = threading.Lock()

# One cell, resolved once, when HOT_SLOT is set.
_hot_target: tuple[str, str] | None = None
_hot_target_lock = threading.Lock()

# Booking POST outcomes, independent of Locust's failure counter (409 is healthy).
_booking_status = Counter()
_booking_status_lock = threading.Lock()


def _next_student_id() -> str:
    with _student_ids_lock:
        return next(_student_ids)


def _slot_local(slot_key: str) -> datetime:
    return datetime.strptime(slot_key, "%Y%m%dT%H%M").replace(tzinfo=BANGKOK)


def _combo_is_lead(date_text: str, hour_text: str, moment: datetime) -> bool:
    hour = int(hour_text)
    local = datetime.strptime(date_text, "%Y-%m-%d").replace(
        hour=hour, minute=0, second=0, microsecond=0, tzinfo=BANGKOK
    )
    return local >= moment + SLOT_LEAD


def _record_booking_status(status_code: int) -> None:
    with _booking_status_lock:
        _booking_status[status_code] += 1


class Student(HttpUser):
    """One simulated student: log in, browse, try to book a room."""

    # The race run wants the POSTs to overlap. The mixed run pauses like a person.
    wait_time = constant(0) if HOT_SLOT else between(1, 3)

    def on_start(self):
        self.institutional_id = _next_student_id()
        self.attempt = 0
        # Same-origin Referer so Django's CSRF check accepts the POSTs.
        self.client.headers["Referer"] = f"{self.host}/{LANG}/"
        if not self._login():
            raise StopUser()

    def _extract(self, pattern: re.Pattern, text: str) -> str | None:
        match = pattern.search(text or "")
        return match.group(1) if match else None

    def _login(self) -> bool:
        with self.client.get(
            f"/{LANG}/login/", name="/login [GET]", catch_response=True
        ) as page:
            if page.status_code != 200:
                page.failure(f"login page status {page.status_code}")
                return False
            csrf = self._extract(CSRF_RE, page.text)
            if not csrf:
                page.failure("login page has no csrfmiddlewaretoken")
                return False
            page.success()

        with self.client.post(
            f"/{LANG}/login/",
            data={
                "csrfmiddlewaretoken": csrf,
                "institutional_id": self.institutional_id,
                "password": DEMO_PASSWORD,
            },
            headers={"Referer": f"{self.host}/{LANG}/login/"},
            name="/login [POST]",
            catch_response=True,
        ) as resp:
            location = resp.headers.get("Location", "")
            landed = "my-bookings" in (resp.url or "") or "my-bookings" in location
            if landed and resp.status_code in (200, 301, 302):
                resp.success()
                return True
            resp.failure(f"login failed for {self.institutional_id}: {resp.status_code}")
            return False

    def _discover_open_slots(self, *, stop_when_found: bool = False) -> list[tuple[str, str]]:
        """Walk future room-board hours and collect real reserve links.

        The board is one hour per response. Hours are requested in time order.
        The race run stops at the first hour that still has a reserve link so
        the 30 second window is spent posting, not scanning the horizon.
        """
        moment = datetime.now(BANGKOK)
        found: list[tuple[str, str]] = []
        seen_slots: set[tuple[str, str]] = set()
        queued: set[tuple[str, str]] = set()
        queue: list[tuple[str, str]] = []

        def absorb(text: str) -> None:
            for room_id, slot_key in BOOK_LINK_RE.findall(text or ""):
                if not _combo_is_lead(
                    f"{slot_key[0:4]}-{slot_key[4:6]}-{slot_key[6:8]}",
                    str(int(slot_key[9:11])),
                    moment,
                ):
                    continue
                key = (room_id, slot_key)
                if key not in seen_slots:
                    seen_slots.add(key)
                    found.append(key)
            for date_text, hour_text in BOARD_HREF_RE.findall(text or ""):
                if not _combo_is_lead(date_text, hour_text, moment):
                    continue
                item = (date_text, f"{int(hour_text):02d}")
                if item not in queued:
                    queued.add(item)
                    queue.append(item)

        seen_pages: set[tuple[str, str]] = set()
        first = self.client.get(f"/{LANG}/choose-room/", name="/choose-room/ [GET]")
        absorb(first.text)

        fetched = 0
        while queue and fetched < 120:
            queue.sort()
            date_text, hour_text = queue.pop(0)
            page_key = (date_text, hour_text)
            if page_key in seen_pages:
                continue
            seen_pages.add(page_key)
            page = self.client.get(
                f"/{LANG}/choose-room/?date={date_text}&time={int(hour_text)}",
                name="/choose-room/ [GET]",
            )
            absorb(page.text)
            fetched += 1
            if not (stop_when_found and found):
                continue
            # The default board links to "the same hour, next day", so the first
            # page fetched can be 18:00 even when 08:00 is still free. Keep
            # going while an earlier hour is queued.
            earliest_slot = min(slot_key for _, slot_key in found)
            if not queue:
                break
            qdate, qhour = min(queue)
            qkey = f"{qdate.replace('-', '')}T{qhour}00"
            if qkey >= earliest_slot:
                break

        found.sort(key=lambda item: (item[1], int(item[0])))
        return found

    def _ensure_open_slots(self) -> list[tuple[str, str]]:
        global _open_slots
        if _open_slots:
            return _open_slots
        with _open_slots_lock:
            if not _open_slots:
                discovered = self._discover_open_slots()
                if discovered:
                    _open_slots = discovered
            return list(_open_slots or [])

    def _ensure_hot_target(self) -> tuple[str, str] | None:
        global _hot_target
        if _hot_target is not None:
            return _hot_target
        with _hot_target_lock:
            if _hot_target is None:
                slots = self._discover_open_slots(stop_when_found=True)
                if slots:
                    _hot_target = slots[0]
                    print(
                        f"hot_target room={_hot_target[0]} slot={_hot_target[1]}",
                        flush=True,
                    )
            return _hot_target

    def _assigned_slot(self) -> tuple[str, str] | None:
        slots = self._ensure_open_slots()
        if not slots:
            return None
        # 660000001 -> 1. Users start on different cells, then walk forward by
        # the whole roster so the next try is a different hour, not the
        # adjacent one (adjacency is rejected with 409).
        ordinal = int(self.institutional_id[-3:])
        index = (ordinal - 1 + self.attempt * DEMO_STUDENT_COUNT) % len(slots)
        return slots[index]

    def _book(self, room_id: str, slot_key: str, label: str):
        """GET the confirmation page, then POST it — exactly like a real click."""
        confirm_path = f"/{LANG}/book/{room_id}/{slot_key}/"
        with self.client.get(
            confirm_path,
            name=f"/book/[id]/[slot]/ [GET] {label}",
            catch_response=True,
            allow_redirects=False,
        ) as confirm_page:
            if confirm_page.status_code == 302:
                # Instrument mismatch and closed rooms redirect to the grid.
                # That is a refusal, not a broken page. Do not follow it: the
                # grid is a 200 with no booking form, which looks like a missing
                # token if the redirect is swallowed.
                confirm_page.success()
                return
            if confirm_page.status_code != 200:
                confirm_page.failure(f"confirm page status {confirm_page.status_code}")
                return
            csrf = self._extract(CSRF_RE, confirm_page.text)
            operation_key = self._extract(OPERATION_KEY_RE, confirm_page.text)
            if not csrf or not operation_key:
                confirm_page.failure("confirm page missing csrf or operation_key")
                return
            confirm_page.success()

        with self.client.post(
            f"{confirm_path}confirm/",
            data={
                "csrfmiddlewaretoken": csrf,
                "operation_key": operation_key,
                "title": "Locust load test",
                "purpose": "Automated stress test booking",
                "participant_names": "",
            },
            headers={"Referer": f"{self.host}{confirm_path}"},
            name=f"/book/[id]/[slot]/confirm/ [POST] {label}",
            catch_response=True,
            allow_redirects=False,
        ) as resp:
            _record_booking_status(resp.status_code)
            location = resp.headers.get("Location", "")
            if resp.status_code == 302 and "my-bookings" in location:
                resp.success()
            elif resp.status_code == 409:
                resp.success()
            else:
                snippet = " ".join((resp.text or "").split())[:160]
                resp.failure(
                    f"unexpected status {resp.status_code} booking {room_id}/{slot_key}: {snippet}"
                )

    @task(5)
    def browse_and_book_open_slot(self):
        """Realistic path: look at a future hour on the board, book one open cell."""
        if HOT_SLOT:
            return
        target = self._assigned_slot()
        if target is None:
            self.client.get(f"/{LANG}/choose-room/", name="/choose-room/ [GET]")
            return
        room_id, slot_key = target
        date_text = f"{slot_key[0:4]}-{slot_key[4:6]}-{slot_key[6:8]}"
        hour = int(slot_key[9:11])
        self.client.get(
            f"/{LANG}/choose-room/?date={date_text}&time={hour}",
            name="/choose-room/ [GET]",
        )
        self._book(room_id, slot_key, label="open-slot")
        self.attempt += 1

    @task(1)
    def view_my_bookings(self):
        if HOT_SLOT:
            return
        self.client.get(f"/{LANG}/my-bookings/", name="/my-bookings/ [GET]")

    @task(1)
    def race_for_hot_slot(self):
        """With HOT_SLOT=1 every user fights for the same cell. Otherwise a no-op."""
        if not HOT_SLOT:
            return
        target = self._ensure_hot_target()
        if target is None:
            return
        room_id, slot_key = target
        self._book(room_id, slot_key, label="hot-slot")


# @task records every method. Replace that set so the race run does not also
# browse, and the mixed run does not spend turns on an empty race task.
if HOT_SLOT:
    Student.tasks = [Student.race_for_hot_slot]
else:
    Student.tasks = {
        Student.browse_and_book_open_slot: 5,
        Student.view_my_bookings: 1,
    }


@events.quitting.add_listener
def _summary(environment, **kwargs):
    stats = environment.stats.total
    total = stats.num_requests or 0
    rate = (stats.num_failures / total) if total else 0.0
    print(
        f"\nrequests={total} failures={stats.num_failures} "
        f"failure_rate={rate:.4f} "
        f"median_ms={stats.median_response_time:.0f} "
        f"p95_ms={stats.get_response_time_percentile(0.95) or 0:.0f} "
        f"p99_ms={stats.get_response_time_percentile(0.99) or 0:.0f}"
    )
    print("booking_post_status=" + ", ".join(
        f"{code}:{count}" for code, count in sorted(_booking_status.items())
    ) or "booking_post_status=none")
    for entry in environment.stats.entries.values():
        if "confirm/" not in entry.name:
            continue
        print(
            f"endpoint={entry.name} requests={entry.num_requests} "
            f"failures={entry.num_failures} "
            f"median_ms={entry.median_response_time:.0f} "
            f"p95_ms={entry.get_response_time_percentile(0.95) or 0:.0f} "
            f"p99_ms={entry.get_response_time_percentile(0.99) or 0:.0f}"
        )
