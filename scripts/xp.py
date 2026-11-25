#!/usr/bin/env python3
"""xp-kit: levels, badges and a leaderboard for any club, team or community, run on GitHub.

The ledger (data/ledger.jsonl) is the single source of truth: one JSON line per XP event.
Totals, levels and badges are always recomputed from it, never stored by hand.
Everything about how it looks and what earns XP lives in xp-config.yml.

Commands
  build       compute everything, write the static site + badge SVGs, list announcements
  preview     render every badge in every tier into a gallery page (for designing badges)
  check-icon  validate custom icon SVGs in icons/ (or the paths you pass)
  validate    check the config, icons, ledger and members file for problems
  announce    post announcements to Discord (DISCORD_WEBHOOK_XP)
  add         append events to the ledger (manual fixes)
  award       handle an officer's "/award" issue comment
  join        handle a "Join XP" issue form
  dispatch    handle an xp-event sent from another repo (repository_dispatch)
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH: Path | None = None   # set by --config
USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

PALETTE = {  # name: (mid, light)
    "red": ("#E24B4A", "#F09595"),
    "orange": ("#E0712B", "#F4A774"),
    "amber": ("#D08A1E", "#F2B95A"),
    "yellow": ("#C9B21C", "#EADB6A"),
    "green": ("#1D9E75", "#5DCAA5"),
    "teal": ("#1BA3A3", "#7FD6D6"),
    "blue": ("#378ADD", "#85B7EB"),
    "purple": ("#7F77DD", "#AFA9EC"),
    "pink": ("#D4537E", "#ED93B1"),
    "gray": ("#5F5E5A", "#888780"),
}
METALS = [("bronze", "#C08457"), ("silver", "#C9C7BF"), ("gold", "#F2C14E")]
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"
SHAPES = ("shield", "hex", "coin")
METRICS = ("count", "streak", "joined_before")
THEME_DEFAULTS = {
    "accent": "#5DCAA5", "bg": "#07090C", "panel": "#0B0F14", "line": "#1C2128",
    "text": "#D6D9DE", "muted": "#8B9098", "badge_bg": "#0B0F14",
}


# ============================================================================ config

class ConfigError(Exception):
    pass


def mix(hex_color: str, other: str, t: float) -> str:
    a = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(a, b))


def color_pair(c: str) -> tuple[str, str]:
    """Named palette color or #RRGGBB -> (mid, light)."""
    c = str(c).strip()
    if c in PALETTE:
        return PALETTE[c]
    if HEX_RE.match(c):
        return c.upper(), mix(c.upper(), "#FFFFFF", 0.45)
    raise ConfigError(f"unknown color `{c}`: use one of {', '.join(PALETTE)} or a hex like #3FA9F5")


def load_config(root: Path) -> dict:
    path = CONFIG_PATH or root / "xp-config.yml"
    try:
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        sys.exit(f"{path.name} isn't valid YAML{where}: {getattr(e, 'problem', e)}.\n"
                 "Common causes: a missing space after a colon (`key:{` should be `key: {`), "
                 "tabs instead of spaces, or an unquoted value containing `: ` or `#`.")
    if not isinstance(cfg, dict):
        sys.exit(f"{path.name} should be a set of `key: value` settings at the top level")
    for key, kind in (("group", dict), ("theme", dict), ("sources", dict), ("badges", dict), ("levels", list),
                      ("seasons", list), ("officers", list)):
        if cfg.get(key) is not None and not isinstance(cfg[key], kind):
            sys.exit(f"{path.name}: `{key}` should be a {'list' if kind is list else 'section of settings'}")
    for section in ("sources", "badges"):
        for k, v in (cfg.get(section) or {}).items():
            if not isinstance(v, dict):
                sys.exit(f"{path.name}: {section[:-1]} `{k}` should be a block like `{{ xp: 10, ... }}`. "
                         "Check for a missing space after the colon (`name:{` should be `name: {`).")
    for i, l in enumerate(cfg.get("levels") or []):
        if not isinstance(l, dict) or "name" not in l or not isinstance(l.get("min"), int):
            sys.exit(f"{path.name}: level #{i + 1} needs a name and a whole-number min, e.g. `{{ name: rookie, min: 0, color: gray }}`")
    g = cfg.setdefault("group", {})
    g.setdefault("name", "My Group")
    g.setdefault("handle", re.sub(r"[^a-z0-9]+", "_", g["name"].lower()).strip("_")[:24] or "xp")
    g.setdefault("tagline", "earn xp by showing up and getting involved. levels and badges update automatically.")
    owner, repo = str(g.get("owner", "")), str(g.get("repo", "xp"))
    g["owner"], g["repo"] = owner, repo
    if not g.get("site_url"):
        g["site_url"] = f"https://{owner.lower()}.github.io/{repo}" if owner else ""
    g["site_url"] = str(g["site_url"]).rstrip("/")
    g.setdefault("home_url", "")
    cfg["theme"] = {**THEME_DEFAULTS, **(cfg.get("theme") or {})}
    cfg["theme"].setdefault("shape", "shield")
    cfg.setdefault("officers", [])
    cfg["seasons"] = cfg.get("seasons") or []
    for s in cfg["seasons"]:
        s["start"], s["end"] = str(s["start"]), str(s["end"])
    cfg.setdefault("sources", {})
    cfg["sources"].setdefault("correction", {"xp": None, "unique": False, "label": "correction"})
    cfg.setdefault("levels", [{"name": "member", "min": 0, "color": "gray"}])
    cfg.setdefault("badges", {})
    for b in cfg["badges"].values():
        if "date" in b:
            b["date"] = str(b["date"])
        b.setdefault("desc", "")
        b.setdefault("tiers", [1])
    return cfg


def config_problems(root: Path, cfg: dict) -> list[str]:
    """Friendly checks, written for people editing the config by hand or with an LLM."""
    p = []
    g = cfg["group"]
    if not g["owner"]:
        p.append("group.owner is empty: set it to the GitHub user or org that owns this repo")
    if len(g["handle"]) > 24:
        p.append("group.handle must be 24 characters or fewer (it has to fit on the level badge)")
    for k in ("accent", "bg", "panel", "line", "text", "muted", "badge_bg"):
        if not HEX_RE.match(str(cfg["theme"].get(k, ""))):
            p.append(f"theme.{k} must be a hex color like #5DCAA5")
    if cfg["theme"]["shape"] not in SHAPES:
        p.append(f"theme.shape must be one of {', '.join(SHAPES)}")
    if not cfg["officers"]:
        p.append("officers is empty: nobody will be able to /award XP")
    for name, s in cfg["sources"].items():
        if not SLUG_RE.match(name):
            p.append(f"source `{name}`: use lowercase letters, numbers, _ or -")
        if s.get("xp") is not None and not isinstance(s.get("xp"), int):
            p.append(f"source `{name}`: xp must be a whole number or null")
    lv = cfg["levels"]
    if lv[0].get("min") != 0:
        p.append("the first level must have min: 0")
    for i, l in enumerate(lv):
        try:
            color_pair(l.get("color", "gray"))
        except ConfigError as e:
            p.append(f"level `{l.get('name')}`: {e}")
        if i and l["min"] <= lv[i - 1]["min"]:
            p.append(f"level `{l['name']}`: min must be higher than the level before it")
    for name, b in cfg["badges"].items():
        where = f"badge `{name}`"
        if not SLUG_RE.match(name):
            p.append(f"{where}: name must be lowercase letters, numbers, _ or - (max 32)")
        try:
            color_pair(b.get("color", "gray"))
        except ConfigError as e:
            p.append(f"{where}: {e}")
        if b.get("shape", cfg["theme"]["shape"]) not in SHAPES:
            p.append(f"{where}: shape must be one of {', '.join(SHAPES)}")
        icon = b.get("icon", "star")
        if icon not in BUILTIN_ICONS and not (root / "icons" / f"{icon}.svg").exists():
            p.append(f"{where}: icon `{icon}` is not built in and icons/{icon}.svg doesn't exist")
        if b.get("metric") not in METRICS:
            p.append(f"{where}: metric must be one of {', '.join(METRICS)}")
        if b.get("metric") == "count":
            for s in b.get("sources", []) or []:
                if s not in cfg["sources"]:
                    p.append(f"{where}: counts source `{s}` which isn't defined under sources")
            if not b.get("sources"):
                p.append(f"{where}: count badges need a `sources` list")
        if b.get("metric") == "streak" and "attendance" not in cfg["sources"]:
            p.append(f"{where}: streak badges need an `attendance` source")
        if b.get("metric") == "joined_before" and not re.match(r"^\d{4}-\d{2}-\d{2}$", b.get("date", "")):
            p.append(f"{where}: joined_before badges need `date: YYYY-MM-DD`")
        t = b.get("tiers", [])
        if not t or len(t) > 3 or any(not isinstance(x, int) or x < 1 for x in t) or t != sorted(set(t)):
            p.append(f"{where}: tiers must be 1-3 increasing whole numbers, e.g. [1] or [3, 10, 25]")
    return p


# ============================================================================ data

def load_members(data: Path) -> dict:
    f = data / "members.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text() or "{}").get("members", {})


def save_members(data: Path, members: dict) -> None:
    (data / "members.json").write_text(json.dumps({"members": dict(sorted(members.items()))}, indent=2) + "\n")


def load_ledger(data: Path) -> list[dict]:
    f = data / "ledger.jsonl"
    if not f.exists():
        return []
    out = []
    for n, line in enumerate(f.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            print(f"warning: ledger line {n} is not valid JSON, skipped", file=sys.stderr)
            continue
        e["_line"] = n
        out.append(e)
    return out


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class XPError(Exception):
    pass


def add_events(root: Path, data: Path, users: list[str], source: str, xp=None, ref: str = "",
               by: str = "manual") -> list[str]:
    """Validate and append events. Returns human-readable result lines."""
    cfg = load_config(root)
    members, ledger = load_members(data), load_ledger(data)
    if source not in cfg["sources"]:
        raise XPError(f"unknown source `{source}`. Valid: {', '.join(cfg['sources'])}")
    spec = cfg["sources"][source]
    if xp is None:
        xp = spec.get("xp")
    if xp is None:
        raise XPError(f"`{source}` needs an XP amount, e.g. `/award @user {source} 50 reason`")
    xp = int(xp)
    cap = int(cfg.get("max_award", 500))
    if xp < 0 and source != "correction":
        raise XPError("negative XP is only allowed with the `correction` source")
    if abs(xp) > cap:
        raise XPError(f"refusing a single award over {cap} XP; split it or raise max_award in the config")
    if spec.get("unique") and not ref:
        raise XPError(f"`{source}` needs a ref (an id, date or link) so it can't be double-counted")
    seen = {(e["user"].lower(), e["source"], e.get("ref", "")) for e in ledger}
    results, new = [], []
    for u in users:
        u = u.lstrip("@")
        if not USER_RE.match(u):
            results.append(f"- `{u[:40]}`: not a valid GitHub username, skipped")
            continue
        key = next((m for m in members if m.lower() == u.lower()), None)
        if key is None:
            results.append(f"- `{u}`: hasn't joined yet (they need to open a Join XP issue), skipped")
            continue
        if spec.get("unique") and (key.lower(), source, ref) in seen:
            results.append(f"- `{key}`: already has `{source}` for `{ref}`, skipped")
            continue
        new.append({"ts": now_iso(), "user": key, "xp": xp, "source": source, "ref": ref, "by": by})
        seen.add((key.lower(), source, ref))
        results.append(f"- `{key}`: {xp:+d} XP ({source}{' · ' + ref if ref else ''})")
    if new:
        with open(data / "ledger.jsonl", "a") as f:
            for e in new:
                f.write(json.dumps(e) + "\n")
    return results


# ============================================================================ compute

def current_season(cfg: dict, today: str) -> dict | None:
    cur = None
    for s in sorted(cfg["seasons"], key=lambda s: s["start"]):
        if s["start"] <= today:
            cur = s
    return cur


def max_streak(user_dates: set[str], sessions: list[str]) -> int:
    best = run = 0
    for d in sessions:
        run = run + 1 if d in user_dates else 0
        best = max(best, run)
    return best


def compute(cfg: dict, members: dict, ledger: list[dict], today: str | None = None) -> dict:
    today = today or dt.date.today().isoformat()
    season = current_season(cfg, today)
    unique_sources = {k for k, v in cfg["sources"].items() if v.get("unique")}
    seen = set()
    per = defaultdict(lambda: {"xp": 0, "season_xp": 0, "counts": defaultdict(int), "att": set(), "events": []})
    sessions = set()
    for e in ledger:
        u = e.get("user")
        if u not in members or e.get("source") not in cfg["sources"]:
            continue
        key = (u, e["source"], e.get("ref", ""))
        if e["source"] in unique_sources:
            if key in seen:
                continue
            seen.add(key)
        p = per[u]
        p["xp"] += int(e["xp"])
        if season and season["start"] <= e["ts"][:10] <= season["end"]:
            p["season_xp"] += int(e["xp"])
        p["counts"][e["source"]] += 1
        p["events"].append(e)
        if e["source"] == "attendance" and e.get("ref"):
            p["att"].add(e["ref"])
            sessions.add(e["ref"])
    sessions = sorted(sessions)
    levels = cfg["levels"]
    users = {}
    for u, m in members.items():
        p = per[u]
        xp = max(0, p["xp"])
        li = max(i for i, l in enumerate(levels) if xp >= l["min"])
        badges = {}
        for bname, b in cfg["badges"].items():
            if b["metric"] == "count":
                val = sum(p["counts"][s] for s in b["sources"])
            elif b["metric"] == "streak":
                val = max_streak(p["att"], sessions)
            elif b["metric"] == "joined_before":
                val = 1 if m.get("joined", "9999")[:10] <= b["date"] else 0
            else:
                val = 0
            badges[bname] = {"tier": sum(1 for t in b["tiers"] if val >= t), "value": val}
        users[u] = {"xp": xp, "season_xp": max(0, p["season_xp"]), "level": li, "badges": badges,
                    "events": sorted(p["events"], key=lambda e: e["ts"], reverse=True)}
    return {"users": users, "season": season}


# ============================================================================ icons
# Icons live in a 32x32 box centered on 0,0 (viewBox="-16 -16 32 32").
# They are drawn in `currentColor`, which the build swaps for the badge color.

def _star(r1=13, r2=5.5):
    pts = []
    for i in range(10):
        r = r1 if i % 2 == 0 else r2
        a = math.radians(-90 + i * 36)
        pts.append(f"{r * math.cos(a):.1f},{r * math.sin(a) + 1:.1f}")
    return " ".join(pts)


_S = 'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"'
_F = 'fill="currentColor" fill-opacity="0.25"'
BUILTIN_ICONS = {
    "drop": f'<path d="M0 -14C11 -1 11 13 0 13C-11 13 -11 -1 0 -14Z" {_F} {_S}/>',
    "prompt": (f'<polyline points="-10,-8 -3,-1 -10,6" fill="none" {_S}/>'
               f'<line x1="0" y1="7" x2="10" y2="7" {_S}>'
               '<animate attributeName="opacity" values="1;1;0;0" dur="1.1s" repeatCount="indefinite"/></line>'),
    "flag": (f'<line x1="-7" y1="-13" x2="-7" y2="13" {_S}/>'
             f'<polygon points="-7,-12 10,-6 -7,1" {_F} {_S}/>'),
    "bars": "".join(f'<rect x="{x}" y="{y}" width="4" height="{h}" fill="currentColor"/>'
                    for x, y, h in [(-12, 2, 9), (-6, -2, 13), (0, -7, 18), (6, -13, 24)]),
    "crosshair": (f'<circle r="9" fill="none" {_S}/><path d="M0 -15V-5M0 5V15M-15 0H-5M5 0H15" fill="none" {_S}/>'
                  '<circle r="1.8" fill="currentColor"/>'),
    "nodes": (f'<path d="M0 -9L-9 8M0 -9L9 8M-9 8H9" fill="none" {_S} stroke-opacity="0.6"/>'
              f'<circle cy="-9" r="4" fill="none" {_S}/><circle cx="-9" cy="8" r="4" fill="none" {_S}/>'
              f'<circle cx="9" cy="8" r="4" fill="none" {_S}/>'),
    "star": f'<polygon points="{_star()}" {_F} {_S}/>',
    "bolt": f'<polygon points="3,-15 -9,2 -1,2 -3,15 9,-2 1,-2" {_F} {_S}/>',
    "book": (f'<path d="M0 -8C-4 -11 -9 -11 -13 -9V10C-9 8 -4 8 0 11C4 8 9 8 13 10V-9C9 -11 4 -11 0 -8Z" {_F} {_S}/>'
             f'<line x1="0" y1="-8" x2="0" y2="11" {_S}/>'),
    "code": (f'<polyline points="-6,-8 -13,0 -6,8" fill="none" {_S}/><polyline points="6,-8 13,0 6,8" fill="none" {_S}/>'
             f'<line x1="3" y1="-12" x2="-3" y2="12" {_S}/>'),
    "heart": f'<path d="M0 12C-14 3 -14 -9 -7 -10C-3 -11 -1 -8 0 -6C1 -8 3 -11 7 -10C14 -9 14 3 0 12Z" {_F} {_S}/>',
    "trophy": (f'<path d="M-8 -12H8V-3C8 3 4 6 0 6C-4 6 -8 3 -8 -3Z" {_F} {_S}/>'
               f'<path d="M-8 -9H-12V-6C-12 -2 -10 0 -8 0M8 -9H12V-6C12 -2 10 0 8 0M0 6V10M-6 13H6" fill="none" {_S}/>'),
    "wrench": f'<path d="M5 -13A7 7 0 0 0 -2 -4L-12 6A3 3 0 0 0 -8 10L2 0A7 7 0 0 0 11 -7L6 -6L5 -9Z" {_F} {_S}/>',
    "mic": (f'<rect x="-5" y="-14" width="10" height="17" rx="5" {_F} {_S}/>'
            f'<path d="M-10 -2C-10 5 -5 8 0 8C5 8 10 5 10 -2M0 8V13M-5 13H5" fill="none" {_S}/>'),
    "chat": f'<path d="M-12 -10H12V5H-2L-8 11V5H-12Z" {_F} {_S}/>',
    "rocket": (f'<path d="M0 -15C6 -10 7 -2 5 6H-5C-7 -2 -6 -10 0 -15Z" {_F} {_S}/>'
               f'<path d="M-5 1L-10 7V10L-5 7M5 1L10 7V10L5 7M-2 10L0 14L2 10" fill="none" {_S}/>'
               '<circle cy="-5" r="2.2" fill="currentColor"/>'),
    "lock": (f'<path d="M-7 -1V-6A7 7 0 0 1 7 -6V-1" fill="none" {_S}/>'
             f'<rect x="-10" y="-1" width="20" height="14" rx="2" fill="none" {_S}/>'
             '<circle cy="6" r="1.6" fill="currentColor"/>'),
}

ICON_ELEMENTS = {"svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon"}
ICON_ATTRS = {
    "viewBox", "d", "cx", "cy", "r", "rx", "ry", "x", "y", "width", "height", "x1", "y1", "x2", "y2", "points",
    "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-opacity", "fill-opacity",
    "opacity", "transform", "fill-rule", "stroke-dasharray",
}
NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9 ,.\-+#()]*$")
PAINT_OK = {"none", "currentColor"}


class IconError(Exception):
    pass


def sanitize_icon(text: str, name: str = "icon") -> str:
    """Parse an LLM- or hand-made SVG and rebuild it from an allowlist. Anything unexpected is an error,
    not silently dropped, so the author sees what to fix."""
    if len(text) > 20_000:
        raise IconError("file is over 20 KB; simplify the drawing")
    # LLMs often wrap the SVG in a code fence or a sentence: keep only the <svg>...</svg> part.
    start, end = text.find("<svg"), text.rfind("</svg>")
    if start == -1 or end == -1:
        raise IconError("no <svg>...</svg> found")
    text = text[start:end + 6]
    low = text.lower()
    for bad in ("<!doctype", "<!entity", "<script", "javascript:", "<foreignobject", "<style", "xlink:href", "href="):
        if bad in low:
            raise IconError(f"contains `{bad}`, which isn't allowed in icons")
    try:
        root = ET.fromstring(text.strip())
    except ET.ParseError as e:
        raise IconError(f"not valid SVG/XML: {e}")

    def local(tag: str) -> str:
        return tag.split("}", 1)[-1]

    if local(root.tag) != "svg":
        raise IconError("the top element must be <svg>")
    try:
        vx, vy, vw, vh = (float(v) for v in (root.get("viewBox") or "").replace(",", " ").split())
    except ValueError:
        raise IconError('the <svg> needs a viewBox, e.g. viewBox="-16 -16 32 32" or "0 0 24 24"')
    if vw <= 0 or vh <= 0 or not (0.8 <= vw / vh <= 1.25):
        raise IconError("the viewBox must be (roughly) square")
    count = 0

    def walk(el, depth=0) -> str:
        nonlocal count
        count += 1
        if count > 200:
            raise IconError("more than 200 elements; simplify the drawing")
        if depth > 6:
            raise IconError("groups are nested too deeply")
        tag = local(el.tag)
        if tag not in ICON_ELEMENTS:
            raise IconError(f"<{tag}> isn't allowed (allowed: {', '.join(sorted(ICON_ELEMENTS - {'svg'}))})")
        attrs = []
        for k, v in el.attrib.items():
            k = local(k)
            if tag == "svg" and k in ("xmlns", "version", "width", "height", "class", "id", "viewBox"):
                continue
            if k not in ICON_ATTRS:
                raise IconError(f"attribute `{k}` on <{tag}> isn't allowed")
            v = v.strip()
            if k in ("fill", "stroke") and v.lower() == "currentcolor":
                v = "currentColor"
            if k in ("fill", "stroke") and v not in PAINT_OK:
                raise IconError(f'{k}="{v}" on <{tag}>: use "currentColor" or "none" so the badge color applies')
            if k == "transform" and not re.fullmatch(r"(\s*(translate|scale|rotate)\([-0-9., ]+\)\s*)+", v):
                raise IconError(f"transform `{v}` isn't allowed (use translate, scale or rotate)")
            if k == "d" and not re.fullmatch(r"[MmLlHhVvCcSsQqTtAaZz0-9.,\-+eE \n\t]*", v):
                raise IconError(f"path data on <{tag}> contains unexpected characters")
            if not SAFE_VALUE.match(v.replace("\n", " ").replace("\t", " ")):
                raise IconError(f"value of `{k}` on <{tag}> contains unexpected characters")
            if k in ("cx", "cy", "r", "rx", "ry", "x", "y", "width", "height", "x1", "y1", "x2", "y2",
                     "stroke-width", "opacity", "fill-opacity", "stroke-opacity") and not NUMERIC.match(v):
                raise IconError(f"`{k}` on <{tag}> must be a plain number")
            attrs.append(f'{k}="{html.escape(v, quote=True)}"')
        if (el.text or "").strip():
            raise IconError(f"<{tag}> contains text; icons must be shapes only (no <text>)")
        inner = "".join(walk(c, depth + 1) for c in el)
        if tag == "svg":
            # Fit any square viewBox into the badge's 30x30 icon area, keeping root paint attributes.
            k = 30 / max(vw, vh)
            cx, cy = vx + vw / 2, vy + vh / 2
            a = (" " + " ".join(attrs)) if attrs else ""
            return f'<g transform="scale({k:.4g}) translate({-cx:.4g},{-cy:.4g})"{a}>{inner}</g>' if inner else ""
        a = (" " + " ".join(attrs)) if attrs else ""
        return f"<{tag}{a}>{inner}</{tag}>" if inner else f"<{tag}{a}/>"

    out = walk(root)
    if not out:
        raise IconError("the icon is empty")
    return out


def icon_markup(root: Path, icon: str, cache: dict) -> str:
    if icon in cache:
        return cache[icon]
    f = root / "icons" / f"{icon}.svg"
    if f.exists():
        try:
            cache[icon] = sanitize_icon(f.read_text(), icon)
        except IconError as e:
            print(f"warning: icons/{icon}.svg rejected ({e}); using the star icon", file=sys.stderr)
            cache[icon] = BUILTIN_ICONS["star"]
    else:
        cache[icon] = BUILTIN_ICONS.get(icon, BUILTIN_ICONS["star"])
    return cache[icon]


# ============================================================================ SVG: badges

def _hex_pts(r: float) -> str:
    return " ".join(f"{r * math.cos(math.radians(-90 + 60 * i)):.1f},{r * math.sin(math.radians(-90 + 60 * i)):.1f}"
                    for i in range(6))


SHIELD = "M-22 -32H22L28 -26V2C28 20 12 30 0 38C-12 30 -28 20 -28 2V-26Z"
SHIELD_IN = "M-17 -26H17L22 -21V1C22 16 9 24 0 31C-9 24 -22 16 -22 1V-21Z"


def shape_parts(shape: str):
    """-> (outer(attrs), inner(attrs), frame(attrs), edge_x)"""
    if shape == "hex":
        return (lambda a: f'<polygon points="{_hex_pts(34)}" {a}/>',
                lambda a: f'<polygon points="{_hex_pts(27)}" {a}/>',
                lambda a: f'<polygon points="{_hex_pts(38.5)}" {a}/>', 29.4)
    if shape == "coin":
        return (lambda a: f'<circle r="31" {a}/>', lambda a: f'<circle r="25" {a}/>',
                lambda a: f'<circle r="35" {a}/>', 29.3)
    return (lambda a: f'<path d="{SHIELD}" {a}/>', lambda a: f'<path d="{SHIELD_IN}" {a}/>',
            lambda a: f'<path d="{SHIELD}" transform="translate(0,-0.6) scale(1.12)" {a}/>', 28)


GLOW = ('<defs><filter id="glow" x="-30%" y="-30%" width="160%" height="160%">'
        '<feGaussianBlur stdDeviation="1.4" result="b"/>'
        '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>')


def badge_svg(cfg: dict, name: str, badge: dict, tier: int, icon_svg: str, lock_svg: str,
              title: str = "", compact: bool = False) -> str:
    """120x160 standalone badge (compact: 120x124, no label). tier 0 = locked."""
    locked = tier == 0
    M, L = PALETTE["gray"] if locked else color_pair(badge.get("color", "gray"))
    bg = cfg["theme"]["badge_bg"]
    outer, inner, frame, ex = shape_parts(badge.get("shape", cfg["theme"]["shape"]))
    metal = METALS[min(tier, 3) - 1][1] if (tier and len(badge["tiers"]) > 1) else None
    h = 124 if compact else 160
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="{h}" viewBox="0 0 120 {h}" role="img">',
             f'<title>{html.escape(title or name)}</title>', GLOW,
             '<g transform="translate(60,64) scale(1.5)">',
             f'<path d="M{ex} -10H{ex + 5}L{ex + 8} -13" fill="none" stroke="{M}" stroke-width="1"/>'
             f'<circle cx="{ex + 9}" cy="-14" r="1.4" fill="none" stroke="{M}" stroke-width="0.9"/>']
    if not locked:
        parts.append(f'<path d="M{-ex} 6H{-ex - 5}L{-ex - 8} 9" fill="none" stroke="{M}" stroke-width="1"/>'
                     f'<circle cx="{-ex - 9}" cy="10" r="1.4" fill="none" stroke="{M}" stroke-width="0.9"/>')
    if metal:
        parts.append(frame(f'fill="none" stroke="{metal}" stroke-width="1"'))
    parts.append(outer(f'fill="{bg}" stroke="{M}" stroke-width="1.5"'
                       + (' stroke-dasharray="5 4"' if locked else ' filter="url(#glow)"')))
    if not locked:
        parts.append(inner(f'fill="none" stroke="{L}" stroke-opacity="0.4" stroke-width="0.8" stroke-dasharray="1.5 2"'))
    parts.append(f'<g transform="translate(0,1)" color="{L}" style="color:{L}">{lock_svg if locked else icon_svg}</g>')
    parts.append("</g>")
    if compact:
        return "".join(parts) + "</svg>"
    parts.append(f'<text x="60" y="140" text-anchor="middle" font-family="{MONO}" font-size="12" '
                 f'fill="{L if locked else M}">{html.escape(name)}</text>')
    if locked:
        parts.append(f'<text x="60" y="155" text-anchor="middle" font-family="{MONO}" font-size="10" '
                     f'fill="{PALETTE["gray"][0]}">locked</text>')
    elif metal:
        for i in range(3):
            cx = 48 + i * 12
            parts.append(f'<polygon points="{cx},146 {cx + 4},150 {cx},154 {cx - 4},150" '
                         f'fill="{metal if i < tier else "none"}" stroke="{metal}" stroke-width="1"/>')
    parts.append("</svg>")
    return "".join(parts)


def level_svg(cfg: dict, xp: int, li: int) -> str:
    levels = cfg["levels"]
    lvl = levels[li]
    nxt = levels[li + 1] if li + 1 < len(levels) else None
    M, L = color_pair(lvl.get("color", "gray"))
    t = cfg["theme"]
    handle = cfg["group"]["handle"][:24]
    cursor_x = 16 + 7.23 * (len(handle) + 2) + 2
    frac = 1.0 if not nxt else min(1.0, xp / nxt["min"])
    filled = round(frac * 20)
    segs = "".join(f'<rect x="{16 + i * 17.4:.1f}" y="29" width="14" height="6" '
                   f'fill="{M if i < filled else mix(t["badge_bg"], "#FFFFFF", 0.1)}"/>' for i in range(20))
    bottom_l = f"{xp}/{nxt['min']} xp" if nxt else f"{xp} xp"
    bottom_r = f"next: {html.escape(nxt['name'])}" if nxt else "max level"
    frame = "M8.75 0.75H379.25V55.25L371.25 63.25H0.75V8.75Z"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="380" height="64" viewBox="0 0 380 64" role="img">'
        f'<title>{html.escape(cfg["group"]["name"])}: {html.escape(lvl["name"])}, {xp} XP</title>'
        '<defs><filter id="glow" x="-10%" y="-30%" width="120%" height="160%">'
        '<feGaussianBlur stdDeviation="1.3" result="b"/>'
        '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
        '<pattern id="scan" width="4" height="3" patternUnits="userSpaceOnUse">'
        '<rect width="4" height="1" fill="#ffffff" fill-opacity="0.03"/></pattern></defs>'
        f'<path d="{frame}" fill="{t["badge_bg"]}" stroke="{M}" stroke-width="1.5" filter="url(#glow)"/>'
        f'<path d="{frame}" fill="url(#scan)"/>'
        f'<text x="16" y="21" font-family="{MONO}" font-size="12" fill="{t["accent"]}">&gt; {html.escape(handle)}</text>'
        f'<rect x="{cursor_x:.1f}" y="11" width="7" height="12" fill="{t["accent"]}">'
        '<animate attributeName="opacity" values="1;1;0;0" dur="1.1s" repeatCount="indefinite"/></rect>'
        f'<text x="364" y="21" text-anchor="end" font-family="{MONO}" font-size="12" fill="{L}">'
        f'lvl.{li + 1:02d} {html.escape(lvl["name"])}</text>'
        f'<g filter="url(#glow)">{segs}</g>'
        f'<text x="16" y="52" font-family="{MONO}" font-size="11" fill="#888780">{bottom_l}</text>'
        f'<text x="364" y="52" text-anchor="end" font-family="{MONO}" font-size="11" fill="#888780">{bottom_r}</text>'
        '</svg>')


# ============================================================================ site

CSS = """
:root{--bg:%(bg)s;--panel:%(panel)s;--line:%(line)s;--text:%(text)s;--muted:%(muted)s;--accent:%(accent)s;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:var(--mono);font-size:14px;line-height:1.6}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:960px;margin:0 auto;padding:32px 16px 64px}
header{border:1px solid var(--line);background:var(--panel);padding:18px 20px;margin-bottom:24px;
clip-path:polygon(10px 0,100%% 0,100%% calc(100%% - 10px),calc(100%% - 10px) 100%%,0 100%%,0 10px)}
header h1{margin:0;font-size:20px;font-weight:500}header h1 span{color:var(--accent)}
header p{margin:4px 0 0;color:var(--muted);font-size:13px}
.tabs{display:flex;gap:8px;margin-bottom:12px}.tabs button{font:inherit;background:var(--panel);color:var(--muted);
border:1px solid var(--line);padding:6px 14px;cursor:pointer}.tabs button.on{color:var(--accent);border-color:var(--accent)}
table{width:100%%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line)}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:middle}
th{color:var(--muted);font-weight:400;font-size:12px}td.n{color:var(--muted);width:40px}
.u{display:flex;align-items:center;gap:10px}.u img{width:28px;height:28px;border-radius:4px;background:var(--line)}
td.xp{text-align:right;color:var(--accent);white-space:nowrap}.mini img{width:31px;height:32px;margin-right:3px}
.lvl{font-size:12px;padding:1px 8px;border:1px solid}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:12px;margin:12px 0 24px}
.grid figure{margin:0;text-align:center}.grid figcaption{color:var(--muted);font-size:11px}
pre{background:var(--panel);border:1px solid var(--line);padding:12px;overflow-x:auto;font-size:12px;white-space:pre-wrap;word-break:break-all}
.feed{list-style:none;padding:0;margin:0}.feed li{border-bottom:1px solid var(--line);padding:6px 0;color:var(--muted)}
.feed b{color:var(--text);font-weight:400}.empty{color:var(--muted);padding:24px;text-align:center}
h2{font-size:15px;font-weight:500;margin:28px 0 8px}h2::before{content:"// ";color:var(--muted)}
button.copy{font:inherit;font-size:12px;background:none;border:1px solid var(--line);color:var(--muted);padding:3px 10px;cursor:pointer}
nav.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;font-size:12px}
nav.top a.home{color:var(--muted)}
a.cta{display:inline-block;color:var(--bg);background:var(--accent);padding:7px 16px;font-size:13px;
clip-path:polygon(6px 0,100%% 0,100%% calc(100%% - 6px),calc(100%% - 6px) 100%%,0 100%%,0 6px)}a.cta:hover{text-decoration:none;filter:brightness(1.1)}
.join{border:1px solid var(--accent);background:var(--panel);padding:18px 20px;margin:24px 0}
.join h3{margin:0 0 4px;font-size:15px;font-weight:500;color:var(--accent)}.join>p{margin:0 0 12px;color:var(--muted);font-size:13px}
.steps{list-style:none;padding:0;margin:0 0 16px;counter-reset:s}
.steps li{counter-increment:s;padding:6px 0 6px 40px;position:relative;border-bottom:1px dashed var(--line)}
.steps li::before{content:counter(s,decimal-leading-zero);position:absolute;left:0;color:var(--accent)}
.steps li span{display:block;color:var(--muted);font-size:12px}
.callout{color:var(--accent);font-size:13px;margin:0 0 14px}.fine{color:var(--muted);font-size:12px;margin:12px 0 0}
footer{margin-top:48px;color:var(--muted);font-size:11px;text-align:center}
@media(max-width:640px){.mini{display:none}th.mini{display:none}}
"""


def join_url(cfg: dict) -> str:
    g = cfg["group"]
    return f"https://github.com/{g['owner']}/{g['repo']}/issues/new?template=join-xp.yml"


def page(cfg: dict, title: str, body: str, depth: int = 0) -> str:
    g = cfg["group"]
    up = "../" * depth
    home = g.get("home_url", "")
    nav = ('<nav class="top">'
           + (f'<a class="home" href="{html.escape(home)}">&lt;- home</a>' if home else "<span></span>")
           + (f'<a class="cta" href="{html.escape(join_url(cfg))}">join xp</a>' if g["owner"] else "")
           + "</nav>")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>{CSS % cfg["theme"]}</style></head><body><div class="wrap">'
            f'{nav}<header><h1><a href="{up}" style="color:inherit">&gt; <span>{html.escape(g["handle"])}</span> // xp</a></h1>'
            f'<p>{html.escape(g["tagline"])}</p></header>{body}'
            '<footer>powered by <a href="https://github.com/cyber-tech-kev/xp-kit">xp-kit</a></footer>'
            '</div></body></html>')


def join_panel(cfg: dict, today: str) -> str:
    callout = ""
    for bname, b in cfg["badges"].items():
        if b.get("metric") == "joined_before" and b.get("date", "") >= today:
            d = dt.date.fromisoformat(b["date"])
            callout = (f'<p class="callout">[*] join before {d.strftime("%b")} {d.day} to unlock the '
                       f'{html.escape(bname)} badge. it won\'t be earnable after that.</p>')
            break
    return (
        '<section class="join" id="join"><h3>&gt; how to join</h3>'
        '<p>takes about two minutes. all you need is a free GitHub account.</p>' + callout +
        '<ol class="steps">'
        '<li>make a free GitHub account<span>already have one? skip this. sign up at '
        '<a href="https://github.com/signup">github.com/signup</a>. students can also get the free '
        '<a href="https://education.github.com/pack">GitHub Student Developer Pack</a>.</span></li>'
        '<li>open the join form<span>click the button below while signed in to GitHub.</span></li>'
        '<li>fill it out and submit<span>tick the box, then click <b>Create</b>.</span></li>'
        '<li>you\'re in<span>a bot adds you within a minute and replies with your profile link. '
        'you\'ll show up on the board a minute or two later.</span></li>'
        '<li>show it off<span>your profile page has a snippet that puts your live level and badges '
        'on your GitHub profile.</span></li></ol>'
        f'<a class="cta" href="{html.escape(join_url(cfg))}">open the join form -&gt;</a>'
        '<p class="fine">joining puts your GitHub username, xp and badges on this public page. '
        'want off the board later? ask an organizer.</p></section>')


def board_rows(cfg, users, key) -> str:
    ranked = sorted(users.items(), key=lambda kv: (-kv[1][key], kv[0].lower()))
    if not ranked:
        return '<div class="empty">nobody here yet. <a href="#join">join</a> and be the first.</div>'
    rows = []
    for i, (u, d) in enumerate(ranked, 1):
        lvl = cfg["levels"][d["level"]]
        M, L = color_pair(lvl.get("color", "gray"))
        ue = html.escape(u)
        mini = "".join(f'<img src="badges/{ue}/{b}-mini.svg" alt="{b}" title="{b}">'
                       for b, v in d["badges"].items() if v["tier"])
        rows.append(
            f'<tr><td class="n">{i:02d}</td><td><div class="u"><img src="https://github.com/{ue}.png?size=56" alt="">'
            f'<a href="u/{ue}/">{ue}</a></div></td>'
            f'<td><span class="lvl" style="color:{L};border-color:{M}">{html.escape(lvl["name"])}</span></td>'
            f'<td class="mini">{mini}</td><td class="xp">{d[key]} xp</td></tr>')
    return ('<table><thead><tr><th>#</th><th>member</th><th>level</th><th class="mini">badges</th>'
            '<th style="text-align:right">xp</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>")


def event_line(cfg, e) -> str:
    label = cfg["sources"].get(e["source"], {}).get("label", e["source"])
    ref = f' · {html.escape(e["ref"])}' if e.get("ref") else ""
    return f'<li>{e["ts"][:10]} <b>{e["xp"]:+d} xp</b> {html.escape(label)}{ref}</li>'


def render_badges(root: Path, cfg: dict) -> dict:
    """-> {badge_name: icon markup} with custom icons sanitized once."""
    cache: dict = {}
    return {b: icon_markup(root, v.get("icon", "star"), cache) for b, v in cfg["badges"].items()}


def build_site(root: Path, cfg: dict, result: dict, ledger: list[dict], out: Path, today: str) -> None:
    if out.exists():
        shutil.rmtree(out)
    (out / "badges" / "catalog").mkdir(parents=True)
    users = result["users"]
    site = cfg["group"]["site_url"]
    icons = render_badges(root, cfg)
    lock = BUILTIN_ICONS["lock"]

    for bname, b in cfg["badges"].items():
        for t in range(0, len(b["tiers"]) + 1):
            (out / "badges" / "catalog" / f"{bname}-t{t}.svg").write_text(
                badge_svg(cfg, bname, b, t, icons[bname], lock))
    for i, l in enumerate(cfg["levels"]):
        (out / "badges" / "catalog" / f"level-{i + 1}.svg").write_text(level_svg(cfg, l["min"], i))

    for u, d in users.items():
        bdir = out / "badges" / u
        bdir.mkdir(parents=True)
        (bdir / "level.svg").write_text(level_svg(cfg, d["xp"], d["level"]))
        figs = []
        for bname, b in cfg["badges"].items():
            v = d["badges"][bname]
            (bdir / f"{bname}.svg").write_text(badge_svg(cfg, bname, b, v["tier"], icons[bname], lock, f"{u}: {bname}"))
            (bdir / f"{bname}-mini.svg").write_text(
                badge_svg(cfg, bname, b, v["tier"], icons[bname], lock, f"{u}: {bname}", compact=True))
            nxt = next((t for t in b["tiers"] if v["value"] < t), None)
            prog = f"{v['value']}/{nxt} to next" if nxt else "maxed"
            figs.append(f'<figure><img src="../../badges/{u}/{bname}.svg" alt="{bname}" width="120" height="160">'
                        f'<figcaption>{html.escape(b["desc"])}<br>{prog}</figcaption></figure>')
        earned = [b for b, v in d["badges"].items() if v["tier"]]
        embed = (f"[![{cfg['group']['name']} level]({site}/badges/{u}/level.svg)]({site}/u/{u}/)\n"
                 + " ".join(f"[![{b}]({site}/badges/{u}/{b}.svg)]({site}/u/{u}/)" for b in earned))
        feed = "".join(event_line(cfg, e) for e in d["events"][:15]) or "<li>no activity yet</li>"
        ue = html.escape(u)
        season_txt = f' · {d["season_xp"]} xp this season' if result["season"] else ""
        body = (f'<p><img src="../../badges/{ue}/level.svg" alt="level" width="380" height="64" style="max-width:100%"></p>'
                f'<p style="color:var(--muted)">{ue} · {d["xp"]} xp all-time{season_txt}</p>'
                f'<h2>badges</h2><div class="grid">{"".join(figs)}</div>'
                f'<h2>show it off</h2><p style="color:var(--muted)">paste this into your GitHub profile README. '
                f'it updates on its own.</p><pre id="e">{html.escape(embed)}</pre>'
                '<button class="copy" onclick="navigator.clipboard.writeText(document.getElementById(\'e\').innerText);'
                'this.textContent=\'copied\'">copy</button>'
                f'<h2>recent activity</h2><ul class="feed">{feed}</ul>')
        udir = out / "u" / u
        udir.mkdir(parents=True)
        (udir / "index.html").write_text(page(cfg, f"{u} · {cfg['group']['name']} XP", body, depth=2))

    season = result["season"]
    recent = sorted((e for e in ledger if e.get("user") in users), key=lambda e: e["ts"], reverse=True)[:12]
    feed = "".join(f'<li><a href="u/{html.escape(e["user"])}/">{html.escape(e["user"])}</a> '
                   + event_line(cfg, e)[4:] for e in recent) or "<li>nothing yet</li>"
    catalog = "".join(
        f'<figure><img src="badges/catalog/{b}-t{len(v["tiers"])}.svg" alt="{b}" width="120" height="160">'
        f'<figcaption>{html.escape(v["desc"])}<br>{" / ".join(map(str, v["tiers"]))}</figcaption></figure>'
        for b, v in cfg["badges"].items())
    ladder = "".join(f'<li><b>lvl.{i + 1:02d} {html.escape(l["name"])}</b> · {l["min"]}+ xp</li>'
                     for i, l in enumerate(cfg["levels"]))
    earn = "".join(f'<li><b>{html.escape(v.get("label", k))}</b> · {v["xp"] if v.get("xp") is not None else "varies"} xp</li>'
                   for k, v in cfg["sources"].items() if k != "correction")
    if season:
        board = (f'<div class="tabs"><button class="on" data-t="sea">{html.escape(season["name"])}</button>'
                 '<button data-t="all">all-time</button></div>'
                 f'<div id="t-sea">{board_rows(cfg, users, "season_xp")}</div>'
                 f'<div id="t-all" hidden>{board_rows(cfg, users, "xp")}</div>'
                 '<script>document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>{'
                 'document.querySelectorAll(".tabs button").forEach(x=>x.classList.toggle("on",x===b));'
                 'document.getElementById("t-sea").hidden=b.dataset.t!=="sea";'
                 'document.getElementById("t-all").hidden=b.dataset.t!=="all"})</script>')
    else:
        board = board_rows(cfg, users, "xp")
    body = (board + (join_panel(cfg, today) if cfg["group"]["owner"] else "")
            + f'<h2>latest</h2><ul class="feed">{feed}</ul>'
            f'<h2>badges</h2><div class="grid">{catalog}</div>'
            f'<h2>levels</h2><ul class="feed">{ladder}</ul>'
            f'<h2>how to earn</h2><ul class="feed">{earn}</ul>')
    (out / "index.html").write_text(page(cfg, f"{cfg['group']['name']} XP", body))
    (out / ".nojekyll").write_text("")


def state_of(result: dict) -> dict:
    return {"users": {u: {"xp": d["xp"], "level": d["level"], "badges": {b: v["tier"] for b, v in d["badges"].items()}}
                      for u, d in result["users"].items()}}


def diff_announcements(cfg: dict, prev: dict, new: dict) -> list[str]:
    if "users" not in prev:
        return []  # very first deploy: nothing to compare against
    site = cfg["group"]["site_url"]
    msgs = []
    for u, n in new["users"].items():
        p = prev["users"].get(u)
        link = f"[{u}](<{site}/u/{u}/>)"
        if p is None:
            msgs.append(f"`[+]` {link} joined the XP board. welcome!")
            p = {"level": 0, "badges": {}}
        if n["level"] > p["level"]:
            l = cfg["levels"][n["level"]]
            msgs.append(f"`[^]` {link} leveled up to **lvl.{n['level'] + 1:02d} {l['name']}** ({n['xp']} xp)")
        for b, t in n["badges"].items():
            if b in cfg["badges"] and t > p["badges"].get(b, 0):
                tiers = cfg["badges"][b]["tiers"]
                tier_name = f" ({METALS[t - 1][0]})" if len(tiers) > 1 else ""
                msgs.append(f"`[*]` {link} unlocked **{b}**{tier_name}")
    return msgs


# ============================================================================ commands

def data_dir(a) -> Path:
    return Path(a.data_dir) if a.data_dir else ROOT / "data"


def cmd_build(a):
    cfg = load_config(ROOT)
    probs = config_problems(ROOT, cfg)
    if probs:
        sys.exit("config problems:\n- " + "\n- ".join(probs))
    data = data_dir(a)
    members, ledger = load_members(data), load_ledger(data)
    today = a.today or dt.date.today().isoformat()
    result = compute(cfg, members, ledger, today)
    out = Path(a.out)
    build_site(ROOT, cfg, result, ledger, out, today)
    state = state_of(result)
    (out / "state.json").write_text(json.dumps(state, indent=1))
    prev = {}
    if a.prev_state and Path(a.prev_state).exists():
        prev = json.loads(Path(a.prev_state).read_text() or "{}")
    elif a.prev_url:
        try:
            with urllib.request.urlopen(a.prev_url, timeout=15) as r:
                prev = json.loads(r.read())
        except Exception as ex:
            print(f"note: no previous state ({ex}); skipping announcements", file=sys.stderr)
    msgs = diff_announcements(cfg, prev, state)
    if a.announce_file:
        Path(a.announce_file).write_text(json.dumps(msgs, indent=1))
    print(f"built {out} · {len(members)} members · {len(ledger)} events · {len(msgs)} announcements")


def cmd_preview(a):
    """Gallery of every badge in every tier and every level badge, for designing."""
    cfg = load_config(ROOT)
    probs = [p for p in config_problems(ROOT, cfg)
             if not p.startswith(("group.owner is empty", "officers is empty"))]  # setup steps, not design issues
    for p in probs:
        print(f"warning: {p}", file=sys.stderr)
    icons = render_badges(ROOT, cfg)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for bname, b in cfg["badges"].items():
        cells = "".join(f'<figure>{badge_svg(cfg, bname, b, t, icons[bname], BUILTIN_ICONS["lock"])}'
                        f'<figcaption>{"not earned yet" if t == 0 else (METALS[t - 1][0] if len(b["tiers"]) > 1 else "earned")}'
                        f'{"" if t == 0 else " · " + str(b["tiers"][t - 1]) + "+"}'
                        '</figcaption></figure>' for t in range(len(b["tiers"]) + 1))
        rows.append(f'<h2>{html.escape(bname)}</h2><p style="color:var(--muted)">{html.escape(b["desc"])}</p>'
                    f'<div class="grid">{cells}</div>')
    levels = "".join(f'<p>{level_svg(cfg, l["min"], i)}</p>' for i, l in enumerate(cfg["levels"]))
    builtins = "".join(
        f'<figure><svg width="48" height="48" viewBox="-16 -16 32 32" style="color:var(--accent)">{m}</svg>'
        f'<figcaption>{k}</figcaption></figure>' for k, m in BUILTIN_ICONS.items())
    body = ("".join(rows) + f'<h2>levels</h2>{levels}'
            f'<h2>built-in icons</h2><div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(80px,1fr))">'
            f'{builtins}</div>')
    if probs:
        body = '<pre>' + html.escape("config problems:\n- " + "\n- ".join(probs)) + '</pre>' + body
    (out / "index.html").write_text(page(cfg, "badge preview", body))
    print(f"preview written to {out / 'index.html'}")


def cmd_check_icon(a):
    files = [Path(p) for p in a.paths] or sorted((ROOT / "icons").glob("*.svg"))
    if not files:
        print("no icons to check (put custom icons in icons/<name>.svg)")
        return
    bad = 0
    for f in files:
        try:
            sanitize_icon(f.read_text(), f.stem)
            print(f"ok    {f}")
        except IconError as e:
            bad += 1
            print(f"FAIL  {f}: {e}")
    sys.exit(1 if bad else 0)


def cmd_validate(a):
    cfg = load_config(ROOT)
    data = data_dir(a)
    members, ledger = load_members(data), load_ledger(data)
    problems = config_problems(ROOT, cfg)
    for f in sorted((ROOT / "icons").glob("*.svg")):
        try:
            sanitize_icon(f.read_text(), f.stem)
        except IconError as e:
            problems.append(f"icons/{f.name}: {e}")
    for e in ledger:
        for k in ("ts", "user", "xp", "source"):
            if k not in e:
                problems.append(f"ledger line {e['_line']}: missing `{k}`")
        if e.get("source") not in cfg["sources"]:
            problems.append(f"ledger line {e['_line']}: unknown source {e.get('source')}")
        if e.get("user") not in members:
            print(f"note: ledger line {e['_line']}: {e.get('user')} is not a member (ignored in totals)")
    for p in problems:
        print(f"problem: {p}")
    print(f"{len(problems)} problem(s)")
    sys.exit(1 if problems else 0)


def cmd_announce(a):
    msgs = json.loads(Path(a.file).read_text()) if Path(a.file).exists() else []
    hook = os.environ.get("DISCORD_WEBHOOK_XP", "")
    if not msgs:
        print("nothing to announce")
        return
    if not hook:
        print("DISCORD_WEBHOOK_XP not set; would have posted:\n" + "\n".join(msgs))
        return
    chunks, cur = [], ""
    for m in msgs[:40]:
        if len(cur) + len(m) + 1 > 1900:
            chunks.append(cur)
            cur = ""
        cur += m + "\n"
    if cur:
        chunks.append(cur)
    for c in chunks:
        req = urllib.request.Request(hook, data=json.dumps({"content": c, "allowed_mentions": {"parse": []}}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "xp-kit"})
        urllib.request.urlopen(req, timeout=15)
    print(f"posted {len(msgs)} announcements")


def cmd_add(a):
    try:
        lines = add_events(ROOT, data_dir(a), a.user.split(","), a.source, a.xp, a.ref or "", a.by)
    except XPError as ex:
        sys.exit(f"error: {ex}")
    print("\n".join(lines))


AWARD_HELP = ("usage: `/award @user [@user2 ...] <source> [xp] [ref or note]`\n"
              "examples:\n- `/award @alex @sam attendance 2026-11-18`\n"
              "- `/award @alex award 50 ran the workshop`\n"
              "- `/award @alex correction -25 duplicate entry`")


def cmd_award(a):
    cfg = load_config(ROOT)
    text = Path(a.body_file).read_text().strip()
    body = text.splitlines()[0] if text else ""
    reply = []
    if a.actor.lower() not in {o.lower() for o in cfg["officers"]}:
        reply.append(f"@{a.actor} only organizers listed under `officers:` in `xp-config.yml` can award XP.")
    else:
        toks = body.split()[1:]
        users = [t[1:] for t in toks if t.startswith("@")]
        rest = [t for t in toks if not t.startswith("@")]
        if not users or not rest:
            reply.append(AWARD_HELP)
        else:
            source, xp, ref_toks = rest[0], None, rest[1:]
            if ref_toks and re.fullmatch(r"[+-]?\d+", ref_toks[0]):
                xp = int(ref_toks[0])
                ref_toks = ref_toks[1:]
            try:
                reply += add_events(ROOT, data_dir(a), users, source, xp, " ".join(ref_toks)[:200], by=a.actor)
            except XPError as ex:
                reply.append(f"error: {ex}\n\n{AWARD_HELP}")
    Path(a.reply_file).write_text("\n".join(reply) + "\n")
    print("\n".join(reply))


def _form_field(body: str, heading: str) -> str:
    m = re.search(rf"###\s*{re.escape(heading)}\s*\n+(.*?)(?=\n###|\Z)", body, re.S)
    v = m.group(1).strip() if m else ""
    return "" if v in ("_No response_", "None") else v


def cmd_join(a):
    cfg = load_config(ROOT)
    data = data_dir(a)
    body = Path(a.body_file).read_text()
    user, members = a.user, load_members(data)
    site = cfg["group"]["site_url"]
    if not re.search(r"-\s*\[[xX]\]\s*I understand", body):
        msg = "You need to tick the consent box so your username and XP can show on the public board. Open a new Join XP issue and tick it."
    elif user in members:
        msg = f"@{user} you're already on the board: {site}/u/{user}/"
    else:
        handle = re.sub(r"[^\w.#-]", "", _form_field(body, "Discord username"))[:40]
        members[user] = {"joined": now_iso(), **({"discord": handle} if handle else {})}
        save_members(data, members)
        msg = (f"Welcome to the board, @{user}! Your profile appears here within a few minutes: {site}/u/{user}/\n\n"
               "Your profile page has a copy-paste snippet that puts your live level and badges on your GitHub profile.")
    Path(a.reply_file).write_text(msg + "\n")
    print(msg)


def cmd_dispatch(a):
    p = json.loads(Path(a.payload_file).read_text() or "{}")
    users = p.get("users") or ([p["user"]] if p.get("user") else [])
    try:
        lines = add_events(ROOT, data_dir(a), [str(u) for u in users], str(p.get("source", "")), p.get("xp"),
                           str(p.get("ref", ""))[:200], by=f"dispatch:{a.sender}")
    except XPError as ex:
        sys.exit(f"error: {ex}")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", help="defaults to ./data")
    ap.add_argument("--config", help="defaults to ./xp-config.yml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", default="_site")
    b.add_argument("--prev-state")
    b.add_argument("--prev-url")
    b.add_argument("--announce-file")
    b.add_argument("--today")
    b.set_defaults(fn=cmd_build)
    pv = sub.add_parser("preview")
    pv.add_argument("--out", default="_preview")
    pv.set_defaults(fn=cmd_preview)
    ci = sub.add_parser("check-icon")
    ci.add_argument("paths", nargs="*")
    ci.set_defaults(fn=cmd_check_icon)
    sub.add_parser("validate").set_defaults(fn=cmd_validate)
    n = sub.add_parser("announce")
    n.add_argument("--file", default="announce.json")
    n.set_defaults(fn=cmd_announce)
    d = sub.add_parser("add")
    d.add_argument("--user", required=True, help="comma-separated")
    d.add_argument("--source", required=True)
    d.add_argument("--xp", type=int)
    d.add_argument("--ref")
    d.add_argument("--by", default="manual")
    d.set_defaults(fn=cmd_add)
    w = sub.add_parser("award")
    w.add_argument("--actor", required=True)
    w.add_argument("--body-file", required=True)
    w.add_argument("--reply-file", default="reply.md")
    w.set_defaults(fn=cmd_award)
    j = sub.add_parser("join")
    j.add_argument("--user", required=True)
    j.add_argument("--body-file", required=True)
    j.add_argument("--reply-file", default="reply.md")
    j.set_defaults(fn=cmd_join)
    s = sub.add_parser("dispatch")
    s.add_argument("--payload-file", required=True)
    s.add_argument("--sender", default="unknown")
    s.set_defaults(fn=cmd_dispatch)
    a = ap.parse_args()
    global CONFIG_PATH
    CONFIG_PATH = Path(a.config) if a.config else None
    a.fn(a)


if __name__ == "__main__":
    main()
