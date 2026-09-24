"""Render docs/manual/roomreserve-manual.md into a print-ready HTML page."""

import pathlib
import re
import sys

import markdown

src = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
md = src.read_text(encoding="utf-8")

# The cover carries the title block; drop it from the body.
body_md = md.split("\n---\n", 1)[1]
body = markdown.markdown(body_md, extensions=["tables", "md_in_html", "attr_list", "sane_lists"])

# Table of contents from the level and chapter headings.
toc = []
for level, text in re.findall(r"<h([23])>(.*?)</h\1>", body):
    toc.append((int(level), re.sub(r"<.*?>", "", text)))
toc_html = "".join(
    f'<li class="toc-{lvl}">{text}</li>' for lvl, text in toc if lvl == 2 or text[:3] in {"1.4", "2.3", "3.2"}
)

CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Thai:wght@400;500;600;700&family=IBM+Plex+Sans+Thai+Looped:wght@400;600&display=swap');
:root{--navy:#173a6a;--navy2:#0f2747;--crimson:#a3262f;--ink:#1f2937;--muted:#5b6474;--line:#e3e8f0;
  --student:#2563eb;--student-bg:#eff5ff;--teacher:#0f766e;--teacher-bg:#ecfbf7;--admin:#a3262f;--admin-bg:#fff1f2}
@page{size:A4;margin:16mm 15mm 18mm 15mm}
@page :first{margin:0}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{font-family:'IBM Plex Sans Thai',sans-serif;color:var(--ink);font-size:10.4pt;line-height:1.7;margin:0}
code{font-family:'IBM Plex Sans Thai',monospace;background:#f1f4f9;border-radius:4px;padding:0 4px;font-size:.92em}
p{margin:.35em 0 .6em}
ul,ol{margin:.3em 0 .8em;padding-left:1.4em}
li{margin:.15em 0}

/* Cover */
.cover{height:297mm;width:210mm;position:relative;overflow:hidden;color:#fff;page-break-after:always;
  background:radial-gradient(circle at 85% 12%,rgba(255,255,255,.12) 0 18%,transparent 19%),
             radial-gradient(circle at 8% 92%,rgba(163,38,47,.55) 0 22%,transparent 23%),
             linear-gradient(160deg,var(--navy) 0%,var(--navy2) 100%);padding:34mm 22mm}
.cover .brand{display:flex;align-items:center;gap:12px;font-weight:700;letter-spacing:.02em}
.cover .brand b{font-family:Georgia,serif;font-size:22pt}
.cover .brand span{border-left:1px solid rgba(255,255,255,.5);padding-left:12px;font-size:12pt}
.cover h1{font-size:40pt;line-height:1.25;margin:34mm 0 6mm;font-weight:700}
.cover .sub{font-size:14pt;opacity:.9;max-width:150mm}
.cover .url{display:inline-block;margin-top:9mm;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.35);
  border-radius:999px;padding:6px 16px;font-size:11pt}
.cover .cards{position:absolute;left:22mm;right:22mm;bottom:42mm;display:grid;grid-template-columns:repeat(3,1fr);gap:6mm}
.cover .card{background:#fff;color:var(--ink);border-radius:5mm;padding:6mm 6mm 5mm;border-top:3mm solid}
.cover .card.student{border-color:var(--student)} .cover .card.teacher{border-color:var(--teacher)} .cover .card.admin{border-color:var(--admin)}
.cover .card .n{font-size:9pt;color:var(--muted)} .cover .card .t{font-size:15pt;font-weight:700;margin:1mm 0}
.cover .card .d{font-size:9pt;color:var(--muted);line-height:1.5}
.cover .foot{position:absolute;left:22mm;right:22mm;bottom:16mm;font-size:9.5pt;opacity:.85}

/* Contents */
.toc{page-break-after:always}
.toc h2{font-size:22pt;color:var(--navy);margin:0 0 6mm}
.toc ol{list-style:none;padding:0;counter-reset:none}
.toc li{padding:3.2mm 0;border-bottom:1px dashed var(--line);font-size:12pt}
.toc li.toc-3{padding-left:8mm;font-size:10.5pt;color:var(--muted)}

/* Headings */
h2{font-size:19pt;color:var(--navy);margin:0 0 4mm;line-height:1.35}
h3{font-size:13pt;margin:7mm 0 2.5mm;padding-left:3.5mm;border-left:1.3mm solid var(--navy);line-height:1.35;break-after:avoid}
section.level{page-break-before:always}
section.level>h2:first-child{color:#fff;border-radius:5mm;padding:7mm 8mm;margin-bottom:6mm;font-size:21pt}
section.student>h2:first-child{background:linear-gradient(120deg,var(--student),#1e3a8a)}
section.teacher>h2:first-child{background:linear-gradient(120deg,var(--teacher),#134e4a)}
section.admin>h2:first-child{background:linear-gradient(120deg,var(--admin),#6b1220)}
section.student h3{border-color:var(--student)} section.teacher h3{border-color:var(--teacher)} section.admin h3{border-color:var(--admin)}
body>h2{margin-top:2mm}

/* Tables */
table{width:100%;border-collapse:separate;border-spacing:0;margin:2mm 0 5mm;border:1px solid var(--line);border-radius:3mm;overflow:hidden;font-size:9.8pt;break-inside:avoid}
th{background:#f3f6fb;text-align:left;font-weight:600;color:var(--navy)}
th,td{padding:2.2mm 3.2mm;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
section.student th{background:var(--student-bg);color:#1e3a8a} section.teacher th{background:var(--teacher-bg);color:#134e4a}
section.admin th{background:var(--admin-bg);color:#7f1d1d}
.chip{display:inline-block;border-radius:999px;padding:0 10px;font-weight:600;font-size:9.5pt;color:#fff;white-space:nowrap}
.chip.student{background:var(--student)} .chip.teacher{background:var(--teacher)} .chip.admin{background:var(--admin)}

/* Callouts */
.callout{border-radius:4mm;padding:4mm 5mm 3mm 14mm;margin:4mm 0 5mm;position:relative;break-inside:avoid}
.callout::before{position:absolute;left:4.5mm;top:3.6mm;width:6mm;height:6mm;border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-weight:700;color:#fff;font-size:10pt;line-height:6mm;text-align:center}
.callout.warn{background:#fff8e6;border:1px solid #f4d38b} .callout.warn::before{content:"!";background:#d97706}
.callout.tip{background:#eefbf3;border:1px solid #a7e3bf} .callout.tip::before{content:"✓";background:#16a34a}
.callout p:first-child{margin-top:0}

/* Screenshots */
figure{margin:0;break-inside:avoid}
figure img{display:block;width:100%}
figcaption{font-size:8.6pt;color:var(--muted);text-align:center;margin-top:2mm;line-height:1.45}
.shots{display:flex;gap:7mm;justify-content:center;align-items:flex-start;margin:4mm 0 6mm;break-inside:avoid}
figure.phone{width:58mm}
figure.phone img{border:2.2mm solid #111827;border-radius:6mm;max-height:112mm;object-fit:cover;object-position:top;
  box-shadow:0 3mm 8mm rgba(15,39,71,.18)}
.shots.three figure.phone{width:47mm}
.shots.three figure.phone img{max-height:78mm;border-width:1.8mm;border-radius:5mm}
figure.phone.single{margin:4mm auto 6mm}
figure.phone.single img{max-height:140mm}
figure.desktop{margin:4mm 0 6mm}
figure.desktop img{border:1px solid #cfd8e6;border-top:6mm solid #e7ecf4;border-radius:3mm;max-height:105mm;object-fit:cover;object-position:top;
  box-shadow:0 2mm 6mm rgba(15,39,71,.12)}
.shots.desk figure.desktop{flex:1;margin:0}
.shots.desk figure.desktop img{max-height:60mm}
.note{font-size:8.5pt;color:var(--muted);margin-top:8mm}
"""

cover = """
<div class="cover">
  <div class="brand"><b>FAA</b><span>ระบบจองห้องซ้อม · Room Reserve</span></div>
  <h1>คู่มือการใช้งาน<br>ระบบจองห้องซ้อม</h1>
  <div class="sub">วิธีใช้ ข้อพึงระวัง และเคล็ดลับ สำหรับนิสิต อาจารย์ และแอดมิน</div>
  <div class="url">roomreserve.yos.in.th</div>
  <div class="cards">
    <div class="card student"><div class="n">ส่วนที่ 1</div><div class="t">นิสิต</div><div class="d">สมัคร จอง เช็คอินที่ประตู ยกเลิก และคะแนนโทษ</div></div>
    <div class="card teacher"><div class="n">ส่วนที่ 2</div><div class="t">อาจารย์</div><div class="d">ดูตารางห้อง และอนุมัติคำขอห้อง 301</div></div>
    <div class="card admin"><div class="n">ส่วนที่ 3</div><div class="t">แอดมิน</div><div class="d">อนุมัติ จัดการผู้ใช้ ป้าย QR และงานประจำ</div></div>
  </div>
  <div class="foot">ภาควิชาดนตรีตะวันตก คณะศิลปกรรมศาสตร์ จุฬาลงกรณ์มหาวิทยาลัย · อาคารศิลปกรรม ชั้น 3</div>
</div>
"""

html = f"""<!doctype html><html lang="th"><head><meta charset="utf-8">
<title>คู่มือการใช้งานระบบจองห้องซ้อม</title><style>{CSS}</style></head><body>
{cover}
<div class="toc"><h2>สารบัญ</h2><ol>{toc_html}</ol></div>
{body}
</body></html>"""
out.write_text(html, encoding="utf-8")
print("wrote", out)
