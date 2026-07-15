"""Static, local-only executive dashboard and screenshot-ready hero views."""

from __future__ import annotations

import base64
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
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable, Mapping

from .aggregate import aggregate_records
from .creative_gallery import (
    GALLERY_TARGET_MAX,
    GALLERY_TARGET_MIN,
    normalize_creative_metadata,
    synthetic_creative_gallery,
    unavailable_creative_gallery,
)
from .sanitize import assert_recipient_safe, sanitize_text
from .schedule import (
    INCREMENTAL_OVERLAP_DAYS,
    SCHEDULE_HOUR_LOCAL,
    SCHEDULE_MINUTE_LOCAL,
)


_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
    "connect-src 'none'; script-src 'none'; frame-src 'none'; form-action 'none'; "
    "base-uri 'none'; object-src 'none'"
)

_HERO_WIDTH = 1080
_HERO_HEIGHT = 1350
_HERO_FEED_PREVIEW_WIDTH = 390
_HERO_MIN_FEED_COPY_PX = 14
_HERO_FEED_COPY_SOURCE_PX = 40
_HERO_NAMES = ("hero-brand.html", "hero-portfolio.html")
_SECTION_CAPTURE_NAMES = (
    "01-header.png",
    "02-executive-brief.png",
    "03-competitor-comparison.png",
    "04-evergreen-promotional-engine.png",
    "05-seasonal-planner.png",
    "06-messaging-library.png",
    "07-action-plan.png",
    "08-coverage-methodology.png",
)
_BROWSER_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)


class HeroRenderError(RuntimeError):
    """Raised when a local hero cannot be rendered and verified safely."""


@lru_cache(maxsize=1)
def _inter_tight_font_face() -> str:
    """Return the bundled Inter Tight variable font as an offline data URL."""

    font = files("competitor_inbox").joinpath("assets/InterTight-Latin.woff2")
    encoded = base64.b64encode(font.read_bytes()).decode("ascii")
    return (
        "@font-face{font-family:'Inter Tight';font-style:normal;"
        "font-weight:400 800;font-display:swap;"
        f"src:url(data:font/woff2;base64,{encoded}) format('woff2')}}"
    )


_CSS = r"""
:root{color-scheme:dark;--bg:#000000;--surface:#0C0D12;--surface-2:#101218;--zebra:#101218;--ink:#FAFBFC;--muted:#878C96;--quiet:#5C616B;--line:rgba(255,255,255,.10);--accent:#3D6CFF;--accent-soft:#101218;--radius:12px;--radius-lg:18px;--shadow:none}
*{box-sizing:border-box}
html{background:var(--bg);scroll-behavior:auto}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"Inter Tight",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:16px;font-weight:400;line-height:1.5;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
a{color:var(--accent)}
.shell{width:min(1120px,calc(100% - 48px));margin:0 auto}
.mast{padding:42px 0 30px;border-bottom:1px solid var(--line);background:var(--surface)}
.brandline{display:flex;justify-content:space-between;gap:24px;align-items:flex-start}
.brand{color:var(--ink);font-size:14px;font-weight:700;letter-spacing:.06em;text-transform:uppercase}
.stamp,.freshness,.coverage{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:999px;background:var(--surface);color:var(--muted);font-size:12px;font-weight:600;padding:7px 11px}
.stamp{letter-spacing:.06em;text-transform:uppercase}
.mast h1{max-width:920px;margin:62px 0 18px;color:var(--ink);font-size:clamp(42px,6vw,78px);font-weight:800;letter-spacing:-.045em;line-height:.98;text-wrap:balance}
.mast p{max-width:760px;margin:0;color:var(--muted);font-size:19px}
.window{display:flex;align-items:center;flex-wrap:wrap;gap:22px;margin-top:30px;color:var(--muted);font-size:14px}
.window b{color:var(--ink);font-weight:700;font-variant-numeric:tabular-nums}
main{padding:36px 0 76px}
section{margin:0 0 24px;padding:28px;border:1px solid var(--line);border-radius:var(--radius-lg);background:var(--surface);box-shadow:none}
.section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:22px;margin-bottom:24px}
.section-head h2{margin:0;color:var(--ink);font-size:27px;font-weight:700;letter-spacing:-.02em;line-height:1.1}
.section-head p{max-width:640px;margin:8px 0 0;color:var(--muted)}
.coverage{max-width:360px;white-space:normal;line-height:1.35}
.metrics{display:grid;grid-template-columns:1.5fr 1fr 1fr 1fr;gap:14px}
.metric{min-height:142px;padding:20px;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface)}
.metric.primary{border-left:4px solid var(--accent)}
.metric .value{display:block;color:var(--ink);font-size:42px;font-weight:800;font-variant-numeric:tabular-nums;letter-spacing:-.04em;line-height:1}
.metric .label{display:block;margin-top:29px;color:var(--ink);font-size:13px;font-weight:600}
.metric .note{display:block;margin-top:6px;color:var(--muted);font-size:12px}
.grid-two{display:grid;grid-template-columns:1.25fr .75fr;gap:18px}
.subpanel{padding:20px;border:1px solid var(--line);border-radius:var(--radius);background:var(--bg)}
.subpanel h3{margin:0 0 16px;color:var(--ink);font-size:17px;font-weight:600}
.quadrant{display:grid;grid-template-columns:175px 1fr 90px;align-items:center;gap:14px;margin:0 0 16px}
.quadrant:last-child{margin-bottom:0}
.quadrant .label{color:var(--ink);font-size:13px;font-weight:600}
.bar-track{display:block;width:100%;height:10px;overflow:hidden;border-radius:999px;background:var(--line)}
.bar,.bar.secondary,.bar.tertiary,.bar.quiet{display:block;height:100%;min-width:2px;border-radius:999px;background:var(--accent)}
.number{text-align:right;color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
.finding-list{display:grid;gap:0}
.finding{padding:15px 0;border-bottom:1px solid var(--line)}
.finding:last-child{border-bottom:0}
.finding strong{display:block;color:var(--ink);font-size:18px;font-weight:700}
.finding span{color:var(--muted);font-size:13px}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:var(--radius-lg);background:var(--surface)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:13px 14px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{background:var(--bg);color:var(--muted);font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase}
tbody tr:nth-child(even){background:var(--zebra)}
tbody tr:last-child td{border-bottom:0}
td.num{text-align:right;color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}
.posture{display:inline-flex;padding:5px 9px;border:1px solid var(--line);border-radius:999px;background:var(--surface);color:var(--ink);font-size:11px;font-weight:600;line-height:1.2}
.posture.posture-muted{color:var(--quiet)}
.occasion-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.occasion{padding:17px;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface)}
.occasion b{display:block;color:var(--ink);font-size:24px;font-weight:700;font-variant-numeric:tabular-nums}
.occasion span{color:var(--muted);font-size:13px}
.scope-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.scope-block{overflow:hidden;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface)}
.scope-block h3{margin:0;padding:16px;border-bottom:1px solid var(--line);color:var(--ink);font-size:15px;font-weight:600}
.message{padding:14px 16px;border-bottom:1px solid var(--line)}
.message:last-child{border-bottom:0}
.message b{display:block;color:var(--ink);font-size:13px;font-weight:600}
.message small{display:block;margin-top:4px;color:var(--muted)}
.empty{padding:18px;color:var(--muted);font-size:13px}
.actions{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.action{padding:20px;border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:var(--radius);background:var(--surface)}
.action .time{color:var(--accent);font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase}
.action h3{margin:11px 0 8px;color:var(--ink);font-size:18px;font-weight:600}
.action p{margin:0;color:var(--muted);font-size:14px}
.method{display:grid;grid-template-columns:1fr 1fr;gap:26px}
.method h3{margin:0 0 8px;color:var(--ink);font-size:15px;font-weight:600}
.method p{margin:0 0 14px;color:var(--muted);font-size:14px}
.foot{padding:12px 0 38px;text-align:center;color:var(--muted);font-size:12px}
.prototype{outline:3px solid var(--accent);outline-offset:-3px}
.prototype section:before{display:block;margin-bottom:12px;color:var(--accent);font-size:10px;font-weight:800;letter-spacing:.09em;content:"ILLUSTRATIVE PROTOTYPE"}
.hero-page{width:1080px;min-height:1350px;padding:64px;background:#000000}
.hero-sheet{display:flex;min-height:1222px;flex-direction:column;padding:56px;border:1px solid var(--line);border-radius:var(--radius-lg);background:var(--surface);box-shadow:none}
.hero-top{display:flex;align-items:flex-start;justify-content:space-between}
.hero-kicker{color:var(--ink);font-size:15px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}
.hero-title{max-width:890px;margin:110px 0 28px;color:var(--ink);font-size:73px;font-weight:800;letter-spacing:-.045em;line-height:.98}
.hero-sub{max-width:780px;color:var(--muted);font-size:22px}
.hero-census{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:72px}
.hero-cell{padding:22px;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface)}
.hero-cell b{display:block;color:var(--ink);font-size:42px;font-weight:800;font-variant-numeric:tabular-nums;letter-spacing:-.04em}
.hero-cell span{color:var(--muted);font-size:14px}
.hero-bottom{display:flex;align-items:flex-end;justify-content:space-between;margin-top:auto;padding-top:28px;border-top:1px solid var(--line);color:var(--muted);font-size:14px}
.hero-bottom strong{display:block;color:var(--ink);font-size:16px;font-weight:700}
.accent-rule{width:124px;height:4px;margin-top:30px;border-radius:999px;background:var(--accent)}
.dashboard-product{overflow:hidden;margin-top:28px;border:1px solid var(--line);border-radius:var(--radius-lg);background:var(--bg);box-shadow:none}
.dashboard-product .hero-census{grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:0;padding:18px}
.package-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:0 18px 18px}
.package-item{min-height:72px;padding:13px 14px;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface)}
.package-item strong{display:block;color:var(--ink);font-size:14px;font-weight:600}
.update-proof{display:grid;grid-template-columns:1fr;gap:10px;padding:0 18px 18px}
.update-line{margin:0;padding:11px 12px;border:1px solid var(--line);border-radius:var(--radius);background:var(--bg)}
@media(max-width:900px){.shell{width:min(100% - 28px,1120px)}.mast{padding-top:28px}.mast h1{margin-top:44px}.section-head{display:block}.coverage{width:100%;max-width:100%;margin-top:14px}.metrics,.scope-grid,.actions,.method,.grid-two{grid-template-columns:1fr}.occasion-grid{grid-template-columns:1fr 1fr}.quadrant{grid-template-columns:125px 1fr 72px}.brandline{flex-direction:column;align-items:flex-start}}
@media(max-width:520px){.occasion-grid{grid-template-columns:1fr}.mast h1{font-size:43px}.window{display:block}.window span{display:block;margin-top:7px}.quadrant{grid-template-columns:1fr 60px}.quadrant .bar-track{grid-column:1/-1;grid-row:2}.number{grid-column:2}.section-head h2{font-size:24px}}
@media print{@page{size:auto;margin:12mm}body{background:#0C0D12}section{box-shadow:none;break-inside:avoid}.hero-page{width:1080px;height:1350px;padding:64px}}
"""

_DASHBOARD_POLISH_CSS = r"""
.dashboard-page{min-width:320px;overflow-x:hidden;background:#000000;color:#FAFBFC;font-family:"Inter Tight",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:15px;font-weight:400;line-height:1.55}
.dashboard-page .shell{width:min(1280px,calc(100% - 64px))}
.dashboard-page .mast{padding:46px 0 40px;border-bottom:1px solid rgba(255,255,255,.10);background:#0C0D12;box-shadow:none}
.dashboard-page .brandline{align-items:center}
.dashboard-page .brand{color:#FAFBFC;font-size:12px;font-weight:700;letter-spacing:.08em}
.dashboard-page .stamp,.dashboard-page .freshness{border-color:rgba(255,255,255,.10);background:#0C0D12;color:#878C96;font-size:11px;font-weight:600}
.dashboard-page .mast h1{max-width:780px;margin:62px 0 20px;color:#FAFBFC;font-size:clamp(50px,6vw,76px);font-weight:800;letter-spacing:-.045em;line-height:.98}
.dashboard-page .mast p{max-width:68ch;color:#878C96;font-size:18px;line-height:1.55}
.dashboard-page .window{gap:0;margin-top:34px;color:#878C96;font-size:13px}
.dashboard-page .window>span:not(.freshness){padding:0 18px;border-left:1px solid rgba(255,255,255,.10)}
.dashboard-page .window>span:not(.freshness):first-child{padding-left:0;border-left:0}
.dashboard-page .window b{color:#FAFBFC;font-weight:700}
.dashboard-page .window .freshness{margin-left:18px}
.dashboard-page main{padding:48px 0 92px}
.dashboard-page section{margin:0 0 28px;padding:34px;border:1px solid rgba(255,255,255,.10);border-radius:18px;background:#0C0D12;box-shadow:none}
.dashboard-page .section-head{gap:32px;margin-bottom:30px}
.dashboard-page .section-head h2{color:#FAFBFC;font-size:29px;font-weight:700;letter-spacing:-.025em;line-height:1.12}
.dashboard-page .section-head p{max-width:65ch;margin-top:9px;color:#878C96;line-height:1.55}
.dashboard-page .coverage{max-width:360px;border-color:rgba(255,255,255,.10);border-radius:999px;background:#0C0D12;color:#878C96;font-size:11px;font-weight:600;letter-spacing:.01em;line-height:1.4;overflow-wrap:anywhere}
.dashboard-page .metrics{grid-template-columns:1.35fr repeat(3,minmax(0,1fr));gap:14px}
.dashboard-page .metric{min-height:158px;padding:22px;border:1px solid rgba(255,255,255,.10);border-radius:12px;background:#0C0D12;box-shadow:none}
.dashboard-page .metric.primary{border-color:rgba(255,255,255,.10);border-left:4px solid #3D6CFF;background:#0C0D12;color:#FAFBFC;box-shadow:none}
.dashboard-page .metric .value{color:#FAFBFC;font-size:45px;font-weight:800;letter-spacing:-.04em}
.dashboard-page .metric .label{margin-top:33px;color:#FAFBFC;font-size:13px;font-weight:600}
.dashboard-page .metric .note,.dashboard-page .metric.primary .note{color:#878C96;font-size:11px;line-height:1.4}
.dashboard-page .metric.primary .label{color:#FAFBFC}
.dashboard-page .grid-two{grid-template-columns:minmax(0,1.15fr) minmax(300px,.85fr);gap:20px}
.dashboard-page .subpanel{padding:24px;border:1px solid rgba(255,255,255,.10);border-radius:12px;background:#101218}
.dashboard-page .subpanel h3{margin-bottom:22px;color:#FAFBFC;font-size:16px;font-weight:600;letter-spacing:-.01em}
.dashboard-page .quadrant{grid-template-columns:180px minmax(140px,1fr) 108px;gap:16px;margin-bottom:20px}
.dashboard-page .quadrant .label{color:#FAFBFC;font-size:13px;font-weight:600}
.dashboard-page .bar-track{height:12px;border:0;border-radius:999px;background:rgba(255,255,255,.10);box-shadow:none}
.dashboard-page .bar,.dashboard-page .bar.secondary,.dashboard-page .bar.tertiary,.dashboard-page .bar.quiet{background:#3D6CFF}
.dashboard-page .number{color:#878C96;font-size:12px;line-height:1.35}
.dashboard-page .finding{padding:16px 0;border-bottom:1px solid rgba(255,255,255,.10);border-left:0}
.dashboard-page .finding:first-child{padding-top:2px}
.dashboard-page .finding strong{color:#FAFBFC;font-size:19px;font-weight:700;letter-spacing:-.015em}
.dashboard-page .finding span{display:block;margin-top:3px;color:#878C96;line-height:1.45}
.dashboard-page .table-wrap{border:1px solid rgba(255,255,255,.10);border-radius:18px;background:#0C0D12;box-shadow:none;scrollbar-color:#5C616B transparent;overscroll-behavior-inline:contain}
.dashboard-page table{min-width:1060px;border-collapse:separate;border-spacing:0;font-size:13px}
.dashboard-page th,.dashboard-page td{padding:14px 16px;border-bottom:1px solid rgba(255,255,255,.10)}
.dashboard-page th{position:sticky;top:0;z-index:1;background:#101218;color:#878C96;font-size:11px;font-weight:600;letter-spacing:.06em}
.dashboard-page tbody tr{background:#0C0D12}
.dashboard-page tbody tr:nth-child(even){background:#101218}
.dashboard-page tbody tr:hover{background:#101218}
.dashboard-page tbody tr:last-child td{border-bottom:0}
.dashboard-page th:first-child,.dashboard-page td:first-child{position:sticky;left:0;z-index:2;border-right:1px solid rgba(255,255,255,.10);background:inherit}
.dashboard-page th:first-child{z-index:3;background:#101218}
.dashboard-page td strong{color:#FAFBFC;font-weight:600}
.dashboard-page th:last-child,.dashboard-page td:last-child{min-width:178px;max-width:220px;white-space:normal;overflow-wrap:anywhere;line-height:1.35}
.dashboard-page td.num{color:#FAFBFC;font-weight:600}
.dashboard-page .posture{border-color:rgba(255,255,255,.10);background:#0C0D12;color:#FAFBFC;font-weight:600}
.dashboard-page .posture.posture-muted{color:#5C616B}
.dashboard-page .activity-panel{margin-bottom:24px;padding:24px;border:1px solid rgba(255,255,255,.10);border-radius:12px;background:#101218}
.dashboard-page .activity-head{display:flex;align-items:flex-start;justify-content:space-between;gap:32px;margin-bottom:20px}
.dashboard-page .activity-head h3{margin:0;color:#FAFBFC;font-size:16px;font-weight:600;letter-spacing:-.01em}
.dashboard-page .activity-head p{max-width:68ch;margin:5px 0 0;color:#878C96;font-size:12px;line-height:1.45}
.dashboard-page .heat-legend{display:flex;align-items:center;gap:7px;white-space:nowrap;color:#878C96;font-size:10px}
.dashboard-page .legend-cells{display:flex;gap:3px}
.dashboard-page .activity-grid{display:grid;grid-template-columns:repeat(52,minmax(3px,1fr));gap:4px}
.dashboard-page .week-cell{display:block;height:34px;border:1px solid rgba(255,255,255,.10);border-radius:4px;background:#101218}
.dashboard-page .week-cell.level-1{background:rgba(61,108,255,.18)}
.dashboard-page .week-cell.level-2{background:rgba(61,108,255,.38)}
.dashboard-page .week-cell.level-3{background:rgba(61,108,255,.64)}
.dashboard-page .week-cell.level-4{background:#3D6CFF}
.dashboard-page .activity-labels{display:grid;grid-template-columns:repeat(5,1fr);margin-top:9px;color:#5C616B;font-size:10px}
.dashboard-page .activity-labels span:nth-child(2),.dashboard-page .activity-labels span:nth-child(3),.dashboard-page .activity-labels span:nth-child(4){text-align:center}
.dashboard-page .activity-labels span:last-child{text-align:right}
.dashboard-page .occasion-grid{grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.dashboard-page .occasion{min-height:100px;padding:18px;border:1px solid rgba(255,255,255,.10);border-radius:12px;background:#0C0D12}
.dashboard-page .occasion b{color:#FAFBFC;font-size:28px;font-weight:700;letter-spacing:-.025em}
.dashboard-page .occasion span{display:block;margin-top:10px;color:#878C96;font-size:12px}
.dashboard-page .scope-grid{grid-template-columns:1.25fr .875fr .875fr;gap:16px}
.dashboard-page .scope-block{border:1px solid rgba(255,255,255,.10);border-radius:12px;background:#0C0D12}
.dashboard-page .scope-block h3{padding:17px 18px;border-bottom:1px solid rgba(255,255,255,.10);background:#101218;color:#FAFBFC;font-size:13px;font-weight:600}
.dashboard-page .message{padding:15px 18px;border-bottom:1px solid rgba(255,255,255,.10)}
.dashboard-page .message b{color:#FAFBFC;font-size:12px;font-weight:600;line-height:1.45;white-space:normal}
.dashboard-page .message small{color:#5C616B;font-size:10px;line-height:1.4}
.dashboard-page .creative-overview{display:grid;grid-template-columns:1.3fr repeat(3,minmax(0,.7fr));gap:12px;margin-bottom:20px}
.dashboard-page .creative-overview-item{min-height:96px;padding:17px 18px;border:1px solid rgba(255,255,255,.10);border-radius:12px;background:#101218}
.dashboard-page .creative-overview-item b{display:block;color:#FAFBFC;font-size:25px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1}
.dashboard-page .creative-overview-item span{display:block;margin-top:10px;color:#878C96;font-size:11px;line-height:1.4}
.dashboard-page .creative-brand-list{display:grid;gap:14px;margin-bottom:24px}
.dashboard-page .creative-brand-card{padding:20px;border:1px solid rgba(255,255,255,.10);border-radius:12px;background:#101218}
.dashboard-page .creative-brand-card[data-state="insufficient"]{border-color:rgba(61,108,255,.42)}
.dashboard-page .creative-brand-card[data-state="unavailable"]{background:#0C0D12}
.dashboard-page .creative-brand-head{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;margin-bottom:16px}
.dashboard-page .creative-brand-head h3{margin:0;color:#FAFBFC;font-size:18px;font-weight:650;letter-spacing:-.015em}
.dashboard-page .creative-brand-head p{margin:5px 0 0;color:#878C96;font-size:12px;line-height:1.45}
.dashboard-page .creative-status{display:inline-flex;flex:0 0 auto;padding:6px 9px;border:1px solid rgba(255,255,255,.10);border-radius:999px;background:#0C0D12;color:#878C96;font-size:10px;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
.dashboard-page .creative-status.ready{border-color:rgba(61,108,255,.45);color:#FAFBFC}
.dashboard-page .creative-status.insufficient{border-color:rgba(61,108,255,.45);color:#3D6CFF}
.dashboard-page .creative-strip{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}
.dashboard-page .creative-card{min-width:0;overflow:hidden;margin:0;border:1px solid rgba(255,255,255,.10);border-radius:10px;background:#0C0D12}
.dashboard-page .creative-card img{display:block;width:100%;aspect-ratio:320/420;object-fit:cover;background:#000000}
.dashboard-page .creative-card figcaption{min-height:54px;padding:9px 10px;color:#878C96;font-size:10px;line-height:1.35}
.dashboard-page .creative-empty{display:flex;min-height:150px;align-items:center;justify-content:center;padding:24px;border:1px dashed rgba(255,255,255,.14);border-radius:10px;background:#0C0D12;color:#878C96;font-size:12px;text-align:center}
.dashboard-page .creative-target-note{margin:0 0 20px;color:#878C96;font-size:12px}
.dashboard-page .actions{gap:16px}
.dashboard-page .action{padding:24px;border:1px solid rgba(255,255,255,.10);border-left:3px solid #3D6CFF;border-radius:12px;background:#0C0D12}
.dashboard-page .action .time{color:#3D6CFF;font-size:10px;font-weight:700;letter-spacing:.08em}
.dashboard-page .action h3{color:#FAFBFC;font-size:18px;font-weight:600;letter-spacing:-.015em}
.dashboard-page .action p{color:#878C96;line-height:1.55}
.dashboard-page .method{gap:48px}
.dashboard-page .method h3{color:#FAFBFC;font-size:13px;font-weight:600}
.dashboard-page .method p{max-width:64ch;color:#878C96;line-height:1.6}
.dashboard-page .foot{padding:0 0 48px;color:#5C616B;font-size:11px}
.dashboard-page .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:900px){.dashboard-page .shell{width:min(100% - 40px,1120px)}.dashboard-page .metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.dashboard-page .metrics .primary{grid-column:span 2}.dashboard-page .grid-two,.dashboard-page .scope-grid,.dashboard-page .actions,.dashboard-page .method{grid-template-columns:1fr}.dashboard-page .section-head{display:block}.dashboard-page .coverage{margin-top:16px}.dashboard-page .occasion-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.dashboard-page .creative-overview{grid-template-columns:repeat(2,minmax(0,1fr))}.dashboard-page .creative-strip{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:520px){.dashboard-page{font-size:14px}.dashboard-page .shell{width:calc(100% - 28px)}.dashboard-page .mast{padding:28px 0 30px}.dashboard-page .brandline{width:100%;align-items:flex-start;gap:10px}.dashboard-page .brand{max-width:100%;font-size:10px;line-height:1.35;white-space:normal;overflow-wrap:anywhere}.dashboard-page .stamp,.dashboard-page .freshness{margin-top:8px}.dashboard-page .mast h1{max-width:100%;margin:44px 0 16px;font-size:clamp(36px,11vw,43px);line-height:1;letter-spacing:-.04em;overflow-wrap:break-word}.dashboard-page .mast p{font-size:15px;line-height:1.5}.dashboard-page .window{display:flex;width:100%;gap:8px;margin-top:24px}.dashboard-page .window>span:not(.freshness){width:100%;padding:0;border:0}.dashboard-page .window .freshness{margin:5px 0 0}.dashboard-page main{padding:24px 0 60px}.dashboard-page section{margin-bottom:18px;padding:22px 18px;border-radius:18px}.dashboard-page .section-head{margin-bottom:22px}.dashboard-page .section-head h2{font-size:24px}.dashboard-page .section-head p{font-size:13px}.dashboard-page .coverage{max-width:none;font-size:10px}.dashboard-page .section-head>div,.dashboard-page .metrics,.dashboard-page .metric,.dashboard-page .coverage,.dashboard-page .table-wrap{min-width:0}.dashboard-page .coverage,.dashboard-page .table-wrap{width:100%;max-width:100%}.dashboard-page .metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.dashboard-page .metrics .primary{grid-column:span 2}.dashboard-page .metric{min-height:126px;padding:17px}.dashboard-page .metric.primary{min-height:142px}.dashboard-page .metric .value{font-size:34px}.dashboard-page .metric .label{margin-top:22px;font-size:11px}.dashboard-page .metric .note{font-size:10px}.dashboard-page .subpanel{padding:18px}.dashboard-page .quadrant{grid-template-columns:1fr auto;gap:8px 14px;margin-bottom:18px}.dashboard-page .quadrant .bar-track{grid-column:1/-1;grid-row:2}.dashboard-page .quadrant .number{grid-column:2;grid-row:1;text-align:right}.dashboard-page table{min-width:980px}.dashboard-page th,.dashboard-page td{padding:12px 13px}.dashboard-page .activity-panel{padding:18px}.dashboard-page .activity-head{display:block;margin-bottom:16px}.dashboard-page .heat-legend{margin-top:12px}.dashboard-page .activity-grid{grid-template-columns:repeat(26,minmax(6px,1fr));gap:4px}.dashboard-page .week-cell{height:20px}.dashboard-page .activity-labels{display:none}.dashboard-page .occasion-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.dashboard-page .occasion{min-height:92px;padding:15px}.dashboard-page .creative-overview{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.dashboard-page .creative-overview-item{min-height:86px;padding:14px}.dashboard-page .creative-brand-card{padding:16px}.dashboard-page .creative-brand-head{display:block}.dashboard-page .creative-status{margin-top:10px}.dashboard-page .creative-strip{display:flex;overflow-x:auto;gap:10px;padding-bottom:4px;scroll-snap-type:x proximity}.dashboard-page .creative-card{flex:0 0 72%;scroll-snap-align:start}.dashboard-page .creative-empty{min-height:110px}.dashboard-page .action{padding:20px}.dashboard-page .method{gap:10px}}
@media print{.dashboard-page{background:#0C0D12}.dashboard-page .mast{background:#0C0D12}.dashboard-page section{box-shadow:none}}
"""

_HERO_READABILITY_CSS = f"""
.hero-page{{padding:44px;background:#000000}}
.hero-sheet{{min-height:1262px;padding:44px 52px;border:1px solid rgba(255,255,255,.10);border-radius:18px;background:#0C0D12;box-shadow:none}}
.hero-page .hero-top{{align-items:center}}
.hero-page .hero-kicker{{color:#FAFBFC;font-size:30px;font-weight:800;letter-spacing:.05em}}
.hero-page .stamp{{padding:9px 14px;border:1px solid rgba(255,255,255,.10);background:#0C0D12;color:#878C96;font-size:32px;font-weight:600;letter-spacing:.04em}}
.hero-page .hero-title{{max-width:900px;margin:58px 0 24px;color:#FAFBFC;font-size:76px;font-weight:800;line-height:.98;text-wrap:balance}}
.hero-page .hero-support.hero-support{{font-size:{_HERO_FEED_COPY_SOURCE_PX}px;line-height:1.1;letter-spacing:-.015em}}
.hero-page .hero-sub.hero-support{{max-width:900px;color:#878C96;text-wrap:pretty}}
.hero-page .accent-rule{{height:4px;margin-top:28px;background:#3D6CFF}}
.hero-page .hero-census{{grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:38px}}
.hero-page .hero-cell{{display:flex;min-height:150px;flex-direction:column;justify-content:center;padding:14px 18px;border:1px solid rgba(255,255,255,.10);border-radius:12px;background:#0C0D12}}
.hero-page .hero-cell b{{color:#FAFBFC;font-size:56px;font-weight:800;line-height:1}}
.hero-page .hero-cell .hero-support.hero-support{{display:block;margin-top:8px;color:#878C96}}
.hero-page .hero-bottom{{align-items:flex-start;gap:32px;padding-top:26px;border-top:1px solid rgba(255,255,255,.10)}}
.hero-page .hero-bottom>div{{flex:1}}
.hero-page .hero-bottom>div:last-child{{max-width:none;text-align:right}}
.hero-page .hero-bottom .hero-support.hero-support{{display:block;color:#878C96}}
.hero-page .hero-bottom strong.hero-support{{color:#FAFBFC;font-weight:700}}
.dashboard-hero.hero-sheet{{padding:30px 34px}}
.dashboard-hero .hero-title{{max-width:920px;margin:26px 0 10px;font-size:60px;line-height:.98}}
.dashboard-hero .hero-sub.hero-support{{max-width:920px}}
.dashboard-hero .accent-rule{{height:4px;margin-top:14px}}
.dashboard-hero .dashboard-product{{margin-top:16px;border-radius:18px}}
.dashboard-hero .dashboard-product .hero-census{{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:0;padding:14px}}
.dashboard-hero .dashboard-product .hero-cell{{min-height:108px;flex-direction:row;align-items:center;justify-content:flex-start;gap:14px;padding:10px 14px}}
.dashboard-hero .dashboard-product .hero-cell b{{font-size:50px}}
.dashboard-hero .dashboard-product .hero-cell .hero-support.hero-support{{margin-top:0}}
.dashboard-hero .update-proof{{display:grid;grid-template-columns:1fr;gap:8px;padding:0 14px 14px}}
.dashboard-hero .update-line{{margin:0;padding:8px 12px;border:1px solid rgba(255,255,255,.10);border-radius:12px;background:#101218;color:#878C96}}
.dashboard-hero .package-grid{{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:0 14px 14px}}
.dashboard-hero .package-item{{display:flex;min-height:82px;align-items:center;padding:8px 12px;border:1px solid rgba(255,255,255,.10);border-radius:12px;background:#0C0D12}}
.dashboard-hero .hero-bottom{{padding-top:18px}}
"""

_REAL_HERO_CSS = r"""
*{box-sizing:border-box}
html,body{width:1080px;height:1350px;margin:0;padding:0;overflow:hidden;background:#000000}
.launch-hero-page{width:1080px;height:1350px;overflow:hidden;padding:38px;background:#000000;color:#FAFBFC;font-family:"Inter Tight",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-weight:400}
.launch-hero-sheet{height:1274px;overflow:hidden;padding:44px 46px 34px;border:1px solid rgba(255,255,255,.10);border-radius:18px;background:#0C0D12;box-shadow:none}
.launch-hero-top{display:flex;align-items:center;justify-content:space-between;gap:24px}
.launch-hero-brand{color:#FAFBFC;font-size:21px;font-weight:800;letter-spacing:.07em;text-transform:uppercase}
.launch-coverage{padding:11px 17px;border:1px solid rgba(255,255,255,.10);border-radius:999px;background:#0C0D12;color:#878C96;font-size:20px;font-weight:600}
.launch-hero-title{max-width:920px;margin:26px 0 8px;color:#FAFBFC;font-size:52px;font-weight:800;letter-spacing:-.045em;line-height:.98;text-wrap:balance}
.candidate-b .launch-hero-title{max-width:900px;font-size:50px}
.launch-hero-sub{max-width:910px;margin:0;color:#878C96;font-size:21px;font-weight:400;line-height:1.25;text-wrap:pretty}
.launch-window{margin-top:8px;color:#878C96;font-size:16px;font-weight:600;font-variant-numeric:tabular-nums}
.launch-stats{display:grid;grid-template-columns:1.05fr 1.2fr .8fr;gap:10px;margin-top:16px}
.launch-stat{min-height:78px;padding:12px 15px;border:1px solid rgba(255,255,255,.10);border-radius:12px;background:#0C0D12}
.launch-stat.primary{border:1px solid rgba(255,255,255,.10);border-left:4px solid #3D6CFF;background:#0C0D12;color:#FAFBFC;box-shadow:none}
.launch-stat b{display:block;color:#FAFBFC;font-size:38px;font-weight:800;letter-spacing:-.04em;line-height:1;font-variant-numeric:tabular-nums}
.launch-stat span,.launch-stat.primary span{display:block;margin-top:6px;color:#878C96;font-size:15px;font-weight:600}
.launch-creatives{margin-top:12px;padding:13px;border:1px solid rgba(255,255,255,.10);border-radius:16px;background:#101218}
.launch-creatives-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:10px}
.launch-creatives-head h2{margin:0;color:#FAFBFC;font-size:20px;font-weight:700;letter-spacing:-.015em}
.launch-creatives-head span{color:#878C96;font-size:13px}
.launch-creative-grid{display:grid;gap:9px}
.candidate-a .launch-creative-grid{grid-template-columns:repeat(7,minmax(0,1fr))}
.candidate-b .launch-creative-grid{grid-template-columns:repeat(3,minmax(0,1fr))}
.launch-creative{overflow:hidden;margin:0;border:1px solid rgba(255,255,255,.10);border-radius:10px;background:#0C0D12}
.launch-creative img{display:block;width:100%;height:116px;object-fit:cover;object-position:top center;background:#161922}
.candidate-b .launch-creative img{height:132px}
.launch-creative figcaption{padding:7px 8px;color:#FAFBFC;font-size:11px;font-weight:600;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.launch-product{margin-top:12px;padding:12px;border:1px solid rgba(255,255,255,.10);border-radius:16px;background:#101218}
.launch-product-head{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;margin-bottom:9px}
.launch-product-head h2{margin:0;color:#FAFBFC;font-size:20px;font-weight:700;letter-spacing:-.02em}
.launch-product-head span{color:#878C96;font-size:13px}
.launch-quadrants{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.launch-quadrant{min-height:82px;padding:10px;border:1px solid rgba(255,255,255,.10);border-radius:10px;background:#0C0D12}
.launch-quadrant b{display:block;color:#FAFBFC;font-size:25px;font-weight:800;letter-spacing:-.03em;line-height:1;font-variant-numeric:tabular-nums}
.launch-quadrant strong{display:block;min-height:29px;margin-top:5px;color:#FAFBFC;font-size:13px;font-weight:600;line-height:1.1}
.launch-quadrant small{display:block;margin-top:2px;color:#878C96;font-size:12px}
.launch-bar{display:block;height:5px;margin-top:6px;overflow:hidden;border-radius:999px;background:rgba(255,255,255,.10)}
.launch-bar i{display:block;height:100%;border-radius:999px;background:#3D6CFF}
.launch-comparison{margin-top:10px;overflow:hidden;border:1px solid rgba(255,255,255,.10);border-radius:16px;background:#0C0D12}
.launch-comparison-head{display:flex;align-items:center;justify-content:space-between;padding:9px 13px;border-bottom:1px solid rgba(255,255,255,.10)}
.launch-comparison-head h2{margin:0;color:#FAFBFC;font-size:18px;font-weight:700;letter-spacing:-.015em}
.launch-comparison-head span{color:#878C96;font-size:12px}
.launch-table-head,.launch-row{display:grid;grid-template-columns:1.25fr .72fr .8fr .8fr 1.05fr;align-items:center;column-gap:9px;padding:6px 13px}
.launch-table-head{background:#101218;color:#878C96;font-size:13px;font-weight:600;letter-spacing:.05em;text-transform:uppercase}
.launch-row{min-height:34px;border-top:1px solid rgba(255,255,255,.10);color:#FAFBFC;font-size:13px}
.launch-row:nth-child(odd){background:#101218}
.launch-row strong{color:#FAFBFC;font-size:14px;font-weight:600}
.launch-row .num{text-align:right;font-variant-numeric:tabular-nums}
.launch-row .posture{display:inline-flex;width:max-content;padding:3px 7px;border:1px solid rgba(255,255,255,.10);border-radius:999px;background:#0C0D12;color:#FAFBFC;font-size:11px;font-weight:600;line-height:1.2}
.launch-row .posture.posture-muted{color:#5C616B}
.launch-footer{display:flex;align-items:center;justify-content:space-between;gap:24px;margin-top:9px;color:#878C96;font-size:12px}
.launch-footer strong{color:#FAFBFC;font-weight:600}
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
            '<span class="bar-track" aria-hidden="true">'
            f'<span class="bar {classes[index]}" style="width:{width:.2f}%"></span>'
            "</span>"
            f'<span class="number">{count:,} / {total:,}<br>{_pct(count,total)}</span>'
            "</div>"
        )
    return "".join(rows)


def derive_dashboard_weekly_activity(
    records: Iterable[Mapping[str, Any]], summary: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Build 52 view-only activity buckets without mutating the census.

    The full observed range is partitioned into 52 contiguous planning weeks.
    A 366-day range therefore contains a small number of 8-day edge buckets.
    The counts come only from qualified broadcasts and must cross-foot to the
    locked broadcast denominator before the dashboard can render them.
    """

    metadata = summary.get("metadata", {})
    try:
        first = date.fromisoformat(str(metadata.get("first_observed") or ""))
        last = date.fromisoformat(str(metadata.get("last_observed") or ""))
    except ValueError as exc:
        raise HeroRenderError("weekly activity requires the observed date window") from exc
    if last < first:
        raise HeroRenderError("weekly activity date window is reversed")
    span_days = (last - first).days + 1
    counts = [0] * 52
    for record in records:
        if str(record.get("scope") or "") != "broadcast":
            continue
        raw = str(record.get("canonical_received_at") or "")
        if not raw:
            raise HeroRenderError("qualified broadcast is missing its receipt timestamp")
        try:
            observed = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError as exc:
            raise HeroRenderError("qualified broadcast has an invalid receipt timestamp") from exc
        if observed < first or observed > last:
            raise HeroRenderError("qualified broadcast falls outside the frozen window")
        index = min(51, ((observed - first).days * 52) // span_days)
        counts[index] += 1

    expected = int(summary.get("broadcast_count", 0))
    if sum(counts) != expected:
        raise HeroRenderError("weekly activity does not cross-foot to qualified broadcasts")

    buckets: list[dict[str, Any]] = []
    for index, count in enumerate(counts):
        start_offset = (index * span_days) // 52
        next_offset = ((index + 1) * span_days) // 52
        bucket_start = first + timedelta(days=start_offset)
        bucket_end = first + timedelta(days=max(start_offset, next_offset - 1))
        buckets.append(
            {
                "start": bucket_start.isoformat(),
                "end": min(bucket_end, last).isoformat(),
                "count": count,
            }
        )
    return buckets


def _activity_heatmap(summary: Mapping[str, Any]) -> str:
    """Render the 52 view-only activity buckets supplied by the real build."""

    raw_buckets = summary.get("_dashboard_weekly_activity", [])
    buckets = [dict(item) for item in raw_buckets if isinstance(item, Mapping)]
    if len(buckets) != 52:
        return ""
    maximum = max((int(item.get("count", 0)) for item in buckets), default=0)
    cells: list[str] = []
    for index, bucket in enumerate(buckets, start=1):
        count = int(bucket.get("count", 0))
        level = min(4, max(1, (count * 4 + maximum - 1) // maximum)) if count and maximum else 0
        label = (
            f"Week {index}: {bucket.get('start')} to {bucket.get('end')}, "
            f"{count:,} qualified broadcasts"
        )
        cells.append(
            f'<span class="week-cell level-{level}" title="{_e(label)}" aria-hidden="true"></span>'
        )
    label_indices = (0, 13, 26, 39, 51)
    labels = "".join(
        f"<span>{_e(str(buckets[index].get('start') or '')[:7])}</span>"
        for index in label_indices
    )
    legend = "".join(
        f'<span class="week-cell level-{level}" aria-hidden="true"></span>'
        for level in range(1, 5)
    )
    return (
        '<div class="activity-panel">'
        '<div class="activity-head"><div>'
        '<h3>52-week activity heatmap</h3>'
        '<p>Qualified broadcast receipts across 52 contiguous planning weeks. '
        "Darker cells mean more observed sends, not better performance.</p>"
        '</div><div class="heat-legend"><span>Lower</span>'
        f'<span class="legend-cells">{legend}</span><span>Higher volume</span></div></div>'
        '<div class="activity-grid" role="img" aria-label="52-week qualified-broadcast activity heatmap. Performance is not inferred.">'
        f"{''.join(cells)}</div>"
        f'<div class="activity-labels" aria-hidden="true">{labels}</div></div>'
    )


def _brand_table(summary: Mapping[str, Any]) -> str:
    rows = []
    for brand in summary.get("brands", []):
        q = brand.get("quadrants", {})
        posture = brand.get("posture", {})
        posture_eligible = (
            int(brand.get("qualified_broadcasts", 0)) >= 30
            and int(brand.get("observed_days", 0)) >= 90
        )
        posture_label = (
            str(posture.get("label") or "Mixed")
            if posture_eligible
            else "Insufficient history"
        )
        posture_class = (
            "posture posture-muted"
            if posture_label == "Insufficient history"
            else "posture"
        )
        rows.append(
            "<tr>"
            f"<td><strong>{_e(brand.get('brand'))}</strong></td>"
            f"<td class=\"num\">{int(brand.get('qualified_broadcasts',0)):,}</td>"
            f"<td class=\"num\">{int(brand.get('observed_days',0)):,}</td>"
            f"<td class=\"num\">{int(q.get('Evergreen content',0)):,}</td>"
            f"<td class=\"num\">{int(q.get('Everyday promotion',0)):,}</td>"
            f"<td class=\"num\">{int(q.get('Seasonal promotion',0)):,}</td>"
            f"<td class=\"num\">{int(q.get('Seasonal content',0)):,}</td>"
            f'<td><span class="{posture_class}">{_e(posture_label)}</span></td>'
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


def _safe_gallery_data_uri(value: Any) -> str | None:
    candidate = str(value or "")
    prefixes = (
        "data:image/png;base64,",
        "data:image/jpeg;base64,",
        "data:image/webp;base64,",
    )
    prefix = next((item for item in prefixes if candidate.startswith(item)), None)
    if prefix is None:
        return None
    try:
        base64.b64decode(candidate[len(prefix) :], validate=True)
    except (ValueError, TypeError):
        return None
    return candidate


def _creative_gallery(summary: Mapping[str, Any]) -> str:
    raw_gallery = summary.get("_creative_gallery")
    if isinstance(raw_gallery, Mapping):
        gallery = dict(raw_gallery)
    elif summary.get("metadata", {}).get("illustrative_prototype"):
        gallery = synthetic_creative_gallery(summary)
    else:
        gallery = unavailable_creative_gallery(summary)

    target_min = GALLERY_TARGET_MIN
    target_max = GALLERY_TARGET_MAX
    rows = [
        dict(row)
        for row in gallery.get("brands", [])
        if isinstance(row, Mapping)
    ]
    total_brands = len(rows)
    brand_cards: list[str] = []
    loaded = ready = insufficient = unavailable = 0
    for row in rows:
        brand = str(row.get("brand") or "Brand unavailable")
        items = [
            dict(item)
            for item in row.get("items", [])
            if isinstance(item, Mapping)
        ][:target_max]
        cards: list[str] = []
        for index, item in enumerate(items, start=1):
            source = _safe_gallery_data_uri(item.get("data_uri"))
            if source is None:
                continue
            item_date, item_category = normalize_creative_metadata(
                item.get("date"), item.get("category")
            )
            metadata = " | ".join(
                value
                for value in (
                    item_date,
                    item_category,
                )
                if value
            ) or "Safe creative preview"
            cards.append(
                '<figure class="creative-card">'
                f'<img src="{html.escape(source, quote=True)}" '
                f'alt="{_e(brand)} safe creative preview {index}" width="320" height="420">'
                f"<figcaption>{_e(metadata)}</figcaption></figure>"
            )
        safe_count = len(cards)
        loaded += safe_count
        if safe_count >= target_min:
            status = "ready"
            ready += 1
            reason = (
                f"{safe_count} safe creative previews available. "
                f"Target: {target_min}-{target_max}."
            )
        elif safe_count:
            status = "insufficient"
            insufficient += 1
            reason = (
                f"{safe_count} of {target_min} minimum safe creative previews "
                "are available."
            )
        else:
            status = "unavailable"
            unavailable += 1
            reason = "No creative preview passed the safe-render gate."
        if safe_count < target_min:
            needed = target_min - safe_count
            placeholder = (
                f"{needed} more safe preview{'s' if needed != 1 else ''} needed "
                f"to reach the {target_min} preview minimum."
                if safe_count
                else "Creative unavailable. No preview passed the safe-render gate."
            )
            cards.append(f'<div class="creative-empty">{_e(placeholder)}</div>')
        status_label = {
            "ready": "Ready",
            "insufficient": "Insufficient",
            "unavailable": "Unavailable",
        }[status]
        brand_cards.append(
            f'<article class="creative-brand-card" data-state="{status}">'
            '<div class="creative-brand-head"><div>'
            f"<h3>{_e(brand)}</h3><p>{_e(reason)}</p></div>"
            f'<span class="creative-status {status}">{status_label}: {safe_count}</span>'
            f'</div><div class="creative-strip">{"".join(cards)}</div></article>'
        )
    if not brand_cards:
        brand_cards.append(
            '<div class="creative-empty">No census brands are available for creative review.</div>'
        )
    overview = (
        '<div class="creative-overview">'
        '<div class="creative-overview-item">'
        f"<b>{loaded:,}</b><span>Validated local creative previews</span></div>"
        '<div class="creative-overview-item">'
        f"<b>{ready:,} / {total_brands:,}</b><span>Brands at the {target_min}-{target_max} preview target</span></div>"
        '<div class="creative-overview-item">'
        f"<b>{insufficient:,}</b><span>Brands with insufficient safe creative</span></div>"
        '<div class="creative-overview-item">'
        f"<b>{unavailable:,}</b><span>Brands with creative unavailable</span></div>"
        "</div>"
    )
    return (
        overview
        + f'<p class="creative-target-note">Target: {target_min}-{target_max} privacy-reviewed previews per brand. Missing previews stay explicit and do not block the rest of the strategy dashboard.</p>'
        + f'<div class="creative-brand-list">{"".join(brand_cards)}</div>'
    )


def render_dashboard(summary: Mapping[str, Any], title: str = "The Competitor Inbox") -> str:
    """Render one complete static document with no executable or remote content."""

    meta = summary.get("metadata", {})
    total = int(summary.get("broadcast_count", 0))
    brands = int(summary.get("brand_count", 0))
    distinct_messages = int(
        summary.get("pipeline", {}).get("distinct_messages")
        or sum(int(value or 0) for value in summary.get("scope_counts", {}).values())
        or total
    )
    q = {row["name"]: row for row in summary.get("quadrants", [])}
    evergreen = int(q.get("Evergreen content", {}).get("count", 0))
    coverage = _coverage(summary)
    body_class = (
        "dashboard-page prototype"
        if meta.get("illustrative_prototype")
        else "dashboard-page"
    )
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
        f"<span>{_e(item.get('label'))}. {int(item.get('numerator',0)):,} of {int(item.get('denominator',0)):,} {_e(item.get('denominator_unit') or 'qualified broadcasts')}.</span>"
        "</div>"
        for item in visible_findings
    )
    occasions = "".join(
        f'<div class="occasion"><b>{int(count):,}</b><span>{_e(name)}</span></div>'
        for name, count in list(summary.get("occasions", {}).items())[:12]
    ) or '<div class="empty">No explicit seasonal occasions met the evidence rule.</div>'
    seasonal_planner = summary.get("seasonal_planner", {})
    seasonal_minimum = int(seasonal_planner.get("minimum_observed_days", 330))
    seasonal_brand_count = int(seasonal_planner.get("eligible_brand_count", 0))
    seasonal_total_brands = int(
        seasonal_planner.get("total_brand_count", brands)
    )
    seasonal_message_count = int(
        seasonal_planner.get("eligible_message_count", 0)
    )
    seasonal_coverage = (
        f"{seasonal_minimum}-day gate | {seasonal_brand_count} of "
        f"{seasonal_total_brands} brands | n={seasonal_message_count:,} broadcasts"
    )
    annual_copy = (
        "Plan against explicit occasions from brands with at least "
        f"{seasonal_minimum} observed days."
        if seasonal_brand_count
        else (
            f"No brand has {seasonal_minimum} observed days yet. Keep collecting "
            "history before prior-season planning."
        )
    )
    generated_date = str(meta.get("generated_at") or "")[:10]
    freshness = f"Fresh as of {generated_date}" if generated_date else "Freshness unavailable"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{_e(_CSP)}"><meta name="referrer" content="no-referrer">
<title>{_e(title)}</title><style>{_inter_tight_font_face()}{_CSS}{_DASHBOARD_POLISH_CSS}</style></head>
<body class="{body_class}"><header class="mast"><div class="shell"><div class="brandline"><span class="brand">ZHS Ecom | Competitive Email Intelligence</span>{_stamp(summary)}</div>
<h1>{_e(title)}</h1><p>A private view of competitor cadence, content mix, offers, and seasonal timing from emails already in an inbox you control.</p>
<div class="window"><span><b>{distinct_messages:,}</b> distinct messages</span><span><b>{total:,}</b> qualified broadcasts</span><span><b>{brands}</b> brands</span><span><b>{_e(_date_window(meta))}</b></span><span class="freshness">{_e(_source_completeness_label(source_completeness))}</span><span class="freshness" role="status">{_e(freshness)}</span></div></div></header>
<main class="shell">
<section aria-labelledby="executive"><div>{_section_head('Executive Brief','The owner-level read on what competitors are sending and where the calendar has room.',coverage)}</div>
<div class="metrics"><div class="metric primary"><span class="value">{total:,}</span><span class="label">Qualified broadcasts</span><span class="note">Lifecycle excluded</span></div>
<div class="metric"><span class="value">{brands}</span><span class="label">Competitors tracked</span><span class="note">One census</span></div>
<div class="metric"><span class="value">{_pct(evergreen,total)}</span><span class="label">Evergreen content</span><span class="note">{evergreen:,} of {total:,}</span></div>
<div class="metric"><span class="value">{int(meta.get('observed_days',0)):,}</span><span class="label">Observed days</span><span class="note">{_e(coverage)}</span></div></div></section>
<section aria-labelledby="comparison">{_section_head('Competitor Comparison','Compare planning mix and strategic posture using qualified broadcasts only.',coverage)}{_brand_table(summary)}</section>
<section aria-labelledby="engine">{_section_head('Evergreen and Promotional Engine','Offer status and seasonality are independent, so the 4-part census stays useful for planning.',coverage)}
<div class="grid-two"><div class="subpanel"><h3>Four-quadrant census</h3>{_quadrant_rows(summary)}</div><div class="subpanel"><h3>What stands out</h3><div class="finding-list">{findings}</div></div></div></section>
<section aria-labelledby="seasonal">{_section_head('Seasonal Planner',annual_copy,seasonal_coverage)}{_activity_heatmap(summary)}<div class="occasion-grid">{occasions}</div></section>
<section aria-labelledby="library">{_section_head('Messaging Library','Review privacy-checked creative previews and recent sanitized subjects. Only broadcasts feed the strategy metrics.',coverage)}
{_creative_gallery(summary)}
<div class="scope-grid"><div class="scope-block"><h3>Broadcast subjects</h3>{_messages(summary,'broadcast')}</div><div class="scope-block"><h3>Lifecycle subjects</h3>{_messages(summary,'lifecycle')}</div><div class="scope-block"><h3>Uncertain subjects</h3>{_messages(summary,'uncertain')}</div></div></section>
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
    """Reject raster corruption in either the light demo or dark launch theme."""

    source = Path(path).expanduser().resolve()
    width, height, bytes_per_pixel, rows = _decode_png_rows(source)
    total_pixels = width * height
    black_pixels = 0
    dark_pixels = 0
    light_pixels = 0
    maximum_black_row = 0
    longest_black_run = 0
    current_black_run = 0
    for row in rows:
        row_black = 0
        for offset in range(0, len(row), bytes_per_pixel):
            red, green, blue = row[offset : offset + 3]
            if max(red, green, blue) <= 12:
                black_pixels += 1
                row_black += 1
            if max(red, green, blue) <= 32:
                dark_pixels += 1
            if min(red, green, blue) >= 220:
                light_pixels += 1
        maximum_black_row = max(maximum_black_row, row_black)
        if row_black / width >= 0.98:
            current_black_run += 1
            longest_black_run = max(longest_black_run, current_black_run)
        else:
            current_black_run = 0
    black_share = black_pixels / total_pixels
    dark_share = dark_pixels / total_pixels
    light_share = light_pixels / total_pixels
    maximum_black_row_share = maximum_black_row / width
    maximum_black_run_share = longest_black_run / height
    light_theme_passed = (
        black_share <= 0.03
        and maximum_black_row_share <= 0.50
        and light_share >= 0.55
    )
    dark_theme_passed = (
        dark_share >= 0.55
        and black_share <= 0.35
        and maximum_black_run_share <= 0.20
        and light_share >= 0.005
    )
    passed = light_theme_passed or dark_theme_passed
    result: dict[str, float | int | bool] = {
        "width": width,
        "height": height,
        "black_pixel_share": round(black_share, 6),
        "dark_pixel_share": round(dark_share, 6),
        "maximum_black_row_share": round(maximum_black_row_share, 6),
        "maximum_black_run_share": round(maximum_black_run_share, 6),
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
        for source in sources:
            profile = temporary_root / f"profile-{source.stem}"
            profile.mkdir(mode=0o700)
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


def _local_time_label(hour: int, minute: int) -> str:
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {suffix} local"


def _hero_update_contract(
    summary: Mapping[str, Any],
    *,
    current_through: Any,
) -> dict[str, Any]:
    metadata = summary.get("metadata", {})
    contract = metadata.get("update_contract", {})
    if not isinstance(contract, Mapping):
        contract = {}
    hour = int(contract.get("schedule_hour_local", SCHEDULE_HOUR_LOCAL))
    minute = int(contract.get("schedule_minute_local", SCHEDULE_MINUTE_LOCAL))
    overlap = int(
        contract.get("incremental_overlap_days", INCREMENTAL_OVERLAP_DAYS)
    )
    return {
        "current_through": str(
            current_through
            or contract.get("current_through")
            or metadata.get("last_observed")
            or "Unavailable"
        ),
        "schedule_hour_local": hour,
        "schedule_minute_local": minute,
        "schedule_label": _local_time_label(hour, minute),
        "incremental_overlap_days": overlap,
        "mac_on_dependency": (
            "Mac must be on or wake"
            if bool(contract.get("requires_mac_on_or_wake", True))
            else "Scheduler host must be available"
        ),
    }


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
            "update_contract": _hero_update_contract(
                summary,
                current_through=brand.get("last_observed", ""),
            ),
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
        "update_contract": _hero_update_contract(
            summary,
            current_through=metadata.get("last_observed", ""),
        ),
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


def _render_real_launch_hero(summary: Mapping[str, Any], variant: str) -> str:
    """Render a multi-brand, screenshot-first view of the frozen real census."""

    metadata = summary.get("metadata", {})
    pipeline = summary.get("pipeline", {})
    scopes = summary.get("scope_counts", {})
    distinct = int(
        pipeline.get("distinct_messages")
        or sum(int(value or 0) for value in scopes.values())
        or summary.get("broadcast_count", 0)
    )
    broadcasts = int(summary.get("broadcast_count", 0))
    brands = int(summary.get("brand_count", 0))
    lifecycle = int(scopes.get("lifecycle", 0) or 0)
    uncertain = int(scopes.get("uncertain", 0) or 0)
    coverage_label = _source_completeness_label(
        str(metadata.get("source_completeness") or "")
    )
    title = (
        f"{distinct:,} competitor emails, mapped into one planning dashboard."
        if variant == "brand"
        else f"See what {brands} competitors sent before you plan the next quarter."
    )
    subtitle = (
        f"{broadcasts:,} qualified broadcasts after {lifecycle:,} lifecycle and "
        f"{uncertain:,} uncertain messages were separated."
        if variant == "brand"
        else (
            f"One strategy view across {distinct:,} distinct messages and "
            f"{broadcasts:,} qualified broadcasts."
        )
    )
    quadrant_cards: list[str] = []
    for row in summary.get("quadrants", []):
        if not isinstance(row, Mapping):
            continue
        count = int(row.get("count", 0))
        width = max(0.2, 100 * count / broadcasts) if broadcasts else 0.2
        quadrant_cards.append(
            '<div class="launch-quadrant">'
            f"<b>{count:,}</b>"
            f"<strong>{_e(row.get('name'))}</strong>"
            f"<small>{_pct(count, broadcasts)}</small>"
            '<span class="launch-bar" aria-hidden="true">'
            f'<i style="width:{width:.2f}%"></i></span></div>'
        )

    comparison_rows: list[str] = []
    for brand in list(summary.get("brands", []))[:5]:
        if not isinstance(brand, Mapping):
            continue
        quadrants = dict(brand.get("quadrants", {}) or {})
        evergreen = int(quadrants.get("Evergreen content", 0))
        promotion = int(quadrants.get("Everyday promotion", 0)) + int(
            quadrants.get("Seasonal promotion", 0)
        )
        posture = str(brand.get("posture", {}).get("label") or "Mixed")
        posture_class = (
            "posture posture-muted"
            if posture == "Insufficient history"
            else "posture"
        )
        comparison_rows.append(
            '<div class="launch-row">'
            f"<strong>{_e(brand.get('brand'))}</strong>"
            f'<span class="num">{int(brand.get("qualified_broadcasts", 0)):,}</span>'
            f'<span class="num">{evergreen:,}</span>'
            f'<span class="num">{promotion:,}</span>'
            f'<span class="{posture_class}">{_e(posture)}</span>'
            "</div>"
        )

    gallery = summary.get("_creative_gallery", {})
    gallery_rows = gallery.get("brands", []) if isinstance(gallery, Mapping) else []
    preferred = (
        ("SKIMS", "Liquid Death", "Act+Acre", "Allbirds", "Athletic Brewing", "Caraway", "Culture Pop")
        if variant == "brand"
        else ("Liquid Death", "Culture Pop", "Athletic Brewing", "Caraway", "Allbirds", "Act+Acre")
    )
    by_name = {
        str(row.get("brand") or ""): row
        for row in gallery_rows
        if isinstance(row, Mapping) and row.get("items")
    }
    ordered_names = [name for name in preferred if name in by_name]
    ordered_names.extend(
        sorted(
            (name for name in by_name if name not in ordered_names),
            key=str.casefold,
        )
    )
    creative_limit = 7 if variant == "brand" else 6
    creative_cards: list[str] = []
    for name in ordered_names[:creative_limit]:
        items = by_name[name].get("items", [])
        item = items[0] if isinstance(items, list) and items else {}
        data_uri = (
            _safe_gallery_data_uri(item.get("data_uri"))
            if isinstance(item, Mapping)
            else None
        )
        if data_uri is None:
            continue
        creative_cards.append(
            '<figure class="launch-creative">'
            f'<img src="{html.escape(data_uri, quote=True)}" '
            f'alt="Privacy-reviewed email creative from {_e(name)}">'
            f'<figcaption>{_e(name)}</figcaption></figure>'
        )
    gallery_status = str(gallery.get("provenance_status") or "") if isinstance(gallery, Mapping) else ""
    creative_surface = (
        '<div class="launch-creatives">'
        '<div class="launch-creatives-head"><h2>Messaging Library</h2>'
        f'<span>{len(creative_cards)} privacy-reviewed local renders</span></div>'
        f'<div class="launch-creative-grid">{"".join(creative_cards)}</div></div>'
        if creative_cards and gallery_status == "verified"
        else ""
    )

    candidate_class = "candidate-a" if variant == "brand" else "candidate-b"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=1080">
    <meta http-equiv="Content-Security-Policy" content="{_e(_CSP)}"><meta name="referrer" content="no-referrer"><title>Competitor Inbox real dashboard</title><style>{_inter_tight_font_face()}{_REAL_HERO_CSS}</style></head>
<body><main class="launch-hero-page"><article class="launch-hero-sheet {candidate_class}" data-census-scope="portfolio-dashboard">
<div class="launch-hero-top"><span class="launch-hero-brand">The Competitor Inbox</span><span class="launch-coverage">{_e(coverage_label)}</span></div>
<h1 class="launch-hero-title">{_e(title)}</h1><p class="launch-hero-sub">{_e(subtitle)}</p><div class="launch-window">{_e(_date_window(metadata))}</div>
<div class="launch-stats"><div class="launch-stat"><b>{distinct:,}</b><span>Distinct messages</span></div><div class="launch-stat primary"><b>{broadcasts:,}</b><span>Qualified broadcasts</span></div><div class="launch-stat"><b>{brands}</b><span>Brands</span></div></div>
{creative_surface}
<div class="launch-product"><div class="launch-product-head"><h2>Evergreen and promotional engine</h2><span>n={broadcasts:,} broadcasts</span></div><div class="launch-quadrants">{''.join(quadrant_cards)}</div></div>
<div class="launch-comparison"><div class="launch-comparison-head"><h2>Competitor comparison</h2><span>Top rows from the real census</span></div>
<div class="launch-table-head"><span>Brand</span><span>Broadcasts</span><span>Evergreen</span><span>Promotion</span><span>Posture</span></div>{''.join(comparison_rows)}</div>
<div class="launch-footer"><strong>Inbox behavior, not performance.</strong><span>Lifecycle excluded from broadcast metrics.</span></div>
</article></main></body></html>"""


def render_hero(summary: Mapping[str, Any], variant: str = "brand") -> str:
    if not summary.get("metadata", {}).get("illustrative_prototype"):
        return _render_real_launch_hero(summary, variant)
    context = _hero_context(summary, variant)
    meta = summary.get("metadata", {})
    total = int(context["total"])
    quadrant_cells = "".join(
        '<div class="hero-cell">'
        f"<b>{int(row.get('count',0)):,}</b>"
        f'<span class="hero-support">{_e(row.get("name"))} | '
        f"{_pct(int(row.get('count',0)),total)}</span></div>"
        for row in context["quadrants"]
    )
    if context["layout"] == "dashboard":
        update = context["update_contract"]
        product_surface = f"""<div class="dashboard-product">
<div class="hero-census">{quadrant_cells}</div>
<div class="update-proof" aria-label="Current through {_e(update['current_through'])}. Update proof. Local scheduler, not cloud uptime">
<p class="update-line hero-support">Daily {_e(update['schedule_label'])} update | {int(update['incremental_overlap_days'])}-day overlap | {_e(update['mac_on_dependency'])}</p>
</div>
<div class="package-grid">
<div class="package-item"><strong class="hero-support">Competitor comparison</strong></div>
<div class="package-item"><strong class="hero-support">Evergreen + promo mix</strong></div>
<div class="package-item"><strong class="hero-support">Seasonal planner</strong></div>
<div class="package-item"><strong class="hero-support">Messaging library + action plan</strong></div>
</div></div>"""
    else:
        product_surface = f'<div class="hero-census">{quadrant_cells}</div>'
    sheet_class = (
        "hero-sheet dashboard-hero"
        if context["layout"] == "dashboard"
        else "hero-sheet poster-hero"
    )
    body_class = "prototype" if meta.get("illustrative_prototype") else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=1080">
<meta http-equiv="Content-Security-Policy" content="{_e(_CSP)}"><meta name="referrer" content="no-referrer"><title>Competitor Inbox hero</title><style>{_inter_tight_font_face()}{_CSS}{_HERO_READABILITY_CSS}</style></head>
<body class="{body_class}"><main class="hero-page"><article class="{sheet_class}" data-census-scope="{_e(context['scope'])}"><div class="hero-top"><span class="hero-kicker">The Competitor Inbox</span>{_stamp(summary)}</div>
<h1 class="hero-title">{_e(context['title'])}</h1><p class="hero-sub hero-support">{_e(context['subtitle'])}</p><div class="accent-rule"></div>{product_surface}
<div class="hero-bottom"><div><strong class="hero-support">{_e(_date_window(context['window']))}</strong><span class="hero-support">{int(context['observed_days']):,} observed days</span></div><div aria-label="{_e(context['coverage_note'])}"><strong class="hero-support">{_e(context['coverage_label'])}</strong></div></div>
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
    update_contract = dict(metadata.get("update_contract", {}) or {})
    seasonal_planner = dict(summary.get("seasonal_planner", {}) or {})
    qualified_broadcasts = int(summary.get("broadcast_count", 0))
    brand_count = int(summary.get("brand_count", 0))
    offer_count = everyday_promotion + seasonal_promotion
    return {
        "raw_messages": int(summary.get("pipeline", {}).get("raw_fetched", 0)),
        "qualified_broadcasts": qualified_broadcasts,
        "brand_count": brand_count,
        "broadcast_brand_count": int(summary.get("broadcast_brand_count", 0)),
        "observed_days": int(metadata.get("observed_days", 0)),
        "update_contract": {
            "current_through": str(update_contract.get("current_through") or ""),
            "schedule_hour_local": int(
                update_contract.get("schedule_hour_local", SCHEDULE_HOUR_LOCAL)
            ),
            "schedule_minute_local": int(
                update_contract.get("schedule_minute_local", SCHEDULE_MINUTE_LOCAL)
            ),
            "incremental_overlap_days": int(
                update_contract.get(
                    "incremental_overlap_days", INCREMENTAL_OVERLAP_DAYS
                )
            ),
            "requires_mac_on_or_wake": bool(
                update_contract.get("requires_mac_on_or_wake", True)
            ),
        },
        "seasonal_planner": {
            "minimum_observed_days": int(
                seasonal_planner.get("minimum_observed_days", 330)
            ),
            "eligible_brand_count": int(
                seasonal_planner.get("eligible_brand_count", 0)
            ),
            "total_brand_count": int(
                seasonal_planner.get("total_brand_count", brand_count)
            ),
            "eligible_message_count": int(
                seasonal_planner.get("eligible_message_count", 0)
            ),
            "eligible_brands": [
                str(value) for value in seasonal_planner.get("eligible_brands", [])
            ],
        },
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
    section_capture_paths: Iterable[str | Path] = (),
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
    section_captures = [
        Path(path).expanduser().resolve() for path in section_capture_paths
    ]
    if section_captures:
        section_names = [path.name for path in section_captures]
        if len(section_names) != len(set(section_names)) or set(section_names) != set(
            _SECTION_CAPTURE_NAMES
        ):
            raise HeroRenderError(
                "freeze requires all 8 canonical real-dashboard section captures"
            )
    section_capture_entries = []
    for path in sorted(section_captures, key=lambda candidate: candidate.name):
        width, height = _png_dimensions(path)
        if width < 1000 or height < 200:
            raise HeroRenderError(
                "frozen dashboard section capture is below the readability floor"
            )
        section_capture_entries.append(
            {
                "path": path.name,
                "sha256": _sha256(path),
                "width": width,
                "height": height,
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
        "hero_html": [
            {
                "path": str(path),
                "sha256": _sha256(path),
                "role": (
                    "primary_dashboard"
                    if path.name == "hero-brand.html"
                    else "secondary_portfolio"
                ),
                "scope": (
                    "portfolio-dashboard"
                    if not summary.get("metadata", {}).get("illustrative_prototype")
                    else "synthetic-demo"
                ),
            }
            for path in heroes
        ],
        "screenshots": screenshot_entries,
        "section_captures": section_capture_entries,
        "window": {
            "first": summary.get("metadata", {}).get("first_observed", ""),
            "last": summary.get("metadata", {}).get("last_observed", ""),
        },
        "qualified_broadcasts": int(summary.get("broadcast_count", 0)),
        "metrics": frozen_metrics,
        "hero_selection": hero_selection(summary),
        "hero_update_contract": dict(
            _hero_context(summary, "brand")["update_contract"]
        ),
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
