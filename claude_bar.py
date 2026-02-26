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
import sys
import tempfile
import threading
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

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


@dataclass
class ProviderData:
    """Usage/billing data for a third-party API provider."""
    name: str
    spent: float | None = None    # current period spend
    limit: float | None = None    # hard/soft limit
    balance: float | None = None  # prepaid balance (for credit-based providers)
    currency: str = "USD"
    period: str = "this month"
    error: str | None = None

    @property
    def pct(self) -> int | None:
        if self.spent is not None and self.limit and self.limit > 0:
            return min(100, round(self.spent / self.limit * 100))
        return None


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


# ── third-party provider APIs ────────────────────────────────────────────────

def _api_get(url: str, headers: dict, cookies: dict | None = None) -> dict:
    r = requests.get(url, headers=headers, cookies=cookies, timeout=10, impersonate="chrome131")
    r.raise_for_status()
    return r.json()


_CHATGPT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://chatgpt.com/codex/settings/usage",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


def _chatgpt_access_token(cookies: dict) -> str | None:
    """Exchange session cookie for a short-lived Bearer token."""
    data = _api_get("https://chatgpt.com/api/auth/session", _CHATGPT_HEADERS, cookies)
    return data.get("accessToken")


def _parse_wham_window(window: dict, label: str) -> LimitRow | None:
    """Parse a single rate-limit window dict into a LimitRow."""
    if not window or not isinstance(window, dict):
        return None
    pw = window.get("primary_window") or {}
    pct = min(100, int(pw.get("used_percent", 0)))
    reset_str = _fmt_reset(pw.get("reset_at")) if pw.get("reset_at") else ""
    return LimitRow(label, pct, reset_str)


def _parse_wham_usage(data: dict) -> ProviderData:
    """Parse /backend-api/wham/usage response.

    Confirmed shape (2026-02):
      rate_limit.primary_window.used_percent  (0-100)
      rate_limit.primary_window.reset_at      (Unix timestamp)
      code_review_rate_limit  — same structure
    """
    log.debug("wham/usage raw: %s", json.dumps(data, indent=2))

    rows: list[LimitRow] = []

    label_map = {
        "rate_limit":            "Codex Tasks",
        "code_review_rate_limit": "Code Review",
    }
    for key, label in label_map.items():
        row = _parse_wham_window(data.get(key), label)
        if row is not None:
            rows.append(row)

    # additional_rate_limits may be a list of extra buckets
    for extra in (data.get("additional_rate_limits") or []):
        if isinstance(extra, dict):
            name = extra.get("name") or extra.get("type") or "Extra"
            row = _parse_wham_window(extra, name.replace("_", " ").title())
            if row:
                rows.append(row)

    if not rows:
        return ProviderData("ChatGPT", error="No rate limit data in response")

    worst = max(rows, key=lambda r: r.pct)
    pd = ProviderData("ChatGPT", spent=float(worst.pct), limit=100.0, currency="")
    pd._rows = rows
    return pd


def fetch_chatgpt(cookie_str: str) -> ProviderData:
    """Fetch ChatGPT / Codex usage via /backend-api/wham/usage."""
    cookies = parse_cookie_string(cookie_str)
    try:
        token = _chatgpt_access_token(cookies)
        if not token:
            return ProviderData("ChatGPT", error="Not logged in")
        h = {**_CHATGPT_HEADERS, "Authorization": f"Bearer {token}"}
        data = _api_get("https://chatgpt.com/backend-api/wham/usage", h, cookies)
        return _parse_wham_usage(data)
    except Exception as e:
        log.debug("fetch_chatgpt failed: %s", e)
        return ProviderData("ChatGPT", error=str(e)[:80])


def fetch_openai(api_key: str) -> ProviderData:
    h = {"Authorization": f"Bearer {api_key}"}
    try:
        sub = _api_get(
            "https://api.openai.com/v1/dashboard/billing/subscription", h
        )
        hard_limit = float(
            sub.get("hard_limit_usd") or sub.get("system_hard_limit_usd") or 0
        )
        now = datetime.now()
        start = now.replace(day=1).strftime("%Y-%m-%d")
        end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        usage = _api_get(
            f"https://api.openai.com/v1/dashboard/billing/usage"
            f"?start_date={start}&end_date={end}", h
        )
        spent = float(usage.get("total_usage", 0)) / 100  # cents → dollars
        return ProviderData(
            "OpenAI", spent=spent,
            limit=hard_limit or None, currency="USD", period="this month",
        )
    except Exception as e:
        log.debug("fetch_openai failed: %s", e)
        return ProviderData("OpenAI", error=str(e)[:80])


def fetch_minimax(api_key: str) -> ProviderData:
    h = {"Authorization": f"Bearer {api_key}"}
    try:
        data = _api_get("https://api.minimax.chat/v1/account_information", h)
        balance = float(
            data.get("available_balance") or data.get("balance") or 0
        )
        return ProviderData("MiniMax", balance=balance, currency="CNY")
    except Exception as e:
        log.debug("fetch_minimax failed: %s", e)
        return ProviderData("MiniMax", error=str(e)[:80])


def fetch_glm(api_key: str) -> ProviderData:
    h = {"Authorization": f"Bearer {api_key}"}
    try:
        data = _api_get(
            "https://open.bigmodel.cn/api/paas/v4/account/balance", h
        )
        balance = float(
            data.get("total_balance") or data.get("balance") or 0
        )
        return ProviderData("GLM (Zhipu)", balance=balance, currency="CNY")
    except Exception as e:
        log.debug("fetch_glm failed: %s", e)
        return ProviderData("GLM (Zhipu)", error=str(e)[:80])


# Registry: config_key → (display_name, fetch_fn)
# chatgpt_cookies is cookie-based (auto-detected); others are API key-based.
PROVIDER_REGISTRY: dict[str, tuple[str, callable]] = {
    "chatgpt_cookies": ("ChatGPT",     fetch_chatgpt),
    "openai_key":      ("OpenAI",      fetch_openai),
    "minimax_key":     ("MiniMax",     fetch_minimax),
    "glm_key":         ("GLM (Zhipu)", fetch_glm),
}

# Cookie-based providers (auto-detected from browser, not manually entered)
_COOKIE_PROVIDERS = {"chatgpt_cookies"}


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
    raw = float(bucket.get("utilization", 0))
    # API is inconsistent: five_hour returns 0-1 fraction, weekly returns 0-100 percentage
    pct = min(100, round(raw if raw > 1.0 else raw * 100))
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


# ── Claude Code local stats ───────────────────────────────────────────────────

CC_STATS_FILE = os.path.expanduser("~/.claude/stats-cache.json")


def fetch_claude_code_stats() -> dict | None:
    """Read Claude Code usage from ~/.claude/stats-cache.json (no network needed).

    Returns dict with today_messages, today_sessions, week_messages,
    week_sessions, week_tool_calls — or None if the file doesn't exist.
    """
    if not os.path.exists(CC_STATS_FILE):
        return None
    try:
        with open(CC_STATS_FILE) as f:
            data = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        entries = data.get("dailyActivity", [])
        today_e = next((e for e in entries if e["date"] == today), None)
        week_e  = [e for e in entries if e["date"] >= week_ago]
        return {
            "today_messages":   today_e["messageCount"]  if today_e else 0,
            "today_sessions":   today_e["sessionCount"]  if today_e else 0,
            "week_messages":    sum(e["messageCount"]  for e in week_e),
            "week_sessions":    sum(e["sessionCount"]  for e in week_e),
            "week_tool_calls":  sum(e["toolCallCount"] for e in week_e),
            "last_date": max((e["date"] for e in entries), default=None),
        }
    except Exception as e:
        log.debug("fetch_claude_code_stats failed: %s", e)
        return None


def _fmt_count(n: int) -> str:
    """Format a message count compactly: 1234 → '1.2k', 999 → '999'."""
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


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
    line1 = f"  {icon} {row.label}  {row.pct}%"
    line2 = f"  {bar}  {row.reset_str}" if row.reset_str else f"  {bar}"
    return [line1, line2]


def _provider_lines(pd: ProviderData) -> list[str]:
    sym = "¥" if pd.currency == "CNY" else ("" if pd.currency == "" else "$")
    if pd.error:
        return [f"  ⚠️  {pd.error[:60]}"]
    # ChatGPT multi-row format (stored in pd._rows by _parse_wham_usage)
    rows = getattr(pd, "_rows", None)
    if rows:
        lines = []
        for row in rows:
            for line in _row_lines(row):
                if line:
                    lines.append(line)
        return lines
    # Standard spending / balance format
    lines = []
    if pd.pct is not None:
        icon = _status_icon(pd.pct)
        bar = _bar(pd.pct)
        lines.append(f"  {icon} {sym}{pd.spent:.2f} / {sym}{pd.limit:.2f} {pd.period}")
        lines.append(f"  {bar}  {pd.pct}%")
    elif pd.balance is not None:
        lines.append(f"  💰 {sym}{pd.balance:.2f} remaining")
    elif pd.spent is not None:
        lines.append(f"  💰 {sym}{pd.spent:.2f} {pd.period}")
    return lines


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


_keychain_warned = False  # show the dialog at most once per session


def _warn_keychain_once():
    """Show a one-time dialog before the macOS Keychain prompt appears."""
    global _keychain_warned
    if _keychain_warned:
        return
    _keychain_warned = True
    subprocess.run(
        ["osascript", "-e",
         'display dialog "Claude Usage Bar needs one-time access to your '
         'browser cookies to read your Claude usage.\\n\\n'
         'macOS will show a security prompt — click \\"Always Allow\\" '
         'and it will never ask again." '
         'with title "Claude Usage Bar — One-time Setup" '
         'buttons {"OK"} default button "OK" '
         'with icon note'],
        capture_output=True, timeout=60,
    )


# Script run in a child process — isolates browser_cookie3 C-library crashes
# (libcrypto / sqlite segfaults on Chromium decryption don't kill the main app).
_DETECT_SCRIPT = r"""
import sys, json

domain  = sys.argv[1]
target  = sys.argv[2]
result  = None

BROWSERS = [
    'firefox', 'librewolf', 'chrome', 'arc', 'brave',
    'edge', 'chromium', 'opera', 'vivaldi',
]

try:
    import browser_cookie3
    for name in BROWSERS:
        fn = getattr(browser_cookie3, name, None)
        if fn is None:
            continue
        try:
            jar = fn(domain_name=domain)
            c = {x.name: x.value for x in jar}
            if target in c:
                result = '; '.join(f'{k}={v}' for k, v in c.items())
                break
        except Exception:
            pass
except Exception:
    pass

print(json.dumps(result))
"""


def _run_cookie_detection(domain: str, target_cookie: str) -> str | None:
    """Run browser_cookie3 in an isolated child process (crash-safe)."""
    try:
        r = subprocess.run(
            [sys.executable, "-c", _DETECT_SCRIPT, domain, target_cookie],
            capture_output=True, text=True, timeout=60,
        )
        log.debug("cookie-detect rc=%d out=%r err=%r",
                  r.returncode, r.stdout[:200], r.stderr[:200])
        if r.stdout.strip():
            return json.loads(r.stdout.strip())
    except Exception as e:
        log.debug("_run_cookie_detection failed: %s", e)
    return None


def _auto_detect_cookies() -> str | None:
    """Detect claude.ai session cookies from the browser (crash-safe subprocess)."""
    if not _BROWSER_COOKIE3_OK:
        return None
    _warn_keychain_once()
    return _run_cookie_detection("claude.ai", "sessionKey")


def _auto_detect_chatgpt_cookies() -> str | None:
    """Detect chatgpt.com session cookies from the browser (crash-safe subprocess)."""
    if not _BROWSER_COOKIE3_OK:
        return None
    return _run_cookie_detection("chatgpt.com", "__Secure-next-auth.session-token")


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
        self._provider_data: list[ProviderData] = []
        self._warned_pcts: set[str] = set()   # track which rows we've notified
        self._auth_fail_count = 0
        self._fetching = False
        self._last_updated: datetime | None = None

        self._refresh_interval = self.config.get("refresh_interval", DEFAULT_REFRESH)
        self._cc_stats: dict | None = None   # Claude Code local stats

        # Thread-safe UI update queue (background thread → main thread)
        self._ui_pending_title: str | None = None
        self._ui_pending_data: UsageData | None = None
        self._ui_lock = threading.Lock()

        self._rebuild_menu(None)
        self._timer = rumps.Timer(self._on_timer, self._refresh_interval)
        self._timer.start()
        # Fast ticker: drains pending UI updates on the main thread (avoids AppKit crashes)
        self._ui_ticker = rumps.Timer(self._flush_ui, 0.25)
        self._ui_ticker.start()

        # Always try to fetch on startup — browser JS works even without saved cookies
        self._schedule_fetch()

    # ── menu ─────────────────────────────────────────────────────────────────

    def _rebuild_menu(self, data: UsageData | None):
        items: list = []

        # ── ◆  CLAUDE section ─────────────────────────────────────────────
        items.append(_mi("◆  CLAUDE"))
        items.append(None)

        if data is None or not any([data.session, data.weekly_all, data.weekly_sonnet]):
            items.append(_mi("  No data — click Auto-detect from Browser"))
        else:
            if data.session:
                for line in _row_lines(data.session):
                    items.append(_mi(line))
                items.append(None)

            for row in [data.weekly_all, data.weekly_sonnet]:
                if row:
                    for line in _row_lines(row):
                        items.append(_mi(line))
                    items.append(None)

            if data.overages_enabled is not None:
                status = "✅  On" if data.overages_enabled else "⛔  Off"
                items.append(_mi(f"  Extra usage  {status}"))
                items.append(None)

        # ── ◇  CHATGPT section (if detected) ──────────────────────────────
        chatgpt_pd = next(
            (pd for pd in self._provider_data if pd.name == "ChatGPT"), None
        )
        if chatgpt_pd:
            items.append(_mi("◇  CHATGPT"))
            items.append(None)
            for line in _provider_lines(chatgpt_pd):
                if line:
                    items.append(_mi(line))
            items.append(None)

        # ── ◆  CLAUDE CODE section ────────────────────────────────────────────
        if self._cc_stats:
            cc = self._cc_stats
            items.append(_mi("◆  CLAUDE CODE"))
            items.append(None)
            if cc["today_messages"] > 0:
                items.append(_mi(
                    f"  Today     {_fmt_count(cc['today_messages'])} msgs"
                    f"  ·  {cc['today_sessions']} sessions"
                ))
            wm = cc["week_messages"]
            if wm > 0:
                items.append(_mi(
                    f"  This week  {_fmt_count(wm)} msgs"
                    f"  ·  {cc['week_sessions']} sessions"
                    f"  ·  {_fmt_count(cc['week_tool_calls'])} tools"
                ))
            if cc.get("last_date"):
                items.append(_mi(f"  Last active  {cc['last_date']}"))
            items.append(None)

        # ── Other API providers ────────────────────────────────────────────
        for pd in self._provider_data:
            if pd.name == "ChatGPT":
                continue
            items.append(_mi(f"  {pd.name}"))
            items.append(None)
            for line in _provider_lines(pd):
                if line:
                    items.append(_mi(line))
            items.append(None)

        # ── Footer ────────────────────────────────────────────────────────
        if self._last_updated:
            t = self._last_updated.strftime("%H:%M")
            items.append(_mi(f"  Updated {t}"))
            items.append(None)

        # ── Actions ──────────────────────────────────────────────────────
        items.append(rumps.MenuItem("Refresh Now", callback=self._do_refresh))
        items.append(rumps.MenuItem("Open claude.ai/settings/usage", callback=self._open_usage_page))
        items.append(None)

        # Refresh interval submenu
        interval_menu = rumps.MenuItem("Refresh Interval")
        for label, secs in REFRESH_INTERVALS.items():
            item = rumps.MenuItem(
                ("✓ " if secs == self._refresh_interval else "   ") + label,
                callback=self._make_interval_cb(secs, label),
            )
            interval_menu.add(item)
        items.append(interval_menu)
        items.append(None)

        # API providers submenu
        providers_menu = rumps.MenuItem("API Providers")
        for cfg_key, (name, _) in PROVIDER_REGISTRY.items():
            is_set = bool(self.config.get(cfg_key))
            if cfg_key in _COOKIE_PROVIDERS:
                label = f"{'✓' if is_set else '+'} {name} (auto-detect)"
            else:
                label = f"{'✓' if is_set else '+'} {name} API Key…"
            providers_menu.add(rumps.MenuItem(
                label, callback=self._make_provider_key_cb(cfg_key, name)
            ))
        items.append(providers_menu)

        items.append(None)
        items.append(rumps.MenuItem("Auto-detect from Browser", callback=self._auto_detect_menu))
        items.append(rumps.MenuItem("Set Session Cookie…", callback=self._set_cookie))
        items.append(rumps.MenuItem("Paste Cookie from Clipboard", callback=self._paste_cookie))
        items.append(rumps.MenuItem("Show Raw API Data…", callback=self._show_raw))
        items.append(None)

        login_label = "✓ Launch at Login" if _is_login_item() else "   Launch at Login"
        items.append(rumps.MenuItem(login_label, callback=self._toggle_login_item))

        items.append(None)
        items.append(rumps.MenuItem("Quit", callback=rumps.quit_application))

        self.menu.clear()
        self.menu = items

    # ── thread-safe UI helpers ────────────────────────────────────────────────

    def _post_title(self, title: str):
        """Queue a title update from any thread."""
        with self._ui_lock:
            self._ui_pending_title = title

    def _post_data(self, data: UsageData):
        """Queue a full UI update (title + menu) from any thread."""
        with self._ui_lock:
            self._ui_pending_data = data

    def _flush_ui(self, _timer):
        """Main-thread ticker: apply any queued updates from background threads."""
        with self._ui_lock:
            title = self._ui_pending_title
            data = self._ui_pending_data
            self._ui_pending_title = None
            self._ui_pending_data = None
        if data is not None:
            self._apply(data)
        elif title is not None:
            self.title = title

    # ── fetch ─────────────────────────────────────────────────────────────────

    def _on_timer(self, _timer):
        self._schedule_fetch()

    def _schedule_fetch(self):
        if self._fetching:
            return
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        self._fetching = True
        self._post_title("◆ …")

        try:
            sk = self.config.get("cookie_str")
            if not sk:
                sk = _auto_detect_cookies()
                if sk:
                    self.config["cookie_str"] = sk
                    save_config(self.config)
            if not sk:
                self._post_title("◆")
                self._fetching = False
                return
            raw = fetch_raw(sk)
            self._last_raw = raw
            self._auth_fail_count = 0
            data = parse_usage(raw)
            self._last_data = data
            self._last_updated = datetime.now()
            log.debug("parsed UsageData: %s", data)
            self._check_warnings(data)
            self._fetch_providers()
            self._cc_stats = fetch_claude_code_stats()
            self._post_data(data)          # ← main thread applies title + menu
        except CurlHTTPError as e:
            code = getattr(e, "code", 0) or 0
            log.error("HTTP error: %s", e, exc_info=True)
            if code in (401, 403):
                self._auth_fail_count += 1
                self._post_title("◆ !")
                if self._auth_fail_count >= 2:
                    self._auth_fail_count = 0
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
                self._post_title("◆ err")
        except Exception:
            log.exception("fetch failed")
            self._post_title("◆ ?")
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

    def _set_bar_title(self, pct: int, extra: str = "",
                       chatgpt_pct: int | None = None,
                       cc_msgs: int | None = None):
        """Multi-indicator attributed title: ● 31%  ◇ 0%  ◆ 3.2k

        Brand colors:
          Claude / Claude Code  #D97757  coral (Anthropic brand)
          ChatGPT               #74AA9C  teal  (OpenAI brand)
        Falls back to plain emoji text if AppKit is unavailable.
        """
        try:
            from AppKit import (NSColor, NSFont,
                                NSForegroundColorAttributeName, NSFontAttributeName)
            from Foundation import NSMutableAttributedString

            def _rgb(r, g, b):
                return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)

            # Brand colors
            CLAUDE_COLOR  = _rgb(217/255, 119/255,  87/255)  # #D97757 Anthropic coral
            CHATGPT_COLOR = _rgb(116/255, 170/255, 156/255)  # #74AA9C OpenAI teal

            font = NSFont.menuBarFontOfSize_(0)
            base = {NSFontAttributeName: font} if font else {}

            # (text, color | None)
            segs: list[tuple[str, object]] = []
            segs.append(("● ",             CLAUDE_COLOR))
            segs.append((f"{pct}%{extra}", None))

            if chatgpt_pct is not None:
                segs.append(("  ◇ ",           CHATGPT_COLOR))
                segs.append((f"{chatgpt_pct}%", None))

            if cc_msgs is not None and cc_msgs > 0:
                segs.append(("  ◆ ",              CLAUDE_COLOR))
                segs.append((_fmt_count(cc_msgs),  None))

            full = "".join(t for t, _ in segs)
            s = NSMutableAttributedString.alloc().initWithString_attributes_(full, base)
            pos = 0
            for text, color in segs:
                if color is not None:
                    s.addAttribute_value_range_(
                        NSForegroundColorAttributeName, color, (pos, len(text))
                    )
                pos += len(text)

            self._nsapp.nsstatusitem.setAttributedTitle_(s)
            return
        except Exception as e:
            log.debug("_set_bar_title failed: %s", e)
        # Plain-text fallback
        parts = [f"{_status_icon(pct)} {pct}%{extra}"]
        if chatgpt_pct is not None:
            parts.append(f"◇ {chatgpt_pct}%")
        if cc_msgs is not None and cc_msgs > 0:
            parts.append(f"◆ {_fmt_count(cc_msgs)}")
        self.title = "  ".join(parts)

    def _apply(self, data: UsageData):
        primary = data.session or data.weekly_all or data.weekly_sonnet
        if primary:
            weekly_maxed = any(
                r and r.pct >= CRIT_THRESHOLD
                for r in [data.weekly_all, data.weekly_sonnet]
            )
            extra = " ·" if (weekly_maxed and primary is data.session
                             and primary.pct < CRIT_THRESHOLD) else ""

            # ChatGPT Codex % for the bar (worst-case across all windows)
            chatgpt_pd = next(
                (pd for pd in self._provider_data if pd.name == "ChatGPT"), None
            )
            chatgpt_pct: int | None = None
            if chatgpt_pd and not chatgpt_pd.error:
                rows = getattr(chatgpt_pd, "_rows", None)
                if rows:
                    chatgpt_pct = max(r.pct for r in rows)

            # Claude Code weekly messages
            cc_msgs: int | None = None
            if self._cc_stats:
                cc_msgs = self._cc_stats.get("week_messages")

            self._set_bar_title(primary.pct, extra,
                                chatgpt_pct=chatgpt_pct, cc_msgs=cc_msgs)
        else:
            self.title = "◆"
        self._rebuild_menu(data)

    def _fetch_providers(self):
        """Fetch all configured third-party API providers (sync, called from fetch thread)."""
        # Auto-detect ChatGPT cookies if not saved yet
        for cfg_key in _COOKIE_PROVIDERS:
            if not self.config.get(cfg_key):
                if cfg_key == "chatgpt_cookies":
                    ck = _auto_detect_chatgpt_cookies()
                    if ck:
                        self.config[cfg_key] = ck
                        save_config(self.config)

        results = []
        for cfg_key, (name, fetch_fn) in PROVIDER_REGISTRY.items():
            key = self.config.get(cfg_key)
            if key:
                results.append(fetch_fn(key))
        self._provider_data = results

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _do_refresh(self, _sender):
        self._schedule_fetch()

    def _open_usage_page(self, _sender):
        subprocess.Popen(["open", "https://claude.ai/settings/usage"])

    def _make_provider_key_cb(self, cfg_key: str, name: str):
        def _cb(_sender):
            if cfg_key in _COOKIE_PROVIDERS:
                # Cookie-based: re-run auto-detect
                if cfg_key == "chatgpt_cookies":
                    ck = _auto_detect_chatgpt_cookies()
                    if ck:
                        self.config[cfg_key] = ck
                        save_config(self.config)
                        _notify("Claude Usage Bar", f"{name} cookies updated ✓", "Fetching usage…")
                        self._schedule_fetch()
                    else:
                        _notify("Claude Usage Bar", f"Could not find {name} session",
                                f"Make sure you are logged into {name} in your browser.")
                return
            # API key-based
            current = self.config.get(cfg_key, "")
            key = _ask_text(
                title=f"Claude Usage Bar — {name}",
                prompt=f"Paste your {name} API key.\nLeave blank to remove.",
                default=current,
            )
            if key is None:
                return
            if key.strip():
                self.config[cfg_key] = key.strip()
            else:
                self.config.pop(cfg_key, None)
            save_config(self.config)
            self._schedule_fetch()
        return _cb

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
        """Menu item: manually trigger auto-detect (runs in background thread)."""
        if not _BROWSER_COOKIE3_OK:
            _notify(
                "Claude Usage Bar",
                "browser-cookie3 not installed",
                "Run: pip install browser-cookie3",
            )
            return
        # Run cookie detection in a background thread — browser_cookie3 accesses
        # SQLite databases and Keychain which can hard-crash if called on the main thread.
        threading.Thread(target=self._do_auto_detect, daemon=True).start()

    def _do_auto_detect(self):
        """Background: detect cookies then schedule a fetch."""
        try:
            cookie_str = _auto_detect_cookies()
        except Exception:
            log.exception("_auto_detect_cookies failed")
            cookie_str = None
        if cookie_str:
            self.config["cookie_str"] = cookie_str
            save_config(self.config)
            self._warned_pcts.clear()
            self._auth_fail_count = 0
            _notify("Claude Usage Bar", "Cookies auto-detected ✓", "Fetching usage data…")
            self._schedule_fetch()
        else:
            _notify(
                "Claude Usage Bar",
                "Could not find claude.ai session in any browser",
                "Make sure you are logged in to claude.ai in Chrome, Firefox, or Safari.",
            )


if __name__ == "__main__":
    ClaudeBar().run()
