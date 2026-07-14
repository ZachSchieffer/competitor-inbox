"""Static, local-only executive dashboard and screenshot-ready hero views."""

from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
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

_CSS = r"""
:root{color-scheme:light;--bg:#f2f5f9;--surface:#fbfcfe;--ink:#142033;--muted:#56657a;--line:#d8e0ea;--accent:#2457d6;--accent-soft:#e8eefc;--good:#176b4d;--warn:#8a5a08;--radius:14px;--shadow:0 16px 40px rgba(34,56,92,.08)}
*{box-sizing:border-box}html{background:var(--bg);scroll-behavior:auto}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:16px;line-height:1.5}a{color:inherit}.shell{width:min(1240px,calc(100% - 48px));margin:0 auto}.mast{padding:42px 0 28px;border-bottom:1px solid var(--line);background:var(--surface)}.brandline{display:flex;justify-content:space-between;gap:24px;align-items:flex-start}.brand{font-size:14px;font-weight:760;letter-spacing:.08em;text-transform:uppercase}.stamp{display:inline-flex;align-items:center;border:1px solid var(--accent);border-radius:999px;color:var(--accent);font-size:12px;font-weight:760;letter-spacing:.07em;padding:7px 11px;text-transform:uppercase}.mast h1{font-size:clamp(42px,6vw,78px);letter-spacing:-.055em;line-height:.96;margin:62px 0 18px;max-width:920px}.mast p{color:var(--muted);font-size:19px;max-width:760px;margin:0}.window{display:flex;gap:22px;flex-wrap:wrap;margin-top:30px;color:var(--muted);font-size:14px}.window b{color:var(--ink)}main{padding:36px 0 76px}section{margin:0 0 24px;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:28px}.section-head{display:flex;justify-content:space-between;gap:22px;align-items:flex-start;margin-bottom:24px}.section-head h2{font-size:27px;letter-spacing:-.025em;line-height:1.1;margin:0}.section-head p{color:var(--muted);margin:8px 0 0;max-width:640px}.coverage{display:inline-flex;white-space:nowrap;border:1px solid var(--line);border-radius:999px;background:var(--bg);color:var(--muted);font-size:12px;font-weight:700;padding:7px 10px}.metrics{display:grid;grid-template-columns:1.5fr 1fr 1fr 1fr;gap:14px}.metric{min-height:142px;border:1px solid var(--line);border-radius:var(--radius);padding:20px;background:var(--surface)}.metric.primary{background:var(--accent);border-color:var(--accent);color:#f7f9ff}.metric .value{display:block;font-size:42px;font-variant-numeric:tabular-nums;font-weight:780;letter-spacing:-.045em;line-height:1}.metric .label{display:block;color:var(--muted);font-size:13px;font-weight:700;margin-top:29px}.metric.primary .label{color:#dbe5ff}.metric .note{display:block;color:var(--muted);font-size:12px;margin-top:6px}.metric.primary .note{color:#dbe5ff}.grid-two{display:grid;grid-template-columns:1.25fr .75fr;gap:18px}.subpanel{border:1px solid var(--line);border-radius:var(--radius);padding:20px}.subpanel h3{font-size:17px;margin:0 0 16px}.quadrant{display:grid;grid-template-columns:175px 1fr 90px;align-items:center;gap:14px;margin:0 0 16px}.quadrant:last-child{margin-bottom:0}.quadrant .label{font-size:13px;font-weight:680}.bar{height:9px;background:var(--accent);border-radius:999px;min-width:2px}.bar.secondary{background:#7892cf}.bar.tertiary{background:#a8b5ca}.bar.quiet{background:#c8d0dc}.number{text-align:right;font-variant-numeric:tabular-nums;font-size:13px;color:var(--muted)}.finding-list{display:grid;gap:0}.finding{padding:15px 0;border-bottom:1px solid var(--line)}.finding:last-child{border-bottom:0}.finding strong{display:block;font-size:18px}.finding span{color:var(--muted);font-size:13px}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:var(--radius)}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:13px 14px;border-bottom:1px solid var(--line);white-space:nowrap}th{background:var(--bg);color:var(--muted);font-size:11px;letter-spacing:.04em;text-transform:uppercase}tbody tr:last-child td{border-bottom:0}td.num{text-align:right;font-variant-numeric:tabular-nums}.posture{color:var(--accent);font-weight:720}.occasion-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.occasion{border:1px solid var(--line);border-radius:var(--radius);padding:17px}.occasion b{display:block;font-size:24px;font-variant-numeric:tabular-nums}.occasion span{color:var(--muted);font-size:13px}.scope-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.scope-block{border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}.scope-block h3{font-size:15px;margin:0;padding:16px;border-bottom:1px solid var(--line)}.message{padding:14px 16px;border-bottom:1px solid var(--line)}.message:last-child{border-bottom:0}.message b{display:block;font-size:13px}.message small{display:block;color:var(--muted);margin-top:4px}.empty{color:var(--muted);font-size:13px;padding:18px}.actions{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}.action{border:1px solid var(--line);border-radius:var(--radius);padding:20px}.action .time{color:var(--accent);font-size:12px;font-weight:780;text-transform:uppercase;letter-spacing:.06em}.action h3{font-size:18px;margin:11px 0 8px}.action p{color:var(--muted);font-size:14px;margin:0}.method{display:grid;grid-template-columns:1fr 1fr;gap:26px}.method h3{font-size:15px;margin:0 0 8px}.method p{color:var(--muted);font-size:14px;margin:0 0 14px}.foot{color:var(--muted);font-size:12px;padding:12px 0 38px;text-align:center}.prototype{outline:3px solid var(--accent);outline-offset:-3px}.prototype section:before{content:"ILLUSTRATIVE PROTOTYPE";display:block;color:var(--accent);font-size:10px;font-weight:800;letter-spacing:.09em;margin-bottom:12px}.hero-page{width:1080px;min-height:1350px;background:var(--bg);padding:64px}.hero-sheet{min-height:1222px;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface);padding:56px;display:flex;flex-direction:column}.hero-top{display:flex;justify-content:space-between;align-items:flex-start}.hero-kicker{font-size:15px;font-weight:800;letter-spacing:.09em;text-transform:uppercase}.hero-title{font-size:73px;line-height:.98;letter-spacing:-.055em;margin:110px 0 28px;max-width:890px}.hero-sub{color:var(--muted);font-size:22px;max-width:780px}.hero-census{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:72px}.hero-cell{border:1px solid var(--line);border-radius:var(--radius);padding:22px}.hero-cell b{font-size:42px;display:block;font-variant-numeric:tabular-nums;letter-spacing:-.04em}.hero-cell span{color:var(--muted);font-size:14px}.hero-bottom{margin-top:auto;display:flex;justify-content:space-between;align-items:flex-end;border-top:1px solid var(--line);padding-top:28px;color:var(--muted);font-size:14px}.hero-bottom strong{color:var(--ink);display:block;font-size:16px}.accent-rule{height:8px;width:124px;border-radius:999px;background:var(--accent);margin-top:30px}
@media(max-width:900px){.shell{width:min(100% - 28px,1240px)}.mast{padding-top:28px}.mast h1{margin-top:44px}.section-head{display:block}.coverage{display:flex;width:100%;max-width:100%;margin-top:14px;white-space:normal;line-height:1.35}.metrics,.scope-grid,.actions,.method{grid-template-columns:1fr}.grid-two{grid-template-columns:1fr}.occasion-grid{grid-template-columns:1fr 1fr}.quadrant{grid-template-columns:125px 1fr 72px}.metric{min-height:120px}.metric .label{margin-top:18px}section{padding:20px}.brandline{display:flex;flex-direction:column;align-items:flex-start}.stamp{margin-top:14px}}
@media(max-width:520px){.occasion-grid{grid-template-columns:1fr}.mast h1{font-size:43px}.window{display:block}.window span{display:block;margin-top:7px}.quadrant{grid-template-columns:1fr 60px}.quadrant .bar{grid-column:1/-1;grid-row:2}.number{grid-column:2}.section-head h2{font-size:24px}}
@media print{@page{size:auto;margin:12mm}body{background:#fff}section{box-shadow:none;break-inside:avoid}.hero-page{width:1080px;height:1350px;padding:64px}}
"""


def _e(value: Any) -> str:
    safe = sanitize_text(str(value if value is not None else ""))
    assert_recipient_safe(safe)
    return html.escape(safe, quote=True)


def _pct(count: int, total: int) -> str:
    return f"{(100 * count / total if total else 0):.1f}%"


def _coverage(summary: Mapping[str, Any]) -> str:
    coverage = summary.get("metadata", {}).get("coverage", {})
    label = str(coverage.get("label") or "Coverage unavailable")
    return f"{label} | n={int(summary.get('broadcast_count', 0)):,} broadcasts"


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
    findings = "".join(
        '<div class="finding">'
        f"<strong>{_e(item.get('value'))}</strong>"
        f"<span>{_e(item.get('label'))}. {int(item.get('numerator',0)):,} of {int(item.get('denominator',0)):,} qualified broadcasts.</span>"
        "</div>"
        for item in summary.get("findings", [])
    )
    occasions = "".join(
        f'<div class="occasion"><b>{int(count):,}</b><span>{_e(name)}</span></div>'
        for name, count in list(summary.get("occasions", {}).items())[:12]
    ) or '<div class="empty">No explicit seasonal occasions met the evidence rule.</div>'
    first_brand = summary.get("brands", [{}])[0] if summary.get("brands") else {}
    annual_copy = (
        "Use the prior 12 months to map retail moments before calendar planning begins."
        if int(meta.get("observed_days", 0)) >= 330
        else "Keep collecting history until annual planning coverage reaches 330 observed days."
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{_e(_CSP)}"><meta name="referrer" content="no-referrer">
<title>{_e(title)}</title><style>{_CSS}</style></head>
<body class="{body_class}"><header class="mast"><div class="shell"><div class="brandline"><span class="brand">ZHS Ecom | Competitive Email Intelligence</span>{_stamp(summary)}</div>
<h1>{_e(title)}</h1><p>A private view of competitor cadence, content mix, offers, and seasonal timing from emails already in an inbox you control.</p>
<div class="window"><span><b>{total:,}</b> qualified broadcasts</span><span><b>{brands}</b> brands</span><span><b>{_e(_date_window(meta))}</b></span><span>Updated {_e(str(meta.get('generated_at',''))[:10])}</span></div></div></header>
<main class="shell">
<section aria-labelledby="executive"><div>{_section_head('Executive Brief','The owner-level read on what competitors are sending and where the calendar has room.',coverage)}</div>
<div class="metrics"><div class="metric primary"><span class="value">{total:,}</span><span class="label">Qualified broadcasts</span><span class="note">Lifecycle excluded</span></div>
<div class="metric"><span class="value">{brands}</span><span class="label">Competitors tracked</span><span class="note">One census</span></div>
<div class="metric"><span class="value">{_pct(evergreen,total)}</span><span class="label">Evergreen content</span><span class="note">{evergreen:,} of {total:,}</span></div>
<div class="metric"><span class="value">{int(meta.get('observed_days',0)):,}</span><span class="label">Observed days</span><span class="note">{_e(coverage)}</span></div></div></section>
<section aria-labelledby="comparison">{_section_head('Competitor Comparison','Compare volume, planning mix, and strategic posture using qualified broadcasts only.',coverage)}{_brand_table(summary)}</section>
<section aria-labelledby="engine">{_section_head('Evergreen and Promotional Engine','Offer status and seasonality are independent, so the 4-part census stays useful for planning.',coverage)}
<div class="grid-two"><div class="subpanel"><h3>Four-quadrant census</h3>{_quadrant_rows(summary)}</div><div class="subpanel"><h3>What stands out</h3><div class="finding-list">{findings}</div></div></div></section>
<section aria-labelledby="seasonal">{_section_head('Seasonal Planner',annual_copy,coverage)}<div class="occasion-grid">{occasions}</div></section>
<section aria-labelledby="library">{_section_head('Messaging Library','Browse recent sanitized subjects by scope. Only broadcasts feed the strategy metrics.',coverage)}
<div class="scope-grid"><div class="scope-block"><h3>Broadcast</h3>{_messages(summary,'broadcast')}</div><div class="scope-block"><h3>Lifecycle</h3>{_messages(summary,'lifecycle')}</div><div class="scope-block"><h3>Uncertain</h3>{_messages(summary,'uncertain')}</div></div></section>
<section aria-labelledby="action">{_section_head('Owner Action Plan','Turn the census into the next planning conversation without treating inbox activity as performance.',coverage)}
<div class="actions"><div class="action"><span class="time">Next 30 days</span><h3>Audit the live calendar</h3><p>Compare planned sends with {_e(first_brand.get('brand') or 'the volume leader')} and preserve room for evergreen education.</p></div>
<div class="action"><span class="time">Next 60 days</span><h3>Separate content from offers</h3><p>Plan evergreen, everyday promotion, seasonal promotion, and seasonal content as distinct jobs.</p></div>
<div class="action"><span class="time">Next 90 days</span><h3>Build the lookback</h3><p>{_e(annual_copy)}</p></div></div></section>
<section aria-labelledby="methodology">{_section_head('Coverage and Methodology','Every visible claim carries its denominator and is limited by the observed inbox history.',coverage)}
<div class="method"><div><h3>Included</h3><p>Sanitized broadcast subject, preheader, visible text, receipt date, offer evidence, explicit seasonal language, and deterministic classifications.</p><h3>Excluded</h3><p>Recipient addresses, personalized links, remote images, tracking pixels, raw HTML, lifecycle messages from broadcast totals, and conversion claims.</p></div>
<div><h3>Coverage gates</h3><p>90 observed days support cadence, mix, and posture. 330 days support annual and prior-season planning. 730 days support year-over-year analysis.</p><h3>Interpretation</h3><p>Inbox data shows competitor behavior. It does not show revenue, margin, conversion, or which email performed best.</p></div></div></section>
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
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
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


def _hero_line(summary: Mapping[str, Any], variant: str) -> tuple[str, str]:
    priority = {name.casefold(): index for index, name in enumerate(
        ("SKIMS", "Olipop", "Poppi", "AG1", "Huel", "Liquid Death", "Nike")
    )}
    eligible = sorted(
        (brand for brand in summary.get("brands", []) if brand.get("hook_eligible")),
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
        return (
            f"{brand['brand']} sent {total:,} broadcasts in {brand['observed_days']:,} observed days.",
            f"{evergreen:,} of {total:,} were evergreen content. Lifecycle messages are excluded.",
        )
    total = int(summary.get("broadcast_count", 0))
    brands = int(summary.get("brand_count", 0))
    return (
        f"{total:,} competitor broadcasts mapped across {brands} brands.",
        "The dashboard separates evergreen content, promotions, and seasonal timing for calendar planning.",
    )


def render_hero(summary: Mapping[str, Any], variant: str = "brand") -> str:
    title, subtitle = _hero_line(summary, variant)
    meta = summary.get("metadata", {})
    total = int(summary.get("broadcast_count", 0))
    quadrant_cells = "".join(
        '<div class="hero-cell">'
        f"<b>{int(row.get('count',0)):,}</b>"
        f"<span>{_e(row.get('name'))} | {_pct(int(row.get('count',0)),total)}</span></div>"
        for row in summary.get("quadrants", [])
    )
    body_class = "prototype" if meta.get("illustrative_prototype") else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=1080">
<meta http-equiv="Content-Security-Policy" content="{_e(_CSP)}"><meta name="referrer" content="no-referrer"><title>Competitor Inbox hero</title><style>{_CSS}</style></head>
<body class="{body_class}"><main class="hero-page"><article class="hero-sheet"><div class="hero-top"><span class="hero-kicker">The Competitor Inbox</span>{_stamp(summary)}</div>
<h1 class="hero-title">{_e(title)}</h1><p class="hero-sub">{_e(subtitle)}</p><div class="accent-rule"></div><div class="hero-census">{quadrant_cells}</div>
<div class="hero-bottom"><div><strong>{_e(_date_window(meta))}</strong>{int(meta.get('observed_days',0)):,} observed days</div><div><strong>Private, local dashboard</strong>Every count cross-footed to the census</div></div>
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_freeze_manifest(
    summary: Mapping[str, Any],
    dashboard_path: str | Path,
    hero_paths: Iterable[str | Path],
    output_path: str | Path,
    *,
    screenshot_paths: Iterable[str | Path] = (),
    git_sha: str = "",
) -> Path:
    """Bind the census, rendered HTML, and any finished screenshots by hash."""

    dashboard = Path(dashboard_path).expanduser().resolve()
    heroes = [Path(path).expanduser().resolve() for path in hero_paths]
    screenshots = [Path(path).expanduser().resolve() for path in screenshot_paths]
    census_bytes = json.dumps(summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    manifest = {
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "census_sha256": hashlib.sha256(census_bytes).hexdigest(),
        "dashboard": {"path": str(dashboard), "sha256": _sha256(dashboard)},
        "hero_html": [{"path": str(path), "sha256": _sha256(path)} for path in heroes],
        "screenshots": [{"path": str(path), "sha256": _sha256(path)} for path in screenshots],
        "window": {
            "first": summary.get("metadata", {}).get("first_observed", ""),
            "last": summary.get("metadata", {}).get("last_observed", ""),
        },
        "qualified_broadcasts": int(summary.get("broadcast_count", 0)),
        "definition": "scope=broadcast; lifecycle and uncertain excluded",
        "dedupe": "source UID, Message-ID, content hash, then brand and campaign similarity",
        "filters": summary.get("metadata", {}).get("filters", {}),
        "model_mode": summary.get("metadata", {}).get("analysis_mode", "deterministic-only"),
        "model": summary.get("metadata", {}).get("analysis_model"),
        "git_sha": git_sha,
    }
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, destination)
    return destination


__all__ = [
    "generate_dashboard",
    "render_dashboard",
    "render_hero",
    "write_freeze_manifest",
    "write_hero_candidates",
]
