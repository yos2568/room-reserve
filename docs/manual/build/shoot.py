import os, subprocess, sys, time, urllib.request
from playwright.sync_api import sync_playwright

ROOT = "/Volumes/Crucial2TB/All Codes/FAA/Room problem"
OUT = sys.argv[1]
PW = "manual-demo-pass-1"
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"

def server(frozen):
    env = dict(os.environ, DJANGO_SETTINGS_MODULE="roomreserve.settings.dev", POSTGRES_DB="roomreserve_manual",
               ALLOW_TEST_CLOCK="true", FROZEN_CLOCK=frozen, DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost", SUPPORT_CONTACT_EMAIL="superuser@yos.in.th")
    p = subprocess.Popen([os.path.expanduser("~/.virtualenvs/roomreserve/bin/python"), "manage.py", "runserver",
                          f"127.0.0.1:{PORT}", "--noreload"], cwd=ROOT, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/healthz/", timeout=1); return p
        except Exception:
            time.sleep(0.5)
    p.kill(); raise SystemExit("server did not start")

MOBILE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, locale="th-TH")
DESKTOP = dict(viewport={"width": 1280, "height": 860}, device_scale_factor=2, locale="th-TH")

def ctx(browser, kind, user=None):
    c = browser.new_context(**(MOBILE if kind == "m" else DESKTOP))
    pg = c.new_page()
    if user:
        pg.goto(BASE + "/th/login/")
        pg.fill("input[name=institutional_id]", user)
        pg.fill("input[name=password]", PW)
        pg.click("form button[type=submit] >> nth=-1") if False else pg.locator("main form button[type=submit]").first.click()
        pg.wait_for_load_state("networkidle")
    return c, pg

def shot(pg, path, name, full=False, cap=None):
    pg.goto(BASE + path); pg.wait_for_load_state("networkidle")
    width = pg.viewport_size["width"]
    bottom = pg.evaluate("() => { const m = document.querySelector('main'); const r = m.getBoundingClientRect(); return Math.ceil(r.bottom + window.scrollY + 8); }")
    cap = cap or (1500 if width < 500 else 1100)
    height = min(bottom, cap)
    pg.screenshot(path=f"{OUT}/{name}.png", full_page=True, clip={"x": 0, "y": 0, "width": width, "height": height})
    print("saved", name, height)

def session_a(b):
    c, pg = ctx(b, "m")
    shot(pg, "/th/", "m_home")
    shot(pg, "/th/choose-room/?date=2026-09-29&time=15", "m_choose_room")
    shot(pg, "/th/register/", "m_register", full=True)
    shot(pg, "/th/login/", "m_login")
    c.close()
    c, pg = ctx(b, "m", "660000001")
    shot(pg, "/th/r/1/", "m_door_ready")
    shot(pg, "/th/my-bookings/", "m_my_bookings", full=True)
    c.close()
    for user, room, name in [("660000002", 2, "m_door_not_open"), ("660000003", 13, "m_door_pending"),
                             ("660000004", 4, "m_door_other_room"), ("660000011", 9, "m_door_walkin")]:
        c, pg = ctx(b, "m", user); shot(pg, f"/th/r/{room}/", name); c.close()
    c, pg = ctx(b, "m", "660000012")
    shot(pg, "/th/book/8/20260930T1400/", "m_book_confirm", full=True)
    shot(pg, "/th/book/11/20260930T1400/", "m_book_301", full=True)
    c.close()
    c, pg = ctx(b, "d", "teacher@example.invalid")
    shot(pg, "/th/week/?date=2026-09-29", "d_teacher_week")
    shot(pg, "/th/staff/room-admin/", "d_teacher_room_admin", full=True)
    c.close()
    c, pg = ctx(b, "d", "maintainer")
    shot(pg, "/th/staff/today/", "d_staff_today", full=True)
    shot(pg, "/th/staff/room-admin/", "d_staff_approvals", full=True)
    shot(pg, "/th/staff/users/", "d_staff_users")
    shot(pg, "/th/staff/invitations/", "d_staff_invitations")
    shot(pg, "/th/staff/posters/", "d_staff_posters")
    shot(pg, "/th/staff/admin/", "d_staff_admin")
    shot(pg, "/th/", "d_home")
    c.close()

def session_b(b):
    c, pg = ctx(b, "m", "660000005"); shot(pg, "/th/r/5/", "m_door_closed"); c.close()

with sync_playwright() as p:
    b = p.chromium.launch()
    for frozen, fn in [("2026-09-29T14:05:00+07:00", session_a), ("2026-09-29T14:20:00+07:00", session_b)]:
        srv = server(frozen)
        try:
            fn(b)
        finally:
            srv.kill(); srv.wait()
    b.close()
