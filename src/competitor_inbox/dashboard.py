"""Static, local-only executive dashboard and screenshot-ready hero views."""

from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
import signal
import struct
import subprocess
import tempfile
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .aggregate import aggregate_records
from .sanitize import assert_recipient_safe, sanitize_text


_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; "
    "connect-src 'none'; script-src 'none'; frame-src 'none'; form-action 'none'; "
    "base-uri 'none'; object-src 'none'"
)

_HERO_WIDTH = 1080
_HERO_HEIGHT = 1350
_HERO_NAMES = ("hero-brand.html", "hero-portfolio.html")
_BROWSER_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)


class HeroRenderError(RuntimeError):
    """Raised when a local hero cannot be rendered and verified safely."""

_CSS = r"""
:root{color-scheme:light;--bg:#f2f5f9;--surface:#fbfcfe;--ink:#142033;--muted:#56657a;--line:#d8e0ea;--accent:#2457d6;--accent-soft:#e8eefc;--good:#176b4d;--warn:#8a5a08;--radius:14px;--shadow:0 16px 40px rgba(34,56,92,.08)}
*{box-sizing:border-box}html{background:var(--bg);scroll-behavior:auto}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:16px;line-height:1.5}a{color:inherit}.shell{width:min(1240px,calc(100% - 48px));margin:0 auto}.mast{padding:42px 0 28px;border-bottom:1px solid var(--line);background:var(--surface)}.brandline{display:flex;justify-content:space-between;gap:24px;align-items:flex-start}.brand{font-size:14px;font-weight:760;letter-spacing:.08em;text-transform:uppercase}.stamp,.freshness{display:inline-flex;align-items:center;border:1px solid var(--accent);border-radius:999px;color:var(--accent);font-size:12px;font-weight:760;letter-spacing:.07em;padding:7px 11px;text-transform:uppercase}.freshness{background:var(--accent-soft);letter-spacing:.02em;text-transform:none}.mast h1{font-size:clamp(42px,6vw,78px);letter-spacing:-.055em;line-height:.96;margin:62px 0 18px;max-width:920px}.mast p{color:var(--muted);font-size:19px;max-width:760px;margin:0}.window{display:flex;gap:22px;flex-wrap:wrap;margin-top:30px;color:var(--muted);font-size:14px;align-items:center}.window b{color:var(--ink)}main{padding:36px 0 76px}section{margin:0 0 24px;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:28px}.section-head{display:flex;justify-content:space-between;gap:22px;align-items:flex-start;margin-bottom:24px}.section-head h2{font-size:27px;letter-spacing:-.025em;line-height:1.1;margin:0}.section-head p{color:var(--muted);margin:8px 0 0;max-width:640px}.coverage{display:inline-flex;white-space:nowrap;border:1px solid var(--line);border-radius:999px;background:var(--bg);color:var(--muted);font-size:12px;font-weight:700;padding:7px 10px}.metrics{display:grid;grid-template-columns:1.5fr 1fr 1fr 1fr;gap:14px}.metric{min-height:142px;border:1px solid var(--line);border-radius:var(--radius);padding:20px;background:var(--surface)}.metric.primary{background:var(--accent);border-color:var(--accent);color:#f7f9ff}.metric .value{display:block;font-size:42px;font-variant-numeric:tabular-nums;font-weight:780;letter-spacing:-.045em;line-height:1}.metric .label{display:block;color:var(--muted);font-size:13px;font-weight:700;margin-top:29px}.metric.primary .label{color:#dbe5ff}.metric .note{display:block;color:var(--muted);font-size:12px;margin-top:6px}.metric.primary .note{color:#dbe5ff}.grid-two{display:grid;grid-template-columns:1.25fr .75fr;gap:18px}.subpanel{border:1px solid var(--line);border-radius:var(--radius);padding:20px}.subpanel h3{font-size:17px;margin:0 0 16px}.quadrant{display:grid;grid-template-columns:175px 1fr 90px;align-items:center;gap:14px;margin:0 0 16px}.quadrant:last-child{margin-bottom:0}.quadrant .label{font-size:13px;font-weight:680}.bar{height:9px;background:var(--accent);border-radius:999px;min-width:2px}.bar.secondary{background:#7892cf}.bar.tertiary{background:#a8b5ca}.bar.quiet{background:#c8d0dc}.number{text-align:right;font-variant-numeric:tabular-nums;font-size:13px;color:var(--muted)}.finding-list{display:grid;gap:0}.finding{padding:15px 0;border-bottom:1px solid var(--line)}.finding:last-child{border-bottom:0}.finding strong{display:block;font-size:18px}.finding span{color:var(--muted);font-size:13px}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:var(--radius)}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:13px 14px;border-bottom:1px solid var(--line);white-space:nowrap}th{background:var(--bg);color:var(--muted);font-size:11px;letter-spacing:.04em;text-transform:uppercase}tbody tr:last-child td{border-bottom:0}td.num{text-align:right;font-variant-numeric:tabular-nums}.posture{color:var(--accent);font-weight:720}.occasion-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.occasion{border:1px solid var(--line);border-radius:var(--radius);padding:17px}.occasion b{display:block;font-size:24px;font-variant-numeric:tabular-nums}.occasion span{color:var(--muted);font-size:13px}.scope-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.scope-block{border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}.scope-block h3{font-size:15px;margin:0;padding:16px;border-bottom:1px solid var(--line)}.message{padding:14px 16px;border-bottom:1px solid var(--line)}.message:last-child{border-bottom:0}.message b{display:block;font-size:13px}.message small{display:block;color:var(--muted);margin-top:4px}.empty{color:var(--muted);font-size:13px;padding:18px}.actions{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}.action{border:1px solid var(--line);border-radius:var(--radius);padding:20px}.action .time{color:var(--accent);font-size:12px;font-weight:780;text-transform:uppercase;letter-spacing:.06em}.action h3{font-size:18px;margin:11px 0 8px}.action p{color:var(--muted);font-size:14px;margin:0}.method{display:grid;grid-template-columns:1fr 1fr;gap:26px}.method h3{font-size:15px;margin:0 0 8px}.method p{color:var(--muted);font-size:14px;margin:0 0 14px}.foot{color:var(--muted);font-size:12px;padding:12px 0 38px;text-align:center}.prototype{outline:3px solid var(--accent);outline-offset:-3px}.prototype section:before{content:"ILLUSTRATIVE PROTOTYPE";display:block;color:var(--accent);font-size:10px;font-weight:800;letter-spacing:.09em;margin-bottom:12px}.hero-page{width:1080px;min-height:1350px;background:var(--bg);padding:64px}.hero-sheet{min-height:1222px;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface);padding:56px;display:flex;flex-direction:column}.hero-top{display:flex;justify-content:space-between;align-items:flex-start}.hero-kicker{font-size:15px;font-weight:800;letter-spacing:.09em;text-transform:uppercase}.hero-title{font-size:73px;line-height:.98;letter-spacing:-.055em;margin:110px 0 28px;max-width:890px}.hero-sub{color:var(--muted);font-size:22px;max-width:780px}.hero-census{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:72px}.hero-cell{border:1px solid var(--line);border-radius:var(--radius);padding:22px}.hero-cell b{font-size:42px;display:block;font-variant-numeric:tabular-nums;letter-spacing:-.04em}.hero-cell span{color:var(--muted);font-size:14px}.hero-bottom{margin-top:auto;display:flex;justify-content:space-between;align-items:flex-end;border-top:1px solid var(--line);padding-top:28px;color:var(--muted);font-size:14px}.hero-bottom strong{color:var(--ink);display:block;font-size:16px}.accent-rule{height:8px;width:124px;border-radius:999px;background:var(--accent);margin-top:30px}.dashboard-hero.hero-sheet{padding:44px 48px}.dashboard-hero .hero-title{font-size:56px;line-height:1;max-width:900px;margin:50px 0 18px}.dashboard-hero .hero-sub{font-size:18px;max-width:850px}.dashboard-hero .accent-rule{height:6px;margin-top:22px}.dashboard-product{margin-top:28px;border:1px solid var(--line);border-radius:var(--radius);background:var(--bg);overflow:hidden;box-shadow:0 12px 30px rgba(34,56,92,.07)}.dashboard-product-bar{display:flex;justify-content:space-between;align-items:center;gap:18px;padding:14px 18px;background:var(--surface);border-bottom:1px solid var(--line);font-size:12px;color:var(--muted)}.dashboard-product-bar strong{color:var(--ink);font-size:14px}.dashboard-product .hero-census{grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:0;padding:18px}.dashboard-product .hero-cell{padding:15px;background:var(--surface)}.dashboard-product .hero-cell b{font-size:31px}.dashboard-product .hero-cell span{font-size:11px}.package-head{display:flex;justify-content:space-between;align-items:center;padding:0 18px 10px;color:var(--muted);font-size:11px;font-weight:760;letter-spacing:.06em;text-transform:uppercase}.package-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:0 18px 18px}.package-item{min-height:72px;border:1px solid var(--line);border-radius:10px;background:var(--surface);padding:13px 14px}.package-item strong{display:block;font-size:14px}.package-item span{display:block;color:var(--muted);font-size:11px;margin-top:3px}.dashboard-hero .hero-bottom{padding-top:20px;font-size:12px}.dashboard-hero .hero-bottom strong{font-size:14px}
.hero-bottom>div:last-child{max-width:500px;text-align:right}
@media(max-width:900px){.shell{width:min(100% - 28px,1240px)}.mast{padding-top:28px}.mast h1{margin-top:44px}.section-head{display:block}.coverage{display:flex;width:100%;max-width:100%;margin-top:14px;white-space:normal;line-height:1.35}.metrics,.scope-grid,.actions,.method{grid-template-columns:1fr}.grid-two{grid-template-columns:1fr}.occasion-grid{grid-template-columns:1fr 1fr}.quadrant{grid-template-columns:125px 1fr 72px}.metric{min-height:120px}.metric .label{margin-top:18px}section{padding:20px}.brandline{display:flex;flex-direction:column;align-items:flex-start}.stamp,.freshness{margin-top:14px}}
@media(max-width:520px){.occasion-grid{grid-template-columns:1fr}.mast h1{font-size:43px}.window{display:block}.window span{display:block;margin-top:7px}.quadrant{grid-template-columns:1fr 60px}.quadrant .bar{grid-column:1/-1;grid-row:2}.number{grid-column:2}.section-head h2{font-size:24px}}
@media print{@page{size:auto;margin:12mm}body{background:#fff}section{box-shadow:none;break-inside:avoid}.hero-page{width:1080px;height:1350px;padding:64px}}
"""


def _e(value: Any) -> str:
    safe = sanitize_text(str(value if value is not None else ""))
    assert_recipient_safe(safe)
    return html.escape(safe, quote=True)


def _pct(count: int, total: int) -> str:
    return f"{(100 * count / total if total else 0):.1f}%"


def _source_completeness_label(value: str) -> str:
    if value == "complete":
        return "Complete source range"
    if value == "curated_export":
        return "Curated export subset"
    if value == "partial":
        return "Partial source coverage"
    return "Source completeness unavailable"


def _source_completeness_note(value: str) -> str:
    if value == "curated_export":
        return (
            "This is a curated export subset. Counts describe the collected sample, "
            "so single-brand volume comparisons are disabled."
        )
    if value == "complete":
        return "The analyzed source range is complete and error-accounted."
    return "Source coverage is partial or unavailable, so every claim stays bounded by its visible denominator."


def _coverage(summary: Mapping[str, Any]) -> str:
    coverage = summary.get("metadata", {}).get("coverage", {})
    label = str(coverage.get("label") or "Coverage unavailable")
    completeness = _source_completeness_label(
        str(summary.get("metadata", {}).get("source_completeness") or "")
    )
    return (
        f"{label} | {completeness} | "
        f"n={int(summary.get('broadcast_count', 0)):,} broadcasts"
    )


def _date_window(metadata: Mapping[str, Any]) -> str:
    first = str(metadata.get("first_observed") or "")
    last = str(metadata.get("last_observed") or "")
    if first and last:
        return f"{first} to {last}"
    return "Receipt window unavailable"


def _stamp(summary: Mapping[str, Any]) -> str:
    return '<span class="stamp">ILLUSTRATIVE PROTOTYPE</span>' if summary.get("metadata", {}).get("illustrative_prototype") else ""


def _section_head(title: str, copy: str, coverage: str) -> str:
    return (
        '<div class="section-head"><div>'
        f"<h2>{_e(title)}</h2><p>{_e(copy)}</p></div>"
        f'<span class="coverage">{_e(coverage)}</span></div>'
    )


def _quadrant_rows(summary: Mapping[str, Any]) -> str:
    total = int(summary.get("broadcast_count", 0))
    classes = ("", "secondary", "tertiary", "quiet")
    rows = []
    for index, row in enumerate(summary.get("quadrants", [])):
        count = int(row.get("count", 0))
        width = max(0.2, 100 * count / total) if total else 0.2
        rows.append(
            '<div class="quadrant">'
            f'<span class="label">{_e(row.get("name"))}</span>'
            f'<span class="bar {classes[index]}" style="width:{width:.2f}%"></span>'
            f'<span class="number">{count:,} / {total:,}<br>{_pct(count,total)}</span>'
            "</div>"
        )
    return "".join(rows)


def _brand_table(summary: Mapping[str, Any]) -> str:
    rows = []
    for brand in summary.get("brands", []):
        q = brand.get("quadrants", {})
        posture = brand.get("posture", {})
        rows.append(
            "<tr>"
            f"<td><strong>{_e(brand.get('brand'))}</strong></td>"
            f"<td class=\"num\">{int(brand.get('qualified_broadcasts',0)):,}</td>"
            f"<td class=\"num\">{int(brand.get('observed_days',0)):,}</td>"
            f"<td class=\"num\">{int(q.get('Evergreen content',0)):,}</td>"
            f"<td class=\"num\">{int(q.get('Everyday promotion',0)):,}</td>"
            f"<td class=\"num\">{int(q.get('Seasonal promotion',0)):,}</td>"
            f"<td class=\"num\">{int(q.get('Seasonal content',0)):,}</td>"
            f"<td><span class=\"posture\">{_e(posture.get('label','Mixed'))}</span></td>"
            f"<td>{_e(brand.get('coverage',{}).get('label',''))}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr><th>Brand</th><th>Broadcasts</th><th>Days</th>'
        '<th>Evergreen</th><th>Everyday promo</th><th>Seasonal promo</th><th>Seasonal content</th>'
        f"<th>Posture</th><th>Coverage</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _messages(summary: Mapping[str, Any], scope: str) -> str:
    items = [item for item in summary.get("library", []) if item.get("scope") == scope][:6]
    if not items:
        return '<div class="empty">No messages in this scope.</div>'
    return "".join(
        '<div class="message">'
        f"<b>{_e(item.get('subject') or 'Subject unavailable')}</b>"
        f"<small>{_e(item.get('brand'))} | {_e(item.get('date'))} | {_e(item.get('quadrant') or scope.title())}</small>"
        "</div>"
        for item in items
    )


def render_dashboard(summary: Mapping[str, Any], title: str = "The Competitor Inbox") -> str:
    """Render one complete static document with no executable or remote content."""

    meta = summary.get("metadata", {})
    total = int(summary.get("broadcast_count", 0))
    brands = int(summary.get("brand_count", 0))
    q = {row["name"]: row for row in summary.get("quadrants", [])}
    evergreen = int(q.get("Evergreen content", {}).get("count", 0))
    coverage = _coverage(summary)
    body_class = "prototype" if meta.get("illustrative_prototype") else ""
    source_completeness = str(meta.get("source_completeness") or "")
    source_note = _source_completeness_note(source_completeness)
    visible_findings = [
        item
        for item in summary.get("findings", [])
        if not (
            source_completeness == "curated_export"
            and str(item.get("label") or "") == "Highest inbox volume"
        )
    ]
    findings = "".join(
        '<div class="finding">'
        f"<strong>{_e(item.get('value'))}</strong>"
        f"<span>{_e(item.get('label'))}. {int(item.get('numerator',0)):,} of {int(item.get('denominator',0)):,} qualified broadcasts.</span>"
        "</div>"
        for item in visible_findings
    )
    occasions = "".join(
        f'<div class="occasion"><b>{int(count):,}</b><span>{_e(name)}</span></div>'
        for name, count in list(summary.get("occasions", {}).items())[:12]
    ) or '<div class="empty">No explicit seasonal occasions met the evidence rule.</div>'
    annual_copy = (
        "Use the prior 12 months to map retail moments before calendar planning begins."
        if int(meta.get("observed_days", 0)) >= 330
        else "Keep collecting history until annual planning coverage reaches 330 observed days."
    )
    generated_date = str(meta.get("generated_at") or "")[:10]
    freshness = f"Fresh as of {generated_date}" if generated_date else "Freshness unavailable"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{_e(_CSP)}"><meta name="referrer" content="no-referrer">
<title>{_e(title)}</title><style>{_CSS}</style></head>
<body class="{body_class}"><header class="mast"><div class="shell"><div class="brandline"><span class="brand">ZHS Ecom | Competitive Email Intelligence</span>{_stamp(summary)}</div>
<h1>{_e(title)}</h1><p>A private view of competitor cadence, content mix, offers, and seasonal timing from emails already in an inbox you control.</p>
<div class="window"><span><b>{total:,}</b> qualified broadcasts</span><span><b>{brands}</b> brands</span><span><b>{_e(_date_window(meta))}</b></span><span class="freshness" role="status">{_e(freshness)}</span></div></div></header>
<main class="shell">
<section aria-labelledby="executive"><div>{_section_head('Executive Brief','The owner-level read on what competitors are sending and where the calendar has room.',coverage)}</div>
<div class="metrics"><div class="metric primary"><span class="value">{total:,}</span><span class="label">Qualified broadcasts</span><span class="note">Lifecycle excluded</span></div>
<div class="metric"><span class="value">{brands}</span><span class="label">Competitors tracked</span><span class="note">One census</span></div>
<div class="metric"><span class="value">{_pct(evergreen,total)}</span><span class="label">Evergreen content</span><span class="note">{evergreen:,} of {total:,}</span></div>
<div class="metric"><span class="value">{int(meta.get('observed_days',0)):,}</span><span class="label">Observed days</span><span class="note">{_e(coverage)}</span></div></div></section>
<section aria-labelledby="comparison">{_section_head('Competitor Comparison','Compare planning mix and strategic posture using qualified broadcasts only.',coverage)}{_brand_table(summary)}</section>
<section aria-labelledby="engine">{_section_head('Evergreen and Promotional Engine','Offer status and seasonality are independent, so the 4-part census stays useful for planning.',coverage)}
<div class="grid-two"><div class="subpanel"><h3>Four-quadrant census</h3>{_quadrant_rows(summary)}</div><div class="subpanel"><h3>What stands out</h3><div class="finding-list">{findings}</div></div></div></section>
<section aria-labelledby="seasonal">{_section_head('Seasonal Planner',annual_copy,coverage)}<div class="occasion-grid">{occasions}</div></section>
<section aria-labelledby="library">{_section_head('Messaging Library','Browse recent sanitized subjects by scope. Only broadcasts feed the strategy metrics.',coverage)}
<div class="scope-grid"><div class="scope-block"><h3>Broadcast</h3>{_messages(summary,'broadcast')}</div><div class="scope-block"><h3>Lifecycle</h3>{_messages(summary,'lifecycle')}</div><div class="scope-block"><h3>Uncertain</h3>{_messages(summary,'uncertain')}</div></div></section>
<section aria-labelledby="action">{_section_head('Owner Action Plan','Turn the census into the next planning conversation without treating inbox activity as performance.',coverage)}
<div class="actions"><div class="action"><span class="time">Next 30 days</span><h3>Audit the live calendar</h3><p>Compare the planned calendar with the 4-part census and preserve room for evergreen education.</p></div>
<div class="action"><span class="time">Next 60 days</span><h3>Separate content from offers</h3><p>Plan evergreen, everyday promotion, seasonal promotion, and seasonal content as distinct jobs.</p></div>
<div class="action"><span class="time">Next 90 days</span><h3>Build the lookback</h3><p>{_e(annual_copy)}</p></div></div></section>
<section aria-labelledby="methodology">{_section_head('Coverage and Methodology','Every visible claim carries its denominator and is limited by the observed inbox history.',coverage)}
<div class="method"><div><h3>Included</h3><p>Sanitized broadcast subject, preheader, visible text, receipt date, offer evidence, explicit seasonal language, and deterministic classifications.</p><h3>Excluded</h3><p>Recipient addresses, personalized links, remote images, tracking pixels, raw HTML, lifecycle messages from broadcast totals, and conversion claims.</p></div>
<div><h3>Coverage gates</h3><p>90 observed days support cadence, mix, and posture. 330 days support annual and prior-season planning. 730 days support year-over-year analysis.</p><h3>Source completeness</h3><p>{_e(source_note)}</p><h3>Interpretation</h3><p>Inbox data shows competitor behavior. It does not show revenue, margin, conversion, or which email performed best.</p></div></div></section>
</main><footer class="shell foot">Built locally from sanitized inbox records. No remote resources are loaded.</footer></body></html>"""


def _as_summary(value: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    if isinstance(value, Mapping) and "quadrants" in value and "broadcast_count" in value:
        return dict(value)
    return aggregate_records(value)  # type: ignore[arg-type]


def _atomic_html(destination: Path, content: str, retain_previous: bool = True) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if retain_previous and destination.exists():
        previous = destination.with_name(destination.stem + ".previous" + destination.suffix)
        shutil.copy2(destination, previous)
        os.chmod(previous, 0o600)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    return destination


def generate_dashboard(
    value: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    output_path: str | Path,
    *,
    title: str = "The Competitor Inbox",
) -> Path:
    summary = _as_summary(value)
    return _atomic_html(Path(output_path).expanduser().resolve(), render_dashboard(summary, title=title))


def find_headless_browser() -> Path:
    """Return a deterministic local Chromium-family executable."""

    configured = os.environ.get("COMPETITOR_INBOX_BROWSER", "").strip()
    candidates = ([configured] if configured else []) + list(_BROWSER_CANDIDATES)
    for command in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        resolved = shutil.which(command)
        if resolved:
            candidates.append(resolved)
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
    raise HeroRenderError(
        "no supported local headless browser was found; install Chrome, Edge, or Chromium"
    )


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise HeroRenderError("headless browser did not produce a valid PNG")
    return struct.unpack(">II", header[16:24])


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _decode_png_rows(path: Path) -> tuple[int, int, int, list[bytes]]:
    """Decode the non-interlaced 8-bit RGB/RGBA PNGs emitted by Chromium."""

    payload = path.read_bytes()
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HeroRenderError("headless browser did not produce a valid PNG")
    offset = 8
    width = height = bit_depth = color_type = interlace = 0
    compressed = bytearray()
    while offset + 12 <= len(payload):
        chunk_length = struct.unpack(">I", payload[offset : offset + 4])[0]
        chunk_type = payload[offset + 4 : offset + 8]
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_length
        if chunk_end + 4 > len(payload):
            raise HeroRenderError("hero PNG contains a truncated chunk")
        chunk = payload[chunk_start:chunk_end]
        if chunk_type == b"IHDR":
            if len(chunk) != 13:
                raise HeroRenderError("hero PNG has an invalid IHDR chunk")
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
        elif chunk_type == b"IDAT":
            compressed.extend(chunk)
        elif chunk_type == b"IEND":
            break
        offset = chunk_end + 4

    if (width, height) != (_HERO_WIDTH, _HERO_HEIGHT):
        raise HeroRenderError("hero PNG must be exactly 1080x1350")
    if bit_depth != 8 or color_type not in {2, 6} or interlace != 0 or not compressed:
        raise HeroRenderError("hero PNG uses an unsupported pixel format")
    bytes_per_pixel = 3 if color_type == 2 else 4
    stride = width * bytes_per_pixel
    try:
        raw = zlib.decompress(bytes(compressed))
    except zlib.error as exc:
        raise HeroRenderError("hero PNG pixel data is corrupt") from exc
    if len(raw) != (stride + 1) * height:
        raise HeroRenderError("hero PNG pixel data has an invalid length")

    rows: list[bytes] = []
    previous = bytearray(stride)
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        scanline = bytearray(raw[cursor : cursor + stride])
        cursor += stride
        if filter_type not in {0, 1, 2, 3, 4}:
            raise HeroRenderError("hero PNG uses an unsupported scanline filter")
        reconstructed = bytearray(stride)
        for index, value in enumerate(scanline):
            left = reconstructed[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            above = previous[index]
            upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            else:
                predictor = _paeth(left, above, upper_left)
            reconstructed[index] = (value + predictor) & 0xFF
        rows.append(bytes(reconstructed))
        previous = reconstructed
    return width, height, bytes_per_pixel, rows


def audit_hero_png(path: str | Path) -> dict[str, float | int | bool]:
    """Reject raster corruption such as the dominant black overlays seen in QA."""

    source = Path(path).expanduser().resolve()
    width, height, bytes_per_pixel, rows = _decode_png_rows(source)
    total_pixels = width * height
    black_pixels = 0
    light_pixels = 0
    maximum_black_row = 0
    for row in rows:
        row_black = 0
        for offset in range(0, len(row), bytes_per_pixel):
            red, green, blue = row[offset : offset + 3]
            if max(red, green, blue) <= 12:
                black_pixels += 1
                row_black += 1
            if min(red, green, blue) >= 220:
                light_pixels += 1
        maximum_black_row = max(maximum_black_row, row_black)
    black_share = black_pixels / total_pixels
    light_share = light_pixels / total_pixels
    maximum_black_row_share = maximum_black_row / width
    passed = (
        black_share <= 0.03
        and maximum_black_row_share <= 0.50
        and light_share >= 0.55
    )
    result: dict[str, float | int | bool] = {
        "width": width,
        "height": height,
        "black_pixel_share": round(black_share, 6),
        "maximum_black_row_share": round(maximum_black_row_share, 6),
        "light_pixel_share": round(light_share, 6),
        "passed": passed,
    }
    if not passed:
        raise HeroRenderError("hero PNG failed the visual corruption audit")
    return result


def _stop_browser(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (AttributeError, ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=5)


def _render_hero_pngs_once(
    hero_paths: Iterable[str | Path],
    *,
    browser_path: str | Path | None = None,
) -> list[Path]:
    """Render the 2 local hero documents to verified 1080x1350 PNG files.

    The browser receives a temporary isolated profile, a stripped environment,
    and flags that disable background networking. Source documents are rejected
    if they contain executable or remote content.
    """

    sources = [Path(path).expanduser().resolve() for path in hero_paths]
    if tuple(path.name for path in sources) != _HERO_NAMES:
        raise HeroRenderError("expected hero-brand.html and hero-portfolio.html in order")
    browser = (
        Path(browser_path).expanduser().resolve()
        if browser_path is not None
        else find_headless_browser()
    )
    if not browser.is_file() or not os.access(browser, os.X_OK):
        raise HeroRenderError("configured headless browser is not executable")

    safe_environment = {
        key: value
        for key in ("HOME", "LANG", "LC_ALL", "PATH", "TMPDIR")
        if (value := os.environ.get(key))
    }
    rendered: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="competitor-inbox-hero-") as temporary:
        temporary_root = Path(temporary)
        profile = temporary_root / "profile"
        profile.mkdir(mode=0o700)
        for source in sources:
            if not source.is_file():
                raise HeroRenderError("hero HTML is missing")
            document = source.read_text(encoding="utf-8").casefold()
            if "<script" in document or "http://" in document or "https://" in document:
                raise HeroRenderError("hero HTML contains executable or remote content")

            temporary_png = temporary_root / f"{source.stem}.png"
            command = [
                str(browser),
                "--headless=new",
                "--disable-background-networking",
                "--disable-client-side-phishing-detection",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-domain-reliability",
                "--disable-extensions",
                "--disable-features=OptimizationHints,MediaRouter,DialMediaRouteProvider",
                "--disable-gpu",
                "--disable-sync",
                "--force-color-profile=srgb",
                "--force-device-scale-factor=1",
                "--hide-scrollbars",
                "--host-resolver-rules=MAP * 0.0.0.0, EXCLUDE localhost",
                "--metrics-recording-only",
                "--no-default-browser-check",
                "--no-first-run",
                "--run-all-compositor-stages-before-draw",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=1000",
                f"--window-size={_HERO_WIDTH},{_HERO_HEIGHT}",
                f"--screenshot={temporary_png}",
                source.as_uri(),
            ]
            try:
                process = subprocess.Popen(
                    command,
                    cwd=temporary_root,
                    env=safe_environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError as exc:
                raise HeroRenderError("headless browser failed to render a hero image") from exc

            ready = False
            last_size = -1
            stable_checks = 0
            deadline = time.monotonic() + 30
            try:
                while time.monotonic() < deadline:
                    if temporary_png.is_file():
                        try:
                            dimensions = _png_dimensions(temporary_png)
                        except (HeroRenderError, OSError):
                            dimensions = (0, 0)
                        size = temporary_png.stat().st_size
                        stable_checks = stable_checks + 1 if size == last_size else 0
                        last_size = size
                        if dimensions == (_HERO_WIDTH, _HERO_HEIGHT) and (
                            stable_checks >= 2 or process.poll() is not None
                        ):
                            ready = True
                            break
                    if process.poll() is not None:
                        break
                    time.sleep(0.05)
            finally:
                _stop_browser(process)

            if not ready or not temporary_png.is_file():
                raise HeroRenderError("headless browser failed to render a hero image")
            if _png_dimensions(temporary_png) != (_HERO_WIDTH, _HERO_HEIGHT):
                raise HeroRenderError("hero PNG must be exactly 1080x1350")
            audit_hero_png(temporary_png)

            destination = source.with_suffix(".png")
            staged = destination.with_suffix(destination.suffix + ".tmp")
            shutil.copyfile(temporary_png, staged)
            os.chmod(staged, 0o600)
            os.replace(staged, destination)
            rendered.append(destination)
    return rendered


def render_hero_pngs(
    hero_paths: Iterable[str | Path],
    *,
    browser_path: str | Path | None = None,
) -> list[Path]:
    """Render and visually validate both hero candidates, retrying one cold failure."""

    paths = list(hero_paths)
    last_error: HeroRenderError | None = None
    for _ in range(2):
        try:
            return _render_hero_pngs_once(paths, browser_path=browser_path)
        except HeroRenderError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _hero_coverage(source_completeness: str, scope: str) -> tuple[str, str]:
    if source_completeness == "curated_export":
        return (
            "Curated export subset",
            "Counts describe the collected sample. Single-brand comparisons are disabled.",
        )
    if source_completeness == "complete":
        return ("Complete source range", f"Every count cross-footed to this {scope}.")
    return (
        "Partial source coverage",
        "Use the visible denominators and coverage gates before drawing comparisons.",
    )


def _hero_priority(summary: Mapping[str, Any]) -> tuple[str, ...]:
    """Return an explicit launch-only brand universe, or no restriction.

    The dashboard is a reusable product, so named launch brands cannot be a
    global default. A launch build may bind an ordered list in census metadata;
    when present, only eligible brands in that list may power the single-brand
    hero and the order breaks equal-volume ties.
    """

    raw = summary.get("metadata", {}).get("hero_priority_brands")
    if raw in (None, []):
        return ()
    if not isinstance(raw, list):
        raise HeroRenderError("hero_priority_brands must be an ordered list")
    output: list[str] = []
    seen: set[str] = set()
    for value in raw:
        brand = " ".join(str(value or "").split()).strip()
        key = brand.casefold()
        if not brand or key in seen:
            raise HeroRenderError("hero_priority_brands contains an empty or duplicate value")
        seen.add(key)
        output.append(brand)
    return tuple(output)


def _hero_context(summary: Mapping[str, Any], variant: str) -> dict[str, Any]:
    priority_brands = _hero_priority(summary)
    priority = {
        name.casefold(): index for index, name in enumerate(priority_brands)
    }
    eligible = sorted(
        (
            brand
            for brand in summary.get("brands", [])
            if brand.get("hook_eligible")
            and (
                not priority
                or str(brand.get("brand", "")).casefold() in priority
            )
        ),
        key=lambda brand: (
            -int(brand.get("qualified_broadcasts", 0)),
            priority.get(str(brand.get("brand", "")).casefold(), len(priority)),
            str(brand.get("brand", "")).casefold(),
        ),
    )
    if eligible and variant == "brand":
        brand = eligible[0]
        total = int(brand["qualified_broadcasts"])
        evergreen = int(brand["quadrants"].get("Evergreen content", 0))
        quadrant_rows = [
            {
                "name": row.get("name"),
                "count": int(brand.get("quadrants", {}).get(str(row.get("name")), 0)),
            }
            for row in summary.get("quadrants", [])
        ]
        coverage_label, coverage_note = _hero_coverage(
            str(brand.get("source_completeness") or "partial"), "brand"
        )
        return {
            "scope": "brand",
            "layout": "dashboard",
            "title": (
                f"{brand['brand']} sent {total:,} broadcasts in "
                f"{brand['observed_days']:,} observed days."
            ),
            "subtitle": (
                f"{evergreen:,} of {total:,} were evergreen content. "
                "Lifecycle messages are excluded."
            ),
            "total": total,
            "quadrants": quadrant_rows,
            "window": {
                "first_observed": brand.get("first_observed", ""),
                "last_observed": brand.get("last_observed", ""),
            },
            "observed_days": int(brand.get("observed_days", 0)),
            "footer_label": f"{brand['brand']} census",
            "coverage_label": coverage_label,
            "coverage_note": coverage_note,
            "hook": {
                "type": "priority_brand" if priority else "generic_brand",
                "selection_basis": "largest qualified-broadcast denominator",
                "brand": str(brand["brand"]),
                "numerator": evergreen,
                "denominator": total,
                "descriptor": "evergreen content",
                "date_start": str(brand.get("first_observed", "")),
                "date_end": str(brand.get("last_observed", "")),
                "observed_days": int(brand.get("observed_days", 0)),
                "coverage_label": coverage_label,
                "source_completeness": str(
                    brand.get("source_completeness") or "partial"
                ),
            },
        }
    total = int(summary.get("broadcast_count", 0))
    brands = int(summary.get("brand_count", 0))
    metadata = summary.get("metadata", {})
    pipeline = summary.get("pipeline", {})
    scopes = summary.get("scope_counts", {})
    source_total = int(
        pipeline.get("distinct_messages")
        or sum(int(value or 0) for value in scopes.values())
        or total
    )
    lifecycle = int(scopes.get("lifecycle", 0) or 0)
    uncertain = int(scopes.get("uncertain", 0) or 0)
    coverage_label, coverage_note = _hero_coverage(
        str(metadata.get("source_completeness") or "partial"), "portfolio"
    )
    strongest_quadrant = max(
        (
            {
                "descriptor": str(row.get("name") or "qualified broadcasts").casefold(),
                "numerator": int(row.get("count", 0)),
            }
            for row in summary.get("quadrants", [])
            if isinstance(row, Mapping)
        ),
        key=lambda row: (row["numerator"], row["descriptor"]),
        default={"descriptor": "qualified broadcasts", "numerator": total},
    )
    return {
        "scope": "portfolio-dashboard" if variant == "brand" else "portfolio",
        "layout": "dashboard" if variant == "brand" else "poster",
        "title": (
            f"{source_total:,} emails from {brands} brands, "
            "mapped into one strategy dashboard."
        ),
        "subtitle": (
            f"{total:,} qualified broadcasts after {lifecycle:,} lifecycle and "
            f"{uncertain:,} uncertain messages were separated."
        ),
        "total": total,
        "quadrants": list(summary.get("quadrants", [])),
        "window": metadata,
        "observed_days": int(metadata.get("observed_days", 0)),
        "footer_label": "Portfolio census",
        "coverage_label": coverage_label,
        "coverage_note": coverage_note,
        "hook": {
            "type": "multi_brand_fallback" if priority else "generic_portfolio",
            "selection_basis": (
                "no eligible configured priority brand"
                if priority
                else "no eligible single brand"
            ),
            "brand": None,
            "numerator": int(strongest_quadrant["numerator"]),
            "denominator": total,
            "descriptor": str(strongest_quadrant["descriptor"]),
            "date_start": str(metadata.get("first_observed", "")),
            "date_end": str(metadata.get("last_observed", "")),
            "observed_days": int(metadata.get("observed_days", 0)),
            "coverage_label": coverage_label,
            "source_completeness": str(
                metadata.get("source_completeness") or "partial"
            ),
        },
    }


def render_hero(summary: Mapping[str, Any], variant: str = "brand") -> str:
    context = _hero_context(summary, variant)
    meta = summary.get("metadata", {})
    total = int(context["total"])
    quadrant_cells = "".join(
        '<div class="hero-cell">'
        f"<b>{int(row.get('count',0)):,}</b>"
        f"<span>{_e(row.get('name'))} | {_pct(int(row.get('count',0)),total)}</span></div>"
        for row in context["quadrants"]
    )
    if context["layout"] == "dashboard":
        product_surface = f"""<div class="dashboard-product">
<div class="dashboard-product-bar"><strong>{_e(context['footer_label'])}</strong><span>{total:,} qualified broadcasts | {int(context['observed_days']):,} observed days</span></div>
<div class="hero-census">{quadrant_cells}</div>
<div class="package-head"><span>Owner-level dashboard</span><span>{_e(context['coverage_label'])}</span></div>
<div class="package-grid">
<div class="package-item"><strong>Competitor comparison</strong><span>Cadence, mix, and posture by brand</span></div>
<div class="package-item"><strong>Evergreen + promo mix</strong><span>4-part census with visible denominators</span></div>
<div class="package-item"><strong>Seasonal planner</strong><span>Occasion timing with lookback gates</span></div>
<div class="package-item"><strong>Messaging library + action plan</strong><span>Sanitized examples and owner decisions</span></div>
</div></div>"""
    else:
        product_surface = f'<div class="hero-census">{quadrant_cells}</div>'
    sheet_class = "hero-sheet dashboard-hero" if context["layout"] == "dashboard" else "hero-sheet"
    body_class = "prototype" if meta.get("illustrative_prototype") else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=1080">
<meta http-equiv="Content-Security-Policy" content="{_e(_CSP)}"><meta name="referrer" content="no-referrer"><title>Competitor Inbox hero</title><style>{_CSS}</style></head>
<body class="{body_class}"><main class="hero-page"><article class="{sheet_class}" data-census-scope="{_e(context['scope'])}"><div class="hero-top"><span class="hero-kicker">The Competitor Inbox</span>{_stamp(summary)}</div>
<h1 class="hero-title">{_e(context['title'])}</h1><p class="hero-sub">{_e(context['subtitle'])}</p><div class="accent-rule"></div>{product_surface}
<div class="hero-bottom"><div><strong>{_e(_date_window(context['window']))}</strong>{int(context['observed_days']):,} observed days</div><div><strong>{_e(context['coverage_label'])}</strong>{_e(context['coverage_note'])}</div></div>
</article></main></body></html>"""


def write_hero_candidates(
    value: Mapping[str, Any] | Iterable[Mapping[str, Any]], output_dir: str | Path
) -> list[Path]:
    summary = _as_summary(value)
    root = Path(output_dir).expanduser().resolve()
    return [
        _atomic_html(root / "hero-brand.html", render_hero(summary, "brand"), retain_previous=False),
        _atomic_html(root / "hero-portfolio.html", render_hero(summary, "portfolio"), retain_previous=False),
    ]


def hero_selection(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact hook evidence rendered on the primary hero."""

    return dict(_hero_context(summary, "brand")["hook"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _freeze_metrics(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical numeric snapshot allowed in launch artifacts."""

    quadrant_rows = {
        str(row.get("name")): {
            "count": int(row.get("count", 0)),
            "percentage": float(row.get("percentage", 0.0)),
        }
        for row in summary.get("quadrants", [])
        if isinstance(row, Mapping)
    }
    everyday_promotion = int(
        quadrant_rows.get("Everyday promotion", {}).get("count", 0)
    )
    seasonal_promotion = int(
        quadrant_rows.get("Seasonal promotion", {}).get("count", 0)
    )
    seasonal_content = int(
        quadrant_rows.get("Seasonal content", {}).get("count", 0)
    )
    seasonal_count = seasonal_promotion + seasonal_content
    scope_counts = {
        key: int(summary.get("scope_counts", {}).get(key, 0))
        for key in ("broadcast", "lifecycle", "uncertain")
    }
    brand_metrics = {
        str(row.get("brand")): {
            "distinct_messages": int(row.get("distinct_messages", 0)),
            "qualified_broadcasts": int(row.get("qualified_broadcasts", 0)),
            "lifecycle": int(row.get("lifecycle", 0)),
            "uncertain": int(row.get("uncertain", 0)),
            "observed_days": int(row.get("observed_days", 0)),
            "observed_weeks": int(row.get("observed_weeks", 0)),
            "months_represented": int(row.get("months_represented", 0)),
            "first_observed": str(row.get("first_observed") or ""),
            "last_observed": str(row.get("last_observed") or ""),
            "offer_count": int(row.get("offer_count", 0)),
            "numeric_offer_count": int(row.get("numeric_offer_count", 0)),
            "quadrants": {
                name: int(value)
                for name, value in dict(row.get("quadrants", {})).items()
            },
            "posture": {
                "label": str(row.get("posture", {}).get("label") or ""),
                "share": float(row.get("posture", {}).get("share", 0.0)),
                "runner_up_share": float(
                    row.get("posture", {}).get("runner_up_share", 0.0)
                ),
            },
        }
        for row in summary.get("brands", [])
        if isinstance(row, Mapping) and str(row.get("brand") or "")
    }
    cadence_coverage_brands = sum(
        value["qualified_broadcasts"] >= 30 and value["observed_days"] >= 90
        for value in brand_metrics.values()
    )
    metadata = summary.get("metadata", {})
    qualified_broadcasts = int(summary.get("broadcast_count", 0))
    brand_count = int(summary.get("brand_count", 0))
    offer_count = everyday_promotion + seasonal_promotion
    return {
        "raw_messages": int(summary.get("pipeline", {}).get("raw_fetched", 0)),
        "qualified_broadcasts": qualified_broadcasts,
        "brand_count": brand_count,
        "broadcast_brand_count": int(summary.get("broadcast_brand_count", 0)),
        "observed_days": int(metadata.get("observed_days", 0)),
        "scope_counts": scope_counts,
        "offer_count": offer_count,
        "offer_share": (
            round(100 * offer_count / qualified_broadcasts, 1)
            if qualified_broadcasts
            else 0.0
        ),
        "seasonal_count": seasonal_count,
        "seasonal_share": (
            round(100 * seasonal_count / qualified_broadcasts, 1)
            if qualified_broadcasts
            else 0.0
        ),
        "seasonal_promotion_count": seasonal_promotion,
        "seasonal_offer_share": (
            round(100 * seasonal_promotion / seasonal_count, 1)
            if seasonal_count
            else 0.0
        ),
        "cadence_coverage_brand_count": cadence_coverage_brands,
        "cadence_coverage_brand_share": (
            round(100 * cadence_coverage_brands / brand_count, 1)
            if brand_count
            else 0.0
        ),
        "quadrants": quadrant_rows,
        "intents": {
            str(name): int(value)
            for name, value in dict(summary.get("intent_counts", {})).items()
        },
        "occasions": {
            str(name): int(value)
            for name, value in dict(summary.get("occasions", {})).items()
        },
        "window": {
            "first": str(metadata.get("first_observed") or ""),
            "last": str(metadata.get("last_observed") or ""),
        },
        "brands": brand_metrics,
    }


def write_freeze_manifest(
    summary: Mapping[str, Any],
    dashboard_path: str | Path,
    hero_paths: Iterable[str | Path],
    output_path: str | Path,
    *,
    screenshot_paths: Iterable[str | Path] = (),
    git_sha: str = "",
    git_dirty: bool = True,
) -> Path:
    """Bind the census, rendered HTML, and any finished screenshots by hash."""

    dashboard = Path(dashboard_path).expanduser().resolve()
    heroes = [Path(path).expanduser().resolve() for path in hero_paths]
    if len(heroes) != len(_HERO_NAMES) or {path.name for path in heroes} != set(
        _HERO_NAMES
    ):
        raise HeroRenderError("freeze requires the canonical brand and portfolio hero HTML")
    for path in heroes:
        variant = "brand" if path.name == "hero-brand.html" else "portfolio"
        try:
            rendered = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise HeroRenderError("freeze could not read canonical hero HTML") from error
        if rendered != render_hero(summary, variant):
            raise HeroRenderError("hero HTML does not match the census used by the freeze")
    screenshots = [Path(path).expanduser().resolve() for path in screenshot_paths]
    screenshot_entries = []
    for path in screenshots:
        width, height = _png_dimensions(path)
        if (width, height) != (_HERO_WIDTH, _HERO_HEIGHT):
            raise HeroRenderError("frozen hero PNG must be exactly 1080x1350")
        visual_audit = audit_hero_png(path)
        screenshot_entries.append(
            {
                "path": str(path),
                "sha256": _sha256(path),
                "width": width,
                "height": height,
                "visual_audit": visual_audit,
            }
        )
    census_bytes = json.dumps(summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    frozen_metrics = _freeze_metrics(summary)
    manifest = {
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "illustrative_prototype": bool(
            summary.get("metadata", {}).get("illustrative_prototype")
        ),
        "stamp": (
            "ILLUSTRATIVE PROTOTYPE"
            if bool(summary.get("metadata", {}).get("illustrative_prototype"))
            else None
        ),
        "census_sha256": hashlib.sha256(census_bytes).hexdigest(),
        "dashboard": {"path": str(dashboard), "sha256": _sha256(dashboard)},
        "hero_html": [{"path": str(path), "sha256": _sha256(path)} for path in heroes],
        "screenshots": screenshot_entries,
        "window": {
            "first": summary.get("metadata", {}).get("first_observed", ""),
            "last": summary.get("metadata", {}).get("last_observed", ""),
        },
        "qualified_broadcasts": int(summary.get("broadcast_count", 0)),
        "metrics": frozen_metrics,
        "hero_selection": hero_selection(summary),
        "definition": "scope=broadcast; lifecycle and uncertain excluded",
        "dedupe": "source UID, Message-ID, content hash, then brand and campaign similarity",
        "filters": summary.get("metadata", {}).get("filters", {}),
        "model_mode": summary.get("metadata", {}).get("analysis_mode", "deterministic-only"),
        "model": summary.get("metadata", {}).get("analysis_model"),
        "git_sha": git_sha,
        "git_dirty": git_dirty,
    }
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    return destination


__all__ = [
    "HeroRenderError",
    "audit_hero_png",
    "find_headless_browser",
    "generate_dashboard",
    "hero_selection",
    "render_dashboard",
    "render_hero",
    "render_hero_pngs",
    "write_freeze_manifest",
    "write_hero_candidates",
]
