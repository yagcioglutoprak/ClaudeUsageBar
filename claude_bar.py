#!/usr/bin/env python3
"""
Claude Usage Menu Bar — macOS status bar app

Sections shown (matching claude.ai/settings/usage):
  1. Plan usage limits → Current session
  2. Weekly limits     → All models + Sonnet only
  3. Extra usage       → toggle status

Setup:
  pip install rumps requests
  python3 claude_bar.py
"""

import rumps
from curl_cffi import requests  # Chrome TLS fingerprint — bypasses Cloudflare
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
import json
import os
import subprocess
import tempfile
import threading
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ── logging ──────────────────────────────────────────────────────────────────

LOG_FILE = os.path.expanduser("~/.claude_bar.log")
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

CONFIG_FILE = os.path.expanduser("~/.claude_bar_config.json")
REFRESH_INTERVAL = 300  # 5 minutes


# ── config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# ── data models ───────────────────────────────────────────────────────────────

@dataclass
class LimitRow:
    label: str
    pct: int          # 0–100
    reset_str: str    # e.g. "resets in 1h 23m" or "resets Thu 00:00"


@dataclass
class UsageData:
    session: LimitRow | None = None
    weekly_all: LimitRow | None = None
    weekly_sonnet: LimitRow | None = None
    overages_enabled: bool | None = None
    raw: dict = field(default_factory=dict)


# ── claude.ai API ─────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://claude.ai/settings/usage",
    "Origin": "https://claude.ai",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


def parse_cookie_string(raw: str) -> dict:
    """Parse 'key=val; key2=val2' or just a bare sessionKey value."""
    raw = raw.strip()
    if "=" not in raw:
        # bare value — assume it's the sessionKey
        return {"sessionKey": raw}
    cookies = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = v.strip()
    return cookies


def _get(url: str, cookies: dict) -> dict | list:
    r = requests.get(
        url, cookies=cookies, headers=HEADERS, timeout=15,
        impersonate="chrome131",  # match Chrome TLS fingerprint (JA3/JA4)
    )
    log.debug("GET %s  status=%s  body=%s", url, r.status_code, r.text[:800])
    r.raise_for_status()
    return r.json()


def _org_id_from_cookies(cookies: dict) -> str | None:
    """claude.ai stores the active org UUID in the lastActiveOrg cookie."""
    return cookies.get("lastActiveOrg") or cookies.get("routingHint")


def _org_id_from_api(cookies: dict) -> str | None:
    """Fallback: try known account/bootstrap endpoints."""
    for path in (
        "/api/organizations",
        "/api/bootstrap",
        "/api/auth/current_account",
        "/api/account",
    ):
        try:
            data = _get(f"https://claude.ai{path}", cookies)
            # /api/organizations returns a list
            if isinstance(data, list) and data:
                return data[0].get("id") or data[0].get("uuid")
            if isinstance(data, dict):
                # try common nested shapes
                for candidate in (
                    data.get("organization_id"),
                    data.get("org_id"),
                    (data.get("organizations") or [{}])[0].get("id"),
                    (data.get("account", {}).get("memberships") or [{}])[0]
                        .get("organization", {}).get("id"),
                ):
                    if candidate:
                        return candidate
        except Exception as e:
            log.debug("endpoint %s failed: %s", path, e)
    return None


def fetch_raw(cookie_str: str) -> dict:
    """Return raw combined payload: {rate_limits, org_id}."""
    cookies = parse_cookie_string(cookie_str)
    log.debug("using cookies keys: %s", list(cookies.keys()))

    org_id = _org_id_from_cookies(cookies)
    log.debug("org_id from cookie: %s", org_id)

    if not org_id:
        org_id = _org_id_from_api(cookies)
        log.debug("org_id from api: %s", org_id)

    if not org_id:
        raise ValueError(
            "Could not find organization id.\n"
            "Make sure you copied ALL cookies (including lastActiveOrg)."
        )

    usage = _get(
        f"https://claude.ai/api/organizations/{org_id}/usage", cookies
    )
    log.debug("usage full response: %s", json.dumps(usage, indent=2))
    return {"usage": usage, "org_id": org_id}


# ── time helpers ──────────────────────────────────────────────────────────────

_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _fmt_reset(val) -> str:
    if val is None:
        return ""
    try:
        if isinstance(val, (int, float)):
            dt = datetime.fromtimestamp(val, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = dt - now
        secs = delta.total_seconds()
        if secs <= 0:
            return "resets soon"
        if secs < 3600 * 20:
            h, rem = divmod(int(secs), 3600)
            m = rem // 60
            if h > 0:
                return f"resets in {h}h {m}m"
            return f"resets in {m}m"
        # Far future → show weekday + time
        day = _DAYS[dt.weekday()]
        return f"resets {day} {dt.strftime('%H:%M')}"
    except Exception:
        log.debug("_fmt_reset failed for %r", val, exc_info=True)
        return str(val)[:20]


# ── parser ────────────────────────────────────────────────────────────────────

def _row(data: dict, key: str, label: str) -> LimitRow | None:
    """Extract a LimitRow from a known key in the usage response."""
    bucket = data.get(key)
    if not bucket or not isinstance(bucket, dict):
        return None
    pct = min(100, round(float(bucket.get("utilization", 0))))
    reset = _fmt_reset(bucket.get("resets_at"))
    return LimitRow(label, pct, reset)


def parse_usage(raw: dict) -> UsageData:
    """
    API response shape (confirmed):
      five_hour        → Plan usage limits / Current session
      seven_day        → Weekly limits / All models
      seven_day_sonnet → Weekly limits / Sonnet only
      extra_usage      → Extra usage toggle (null = off)
    """
    u = raw.get("usage", {})
    extra = u.get("extra_usage")
    overages = bool(extra) if extra is not None else None

    return UsageData(
        session=_row(u, "five_hour", "Current Session"),
        weekly_all=_row(u, "seven_day", "All Models"),
        weekly_sonnet=_row(u, "seven_day_sonnet", "Sonnet Only"),
        overages_enabled=overages,
        raw=raw,
    )


# ── display helpers ───────────────────────────────────────────────────────────

def _bar(pct: int, width: int = 14) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _row_lines(row: LimitRow) -> list[str]:
    bar = _bar(row.pct)
    return [
        f"  {row.label}",
        f"  {bar}  {row.pct}%",
        f"  {row.reset_str}" if row.reset_str else "",
    ]


def _mi(title: str) -> rumps.MenuItem:
    """Disabled (display-only) menu item."""
    item = rumps.MenuItem(title)
    item.set_callback(None)
    return item


# ── native macOS dialogs via osascript ───────────────────────────────────────

def _ask_text(title: str, prompt: str, default: str = "") -> str | None:
    """Show a native macOS text-input dialog. Returns text or None if cancelled."""
    script = (
        f'display dialog "{prompt}" '
        f'default answer "{default}" '
        f'with title "{title}" '
        f'buttons {{"Cancel", "Save"}} default button "Save"'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return None  # cancelled
        out = result.stdout.strip()
        if "text returned:" in out:
            return out.split("text returned:")[-1].strip()
    except Exception:
        log.exception("_ask_text failed")
    return None


def _show_text(title: str, text: str):
    """Write text to a temp file and open it in TextEdit."""
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False,
            prefix="claude_usage_raw_"
        )
        tmp.write(text)
        tmp.close()
        subprocess.Popen(["open", "-a", "TextEdit", tmp.name])
    except Exception:
        log.exception("_show_text failed")


# ── app ───────────────────────────────────────────────────────────────────────

class ClaudeBar(rumps.App):
    def __init__(self):
        super().__init__("◆", quit_button=None)
        self.config = load_config()
        self._last_raw: dict = {}
        self._rebuild_menu(None)

        self._timer = rumps.Timer(self._on_timer, REFRESH_INTERVAL)
        self._timer.start()

        if self.config.get("cookie_str"):
            self._schedule_fetch()

    # ── menu ─────────────────────────────────────────────────────────────────

    def _rebuild_menu(self, data: UsageData | None):
        # Always create fresh MenuItem objects — rumps forbids reuse across menus
        items: list = []

        if data is None or not any([data.session, data.weekly_all, data.weekly_sonnet]):
            items += [_mi("No data — set session cookie first"), None]
        else:
            # ── Section 1: Plan usage limits ────────────────────────────
            items.append(_mi("PLAN USAGE LIMITS"))
            items.append(None)
            if data.session:
                for line in _row_lines(data.session):
                    if line:
                        items.append(_mi(line))
                items.append(None)

            # ── Section 2: Weekly limits ────────────────────────────────
            items.append(_mi("WEEKLY LIMITS"))
            items.append(None)
            for row in [data.weekly_all, data.weekly_sonnet]:
                if row:
                    for line in _row_lines(row):
                        if line:
                            items.append(_mi(line))
                    items.append(None)

            # ── Section 3: Extra usage ──────────────────────────────────
            items.append(_mi("EXTRA USAGE"))
            items.append(None)
            if data.overages_enabled is not None:
                status = "On" if data.overages_enabled else "Off"
                items.append(_mi(f"  Extra usage: {status}"))
            else:
                items.append(_mi("  (not available)"))
            items.append(None)

        items += [
            rumps.MenuItem("Refresh Now", callback=self._do_refresh),
            rumps.MenuItem("Set Session Cookie…", callback=self._set_cookie),
            rumps.MenuItem("Show Raw API Data…", callback=self._show_raw),
            None,
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]

        self.menu.clear()
        self.menu = items

    # ── fetch ─────────────────────────────────────────────────────────────────

    def _on_timer(self, _timer):
        self._schedule_fetch()

    def _schedule_fetch(self):
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        sk = self.config.get("cookie_str")
        if not sk:
            return
        try:
            raw = fetch_raw(sk)
            self._last_raw = raw
            data = parse_usage(raw)
            log.debug("parsed UsageData: %s", data)
            self._apply(data)
        except CurlHTTPError as e:
            code = getattr(e, "code", 0) or 0
            log.error("HTTP error: %s", e, exc_info=True)
            if code in (401, 403):
                self.title = "◆ !"
                rumps.notification(
                    "Claude Usage Bar",
                    "Auth failed — refresh your cookies",
                    "Click the menu bar icon → Set Session Cookie…",
                )
            else:
                self.title = f"◆ err"
        except Exception:
            log.exception("fetch failed")
            self.title = "◆ ?"

    def _apply(self, data: UsageData):
        # Title badge: highest-pct row
        candidates = [r for r in [data.session, data.weekly_all, data.weekly_sonnet] if r]
        if candidates:
            top = max(candidates, key=lambda r: r.pct)
            self.title = f"◆ {top.pct}%"
        else:
            self.title = "◆"
        self._rebuild_menu(data)

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _do_refresh(self, _sender):
        self._schedule_fetch()

    def _set_cookie(self, _sender):
        key = _ask_text(
            title="Claude Usage — Set Cookies",
            prompt=(
                "Paste ALL cookies from claude.ai (needed to bypass Cloudflare)\\n\\n"
                "How to get them:\\n"
                "  1. Open https://claude.ai/settings/usage in Chrome\\n"
                "  2. F12 → Network tab → click any request to claude.ai\\n"
                "  3. In Headers, find the 'cookie:' row\\n"
                "  4. Right-click it → Copy value  (long string with semicolons)"
            ),
            default=self.config.get("cookie_str", ""),
        )
        if key:
            self.config["cookie_str"] = key.strip()
            save_config(self.config)
            self._schedule_fetch()

    def _show_raw(self, _sender):
        text = json.dumps(self._last_raw.get("usage", self._last_raw), indent=2)
        _show_text(title="Claude Usage — Raw API Response", text=text)


if __name__ == "__main__":
    ClaudeBar().run()
