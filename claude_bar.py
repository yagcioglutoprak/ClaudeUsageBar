#!/usr/bin/env python3
"""
Claude Usage Menu Bar — macOS status bar app

Sections shown (matching claude.ai/settings/usage):
  1. Plan usage limits → Current session
  2. Weekly limits     → All models + Sonnet only
  3. Extra usage       → toggle status

Setup:
  pip install -r requirements.txt
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

try:
    import browser_cookie3
    _BROWSER_COOKIE3_OK = True
except ImportError:
    _BROWSER_COOKIE3_OK = False

# ── logging ──────────────────────────────────────────────────────────────────

LOG_FILE = os.path.expanduser("~/.claude_bar.log")
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

CONFIG_FILE = os.path.expanduser("~/.claude_bar_config.json")

REFRESH_INTERVALS = {
    "1 min":  60,
    "5 min":  300,
    "15 min": 900,
}
DEFAULT_REFRESH = 300

WARN_THRESHOLD = 80   # notify when any limit crosses this %
CRIT_THRESHOLD = 95   # title turns red emoji above this %

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
        impersonate="chrome131",
    )
    log.debug("GET %s  status=%s  body=%s", url, r.status_code, r.text[:800])
    r.raise_for_status()
    return r.json()


def _org_id_from_cookies(cookies: dict) -> str | None:
    return cookies.get("lastActiveOrg") or cookies.get("routingHint")


def _org_id_from_api(cookies: dict) -> str | None:
    for path in (
        "/api/organizations",
        "/api/bootstrap",
        "/api/auth/current_account",
        "/api/account",
    ):
        try:
            data = _get(f"https://claude.ai{path}", cookies)
            if isinstance(data, list) and data:
                return data[0].get("id") or data[0].get("uuid")
            if isinstance(data, dict):
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
            s = str(val).rstrip("Z")
            if "+" not in s[10:] and s[-6] != "+":
                s += "+00:00"
            dt = datetime.fromisoformat(s)
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
        day = _DAYS[dt.weekday()]
        return f"resets {day} {dt.strftime('%H:%M')}"
    except Exception:
        log.debug("_fmt_reset failed for %r", val, exc_info=True)
        return str(val)[:20]


# ── parser ────────────────────────────────────────────────────────────────────

def _row(data: dict, key: str, label: str) -> LimitRow | None:
    bucket = data.get(key)
    if not bucket or not isinstance(bucket, dict):
        return None
    pct = min(100, round(float(bucket.get("utilization", 0)) * 100))
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


def _status_icon(pct: int) -> str:
    if pct >= CRIT_THRESHOLD:
        return "🔴"
    if pct >= WARN_THRESHOLD:
        return "🟡"
    return "🟢"


def _row_lines(row: LimitRow) -> list[str]:
    bar = _bar(row.pct)
    icon = _status_icon(row.pct)
    return [
        f"  {icon} {row.label}",
        f"  {bar}  {row.pct}%",
        f"  {row.reset_str}" if row.reset_str else "",
    ]


def _mi(title: str) -> rumps.MenuItem:
    """Disabled (display-only) menu item."""
    item = rumps.MenuItem(title)
    item.set_callback(None)
    return item


# ── login item helpers ────────────────────────────────────────────────────────

def _script_path() -> str:
    return os.path.abspath(__file__)


def _is_login_item() -> bool:
    try:
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get the name of every login item'],
            capture_output=True, text=True, timeout=10,
        )
        return "claude_bar" in result.stdout.lower()
    except Exception:
        return False


def _add_login_item():
    path = _script_path()
    script = (
        f'tell application "System Events" to make login item at end '
        f'with properties {{path:"/usr/bin/python3", name:"ClaudeBar", hidden:false}}'
    )
    # Use launchctl + a plist for reliability
    plist = os.path.expanduser("~/Library/LaunchAgents/com.claudebar.plist")
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claudebar</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>{path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>"""
    with open(plist, "w") as f:
        f.write(content)
    subprocess.run(["launchctl", "load", plist], capture_output=True)


def _remove_login_item():
    plist = os.path.expanduser("~/Library/LaunchAgents/com.claudebar.plist")
    if os.path.exists(plist):
        subprocess.run(["launchctl", "unload", plist], capture_output=True)
        os.remove(plist)


# ── native macOS dialogs via osascript ───────────────────────────────────────

def _ask_text(title: str, prompt: str, default: str = "") -> str | None:
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
            return None
        out = result.stdout.strip()
        if "text returned:" in out:
            return out.split("text returned:")[-1].strip()
    except Exception:
        log.exception("_ask_text failed")
    return None


def _clipboard_text() -> str:
    try:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _auto_detect_cookies() -> str | None:
    """Try to read claude.ai cookies from installed browsers."""
    if not _BROWSER_COOKIE3_OK:
        return None

    browsers = [
        ("Chrome",  browser_cookie3.chrome),
        ("Brave",   browser_cookie3.brave),
        ("Firefox", browser_cookie3.firefox),
        ("Safari",  browser_cookie3.safari),
    ]
    for name, loader in browsers:
        try:
            jar = loader(domain_name="claude.ai")
            cookies = {c.name: c.value for c in jar}
            if "sessionKey" in cookies:
                cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
                log.info("Auto-detected cookies from %s (%d keys)", name, len(cookies))
                return cookie_str
        except Exception as e:
            log.debug("browser_cookie3 %s failed: %s", name, e)
    return None


def _notify(title: str, subtitle: str, message: str = ""):
    """rumps.notification wrapper — silently swallows if the notification
    center is unavailable (e.g. missing Info.plist in dev environments)."""
    try:
        rumps.notification(title, subtitle, message)
    except Exception as e:
        log.debug("notification suppressed: %s", e)


def _show_text(title: str, text: str):
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
        self._last_data: UsageData | None = None
        self._warned_pcts: set[str] = set()   # track which rows we've notified
        self._auth_fail_count = 0
        self._fetching = False
        self._last_updated: datetime | None = None

        self._refresh_interval = self.config.get("refresh_interval", DEFAULT_REFRESH)

        self._rebuild_menu(None)
        self._timer = rumps.Timer(self._on_timer, self._refresh_interval)
        self._timer.start()

        if self.config.get("cookie_str"):
            self._schedule_fetch()
        else:
            # Try to auto-detect cookies from the browser on first run
            threading.Thread(target=self._try_auto_detect, daemon=True).start()

    # ── menu ─────────────────────────────────────────────────────────────────

    def _rebuild_menu(self, data: UsageData | None):
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

            # ── Section 3: Extra usage ──────────��───────────────────────
            items.append(_mi("EXTRA USAGE"))
            items.append(None)
            if data.overages_enabled is not None:
                status = "✅ On" if data.overages_enabled else "⛔ Off"
                items.append(_mi(f"  Extra usage: {status}"))
            else:
                items.append(_mi("  (not available)"))
            items.append(None)

            # ── Last updated ────────────────────────────────────────────
            if self._last_updated:
                t = self._last_updated.strftime("%H:%M:%S")
                items.append(_mi(f"  Updated at {t}"))
                items.append(None)

        # ── Actions ──────────────────────────────────────────────────────
        items.append(rumps.MenuItem("Refresh Now", callback=self._do_refresh))
        items.append(rumps.MenuItem("Open claude.ai/settings/usage", callback=self._open_usage_page))
        items.append(None)

        # Refresh interval submenu
        interval_menu = rumps.MenuItem("Refresh Interval")
        for label, secs in REFRESH_INTERVALS.items():
            item = rumps.MenuItem(
                ("✓ " if secs == self._refresh_interval else "  ") + label,
                callback=self._make_interval_cb(secs, label),
            )
            interval_menu.add(item)
        items.append(interval_menu)

        items.append(None)
        items.append(rumps.MenuItem("Auto-detect from Browser", callback=self._auto_detect_menu))
        items.append(rumps.MenuItem("Set Session Cookie…", callback=self._set_cookie))
        items.append(rumps.MenuItem("Paste Cookie from Clipboard", callback=self._paste_cookie))
        items.append(rumps.MenuItem("Show Raw API Data…", callback=self._show_raw))
        items.append(None)

        # Launch at login toggle
        login_label = "✓ Launch at Login" if _is_login_item() else "  Launch at Login"
        items.append(rumps.MenuItem(login_label, callback=self._toggle_login_item))

        items.append(None)
        items.append(rumps.MenuItem("Quit", callback=rumps.quit_application))

        self.menu.clear()
        self.menu = items

    # ── fetch ─────────────────────────────────────────────────────────────────

    def _on_timer(self, _timer):
        self._schedule_fetch()

    def _schedule_fetch(self):
        if self._fetching:
            return
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        self._fetching = True
        sk = self.config.get("cookie_str")
        if not sk:
            self._fetching = False
            return

        # Show loading state
        current = self.title or "◆"
        self.title = current.split(" ")[0] + " …"

        try:
            raw = fetch_raw(sk)
            self._last_raw = raw
            self._auth_fail_count = 0
            data = parse_usage(raw)
            self._last_data = data
            self._last_updated = datetime.now()
            log.debug("parsed UsageData: %s", data)
            self._check_warnings(data)
            self._apply(data)
        except CurlHTTPError as e:
            code = getattr(e, "code", 0) or 0
            log.error("HTTP error: %s", e, exc_info=True)
            if code in (401, 403):
                self._auth_fail_count += 1
                self.title = "◆ !"
                if self._auth_fail_count >= 2:
                    self._auth_fail_count = 0
                    # Try auto-detect first before prompting the user
                    cookie_str = _auto_detect_cookies()
                    if cookie_str:
                        self.config["cookie_str"] = cookie_str
                        save_config(self.config)
                        self._warned_pcts.clear()
                        log.info("Auth failed — auto-detected fresh cookies from browser")
                        self._schedule_fetch()
                    else:
                        _notify(
                            "Claude Usage Bar",
                            "Session expired — please update your cookie",
                            "Click: Set Session Cookie… or Auto-detect from Browser",
                        )
            else:
                self.title = "◆ err"
        except Exception:
            log.exception("fetch failed")
            self.title = "◆ ?"
        finally:
            self._fetching = False

    def _check_warnings(self, data: UsageData):
        """Send macOS notification when a limit crosses the warning threshold."""
        rows = [
            (data.session, "session"),
            (data.weekly_all, "weekly_all"),
            (data.weekly_sonnet, "weekly_sonnet"),
        ]
        for row, key in rows:
            if row is None:
                continue
            warn_key = f"{key}_{WARN_THRESHOLD}"
            crit_key = f"{key}_{CRIT_THRESHOLD}"
            if row.pct >= CRIT_THRESHOLD and crit_key not in self._warned_pcts:
                self._warned_pcts.add(crit_key)
                _notify(
                    "Claude Usage Bar 🔴",
                    f"{row.label} is at {row.pct}%!",
                    row.reset_str or "Limit almost reached",
                )
            elif row.pct >= WARN_THRESHOLD and warn_key not in self._warned_pcts:
                self._warned_pcts.add(warn_key)
                _notify(
                    "Claude Usage Bar 🟡",
                    f"{row.label} is at {row.pct}%",
                    row.reset_str or "Approaching limit",
                )
            elif row.pct < WARN_THRESHOLD:
                # Reset warnings when usage drops (after a reset)
                self._warned_pcts.discard(warn_key)
                self._warned_pcts.discard(crit_key)

    def _apply(self, data: UsageData):
        candidates = [r for r in [data.session, data.weekly_all, data.weekly_sonnet] if r]
        if candidates:
            top = max(candidates, key=lambda r: r.pct)
            icon = _status_icon(top.pct)
            self.title = f"{icon} {top.pct}%"
        else:
            self.title = "◆"
        self._rebuild_menu(data)

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _do_refresh(self, _sender):
        self._schedule_fetch()

    def _open_usage_page(self, _sender):
        subprocess.Popen(["open", "https://claude.ai/settings/usage"])

    def _make_interval_cb(self, secs: int, label: str):
        def _cb(_sender):
            self._refresh_interval = secs
            self.config["refresh_interval"] = secs
            save_config(self.config)
            self._timer.stop()
            self._timer = rumps.Timer(self._on_timer, secs)
            self._timer.start()
            self._rebuild_menu(self._last_data)
        return _cb

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
            self._warned_pcts.clear()
            self._auth_fail_count = 0
            self._schedule_fetch()

    def _paste_cookie(self, _sender):
        text = _clipboard_text()
        if not text or ("sessionKey" not in text and "=" not in text):
            _notify(
                "Claude Usage Bar",
                "Nothing useful in clipboard",
                "Copy your cookie string from Chrome DevTools first.",
            )
            return
        self.config["cookie_str"] = text
        save_config(self.config)
        self._warned_pcts.clear()
        self._auth_fail_count = 0
        self._schedule_fetch()
        _notify(
            "Claude Usage Bar",
            "Cookie updated from clipboard ✓",
            "Fetching usage data…",
        )

    def _show_raw(self, _sender):
        text = json.dumps(self._last_raw.get("usage", self._last_raw), indent=2)
        _show_text(title="Claude Usage — Raw API Response", text=text)

    def _toggle_login_item(self, _sender):
        if _is_login_item():
            _remove_login_item()
            _notify("Claude Usage Bar", "Removed from Login Items", "")
        else:
            _add_login_item()
            _notify("Claude Usage Bar", "Added to Login Items ✓", "Will launch automatically on login")
        self._rebuild_menu(self._last_data)

    def _try_auto_detect(self):
        """Background: silently try to grab cookies from the browser on first run."""
        cookie_str = _auto_detect_cookies()
        if cookie_str:
            self.config["cookie_str"] = cookie_str
            save_config(self.config)
            _notify(
                "Claude Usage Bar",
                "Cookies auto-detected from your browser ✓",
                "Fetching usage data…",
            )
            self._schedule_fetch()

    def _auto_detect_menu(self, _sender):
        """Menu item: manually trigger auto-detect."""
        if not _BROWSER_COOKIE3_OK:
            _notify(
                "Claude Usage Bar",
                "browser-cookie3 not installed",
                "Run: pip install browser-cookie3",
            )
            return
        cookie_str = _auto_detect_cookies()
        if cookie_str:
            self.config["cookie_str"] = cookie_str
            save_config(self.config)
            self._warned_pcts.clear()
            self._auth_fail_count = 0
            _notify(
                "Claude Usage Bar",
                "Cookies auto-detected ✓",
                "Fetching usage data…",
            )
            self._schedule_fetch()
        else:
            _notify(
                "Claude Usage Bar",
                "Could not find claude.ai session in any browser",
                "Make sure you are logged in to claude.ai in Chrome, Firefox, or Safari.",
            )


if __name__ == "__main__":
    ClaudeBar().run()
