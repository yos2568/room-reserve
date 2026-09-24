import pathlib
import sys

from playwright.sync_api import sync_playwright

html = pathlib.Path(sys.argv[1]).resolve()
pdf = pathlib.Path(sys.argv[2])
preview = sys.argv[3] if len(sys.argv) > 3 else None

FOOTER = (
    '<div style="width:100%;font-family:sans-serif;font-size:7.5pt;color:#8a94a6;padding:0 15mm;'
    'display:flex;justify-content:space-between"><span>คู่มือการใช้งานระบบจองห้องซ้อม</span>'
    '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'
)

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(html.as_uri())
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready")
    page.pdf(
        path=str(pdf),
        format="A4",
        print_background=True,
        prefer_css_page_size=True,
        display_header_footer=True,
        header_template="<span></span>",
        footer_template=FOOTER,
    )
    browser.close()
print("wrote", pdf)
