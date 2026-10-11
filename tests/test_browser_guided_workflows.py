"""Exercise the guided decision in both languages, including without JS."""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from core.models import User
from tests import browserlib, factories

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("language,javascript", [("th", False), ("en", True)])
def test_guided_approval_validates_other_and_records_choice(
    rooms,
    live_server,
    browser_clock,
    browser,
    language,
    javascript,
):
    candidate = factories.make_user(eligibility=User.Eligibility.PENDING, verified=False)
    staff_user = factories.make_user(is_operational_staff=True)
    context = browser.new_context(java_script_enabled=javascript, viewport={"width": 390, "height": 844})
    try:
        page = context.new_page()
        watched = browserlib.WatchedPage(page=page).watch()
        browserlib.sign_in(
            page, live_server.url, staff_user.institutional_id, browserlib.FACTORY_PASSWORD, language
        )
        browserlib.visit(page, live_server.url, "staff/today/", language)
        field = page.locator(f"#decision-APPROVED-{candidate.pk}-reason")
        panel = page.locator("details").filter(has=field)
        panel.locator("summary").click()
        panel.locator("select").select_option("identity_confirmed")
        snapshots = Path("artifacts/qa/ux-review-20261011")
        snapshots.mkdir(parents=True, exist_ok=True)
        page.locator("#user-approvals").screenshot(path=str(snapshots / f"guided-approval-{language}.png"))
        panel.locator("select").select_option("other")
        panel.locator("button[type=submit]").click()
        expect(panel.locator("[role=alert]")).to_be_visible()
        candidate.refresh_from_db()
        assert candidate.eligibility == User.Eligibility.PENDING
        panel.locator("select").select_option("identity_confirmed")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        panel.locator("button[type=submit]").click()
        expect(field).to_have_count(0)
        candidate.refresh_from_db()
        assert candidate.eligibility == User.Eligibility.APPROVED
        assert candidate.eligibility_reason == "Student identity confirmed by staff"
        watched.assert_clean(allow_console_status=(400,))
    finally:
        context.close()
