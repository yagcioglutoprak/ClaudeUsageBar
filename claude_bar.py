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
import urllib.parse
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

WIDGET_HOST_APP = "/Applications/AIQuotaBarHost.app"
WIDGET_CACHE_DIR = os.path.expanduser(
    "~/Library/Application Support/AIQuotaBar"
)
WIDGET_CACHE_FILE = os.path.join(WIDGET_CACHE_DIR, "usage.json")

# ── notification defaults ─────────────────────────────────────────────────────
# Keys stored in config under "notifications": { key: bool }
_NOTIF_DEFAULTS = {
    "claude_reset":   True,   # notify when Claude session/weekly resets
    "chatgpt_reset":  True,   # notify when ChatGPT rate-limit resets
    "claude_warning": True,   # notify when Claude usage crosses WARN/CRIT
    "chatgpt_warning":True,   # notify when ChatGPT usage crosses WARN/CRIT
    "claude_pacing":  True,   # predictive alert when Claude ETA < 30 min
    "chatgpt_pacing": True,   # predictive alert when ChatGPT ETA < 30 min
    "copilot_pacing": True,   # predictive alert when Copilot ETA < 30 min
}

# ── usage history + burn rate ────────────────────────────────────────────────

HISTORY_FILE = os.path.expanduser("~/.claude_bar_history.json")
HISTORY_MAX_AGE = 24 * 3600  # prune entries older than 24 h
PACING_ALERT_MINUTES = 30    # alert when ETA drops below this


def _load_history() -> dict:
    """Load usage history from disk. Returns {"claude": [...], ...}."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_history(history: dict):
    """Persist usage history (atomic write)."""
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(history, f)
    os.replace(tmp, HISTORY_FILE)


def _append_history(history: dict, key: str, pct: int):
    """Append a timestamped pct snapshot and prune old entries."""
    now = datetime.now(timezone.utc).timestamp()
    history.setdefault(key, []).append({"t": now, "pct": pct})
    cutoff = now - HISTORY_MAX_AGE
    history[key] = [e for e in history[key] if e["t"] >= cutoff]


def _calc_burn_rate(history: dict, key: str) -> float | None:
    """Linear regression over last 30 min of data points.

    Returns pct per minute (positive = increasing usage), or None if
    insufficient data.
    """
    entries = history.get(key, [])
    if len(entries) < 2:
        return None
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - 30 * 60
    recent = [e for e in entries if e["t"] >= cutoff]
    if len(recent) < 2:
        return None
    n = len(recent)
    sum_t = sum(e["t"] for e in recent)
    sum_p = sum(e["pct"] for e in recent)
    sum_tp = sum(e["t"] * e["pct"] for e in recent)
    sum_t2 = sum(e["t"] ** 2 for e in recent)
    denom = n * sum_t2 - sum_t ** 2
    if abs(denom) < 1e-10:
        return None
    slope = (n * sum_tp - sum_t * sum_p) / denom  # pct per second
    return slope * 60  # pct per minute


def _calc_eta_minutes(history: dict, key: str) -> int | None:
    """Estimate minutes until 100% based on burn rate.

    Returns None if burn rate is non-positive or ETA > 10 hours.
    """
    entries = history.get(key, [])
    if not entries:
        return None
    current_pct = entries[-1]["pct"]
    rate = _calc_burn_rate(history, key)
    if rate is None or rate <= 0:
        return None
    remaining = 100 - current_pct
    if remaining <= 0:
        return 0
    eta = remaining / rate  # minutes
    if eta > 600:  # > 10 hours
        return None
    return max(1, round(eta))


def _sparkline(history: dict, key: str, width: int = 20) -> str:
    """Render a sparkline from history using block chars."""
    entries = history.get(key, [])
    if len(entries) < 2:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    pts = [e["pct"] for e in entries[-width:]]
    lo, hi = min(pts), max(pts)
    span = hi - lo if hi > lo else 1
    return "".join(blocks[min(7, int((p - lo) / span * 7))] for p in pts)


# ── config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            corrupt = CONFIG_FILE + ".bak"
            log.warning("Config file corrupt (%s), resetting. Backup at %s", e, corrupt)
            try:
                os.replace(CONFIG_FILE, corrupt)
            except OSError:
                pass
    return {}


def save_config(cfg: dict):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def _notif_enabled(cfg: dict, key: str) -> bool:
    """Return True if the named notification is enabled (defaults to True)."""
    return cfg.get("notifications", {}).get(key, _NOTIF_DEFAULTS.get(key, True))


def _set_notif(cfg: dict, key: str, value: bool):
    """Persist a single notification toggle."""
    cfg.setdefault("notifications", {})[key] = value
    save_config(cfg)


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
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://claude.ai/settings/usage",
    "Origin": "https://claude.ai",
}
# Cloudflare fingerprint-checks Chrome aggressively; Safari passes cleanly.
_IMPERSONATE = "safari184"
# Cloudflare-bound cookies are tied to the real browser fingerprint —
# sending them from a different TLS stack causes a mismatch → 403.
_CF_COOKIE_KEYS = frozenset({"cf_clearance", "__cf_bm", "_cfuvid"})


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


def _strip_cf_cookies(cookies: dict) -> dict:
    return {k: v for k, v in cookies.items() if k not in _CF_COOKIE_KEYS}


def _get(url: str, cookies: dict) -> dict | list:
    r = requests.get(
        url, cookies=_strip_cf_cookies(cookies), headers=HEADERS, timeout=15,
        impersonate=_IMPERSONATE,
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
    clean = _strip_cf_cookies(cookies) if cookies else None
    r = requests.get(url, headers=headers, cookies=clean, timeout=10, impersonate=_IMPERSONATE)
    r.raise_for_status()
    return r.json()


_CHATGPT_HEADERS = {
    "Accept": "application/json",
    "Referer": "https://chatgpt.com/codex/settings/usage",
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


def fetch_copilot(cookie_str: str) -> ProviderData:
    """Fetch GitHub Copilot premium request usage via browser cookies."""
    cookies = parse_cookie_string(cookie_str)
    try:
        r = requests.get(
            "https://github.com/settings/billing/copilot_usage_card",
            cookies=_strip_cf_cookies(cookies),
            headers={
                "Accept": "application/json",
                "Referer": "https://github.com/settings/billing/premium_requests_usage",
            },
            timeout=10,
            impersonate=_IMPERSONATE,
        )
        r.raise_for_status()
        data = r.json()
        log.debug("copilot_usage_card: %s", json.dumps(data, indent=2))
        used = float(data.get("discountQuantity", 0))
        limit = float(data.get("userPremiumRequestEntitlement", 0))
        return ProviderData(
            "Copilot", spent=used, limit=limit or None,
            currency="", period="this month",
        )
    except Exception as e:
        log.debug("fetch_copilot failed: %s", e)
        return ProviderData("Copilot", error=str(e)[:80])


# Registry: config_key → (display_name, fetch_fn)
# chatgpt_cookies / copilot_cookies are cookie-based (auto-detected);
# others are API key-based.
PROVIDER_REGISTRY: dict[str, tuple[str, callable]] = {
    "chatgpt_cookies": ("ChatGPT",     fetch_chatgpt),
    "copilot_cookies": ("Copilot",     fetch_copilot),
    "openai_key":      ("OpenAI",      fetch_openai),
    "minimax_key":     ("MiniMax",     fetch_minimax),
    "glm_key":         ("GLM (Zhipu)", fetch_glm),
}

# Cookie-based providers (auto-detected from browser, not manually entered)
_COOKIE_PROVIDERS = {"chatgpt_cookies", "copilot_cookies"}


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
    # API returns 0-100 percentage for all fields (five_hour, seven_day, etc.)
    pct = min(100, round(raw))
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


# ── Brand icon helpers ────────────────────────────────────────────────────────

_ICON_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_ICON_SIZE  = 14   # points — matches menu bar font height
_icon_cache: dict = {}


def _bar_icon(filename: str, tint_hex: str | None = None):
    """Lazy-load and cache a menu bar icon (14×14 pt NSImage).

    tint_hex: e.g. '#74AA9C' — applied to monochrome (black) icons so they
              show in the brand color. Pass None for already-coloured icons.
    """
    key = (filename, tint_hex)
    if key in _icon_cache:
        return _icon_cache[key]
    img = None
    try:
        from AppKit import NSImage, NSColor
        path = os.path.join(_ICON_DIR, filename)
        raw = NSImage.alloc().initWithContentsOfFile_(path)
        if raw:
            img = raw.copy()
            img.setSize_((_ICON_SIZE, _ICON_SIZE))
            if tint_hex:
                r = int(tint_hex[1:3], 16) / 255
                g = int(tint_hex[3:5], 16) / 255
                b = int(tint_hex[5:7], 16) / 255
                color = NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)
                img.setTemplate_(True)
                if hasattr(img, "imageWithTintColor_"):
                    img = img.imageWithTintColor_(color)
    except Exception as e:
        log.debug("_bar_icon %s: %s", filename, e)
    _icon_cache[key] = img
    return img


def _icon_astr(img, base_attrs: dict):
    """Wrap an NSImage in an NSAttributedString via NSTextAttachment."""
    from AppKit import NSTextAttachment, NSAttributedString
    from Foundation import NSMakeRect, NSMutableAttributedString
    att = NSTextAttachment.alloc().init()
    att.setImage_(img)
    att.setBounds_(NSMakeRect(0, -3, _ICON_SIZE, _ICON_SIZE))
    astr = NSAttributedString.attributedStringWithAttachment_(att)
    m = NSMutableAttributedString.alloc().initWithAttributedString_(astr)
    for k, v in base_attrs.items():
        m.addAttribute_value_range_(k, v, (0, m.length()))
    return m


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


def _write_widget_cache(
    data: UsageData,
    providers: list[ProviderData],
    cc_stats: dict | None,
) -> None:
    """Write current usage snapshot for the WidgetKit widget.

    Writes to ~/Library/Group Containers/group.com.aiquotabar/usage.json
    using atomic replace so the widget never reads a partial file.
    Failures are logged but never crash the main app.
    """
    try:
        def _row_dict(row: LimitRow | None) -> dict | None:
            if row is None:
                return None
            return {"label": row.label, "pct": row.pct, "reset_str": row.reset_str}

        # ChatGPT rows
        chatgpt_pd = next((p for p in providers if p.name == "ChatGPT"), None)
        chatgpt_rows = None
        chatgpt_error = None
        if chatgpt_pd:
            if chatgpt_pd.error:
                chatgpt_error = chatgpt_pd.error
            else:
                raw_rows = getattr(chatgpt_pd, "_rows", None) or []
                chatgpt_rows = [_row_dict(r) for r in raw_rows]

        payload = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "claude": {
                "session": _row_dict(data.session),
                "weekly_all": _row_dict(data.weekly_all),
                "weekly_sonnet": _row_dict(data.weekly_sonnet),
                "overages_enabled": data.overages_enabled,
            },
            "chatgpt": {
                "rows": chatgpt_rows,
                "error": chatgpt_error,
            },
            "claude_code": {
                "today_messages": (cc_stats or {}).get("today_messages", 0),
                "week_messages": (cc_stats or {}).get("week_messages", 0),
            },
        }

        os.makedirs(WIDGET_CACHE_DIR, exist_ok=True)
        tmp = os.path.join(WIDGET_CACHE_DIR, ".usage.json.tmp")
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, WIDGET_CACHE_FILE)
        log.debug("widget cache written: %s", WIDGET_CACHE_FILE)

        # Nudge WidgetKit to reload (non-blocking, best-effort)
        subprocess.Popen(
            ["open", "-g", "-a", "AIQuotaBarHost", "--args", "--reload-widget"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        log.debug("_write_widget_cache failed", exc_info=True)


def _is_widget_installed() -> bool:
    """Check if the AIQuotaBarHost widget app is installed."""
    return os.path.isdir(WIDGET_HOST_APP)


def _show_welcome_window(gif_path: str, widget_installed: bool) -> None:
    """Show a native macOS welcome window with side-by-side GIFs."""
    from AppKit import (
        NSWindow, NSImageView, NSImage, NSTextField, NSButton, NSFont,
        NSMakeRect, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
        NSWindowStyleMaskFullSizeContentView,
        NSBackingStoreBuffered, NSTextAlignmentCenter,
        NSColor, NSBezelStyleRounded,
        NSApplication, NSFloatingWindowLevel, NSScreen,
        NSVisualEffectView, NSView,
    )
    import Quartz

    PAD = 24
    GAP = 16

    # Load both GIFs
    assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    demo_img = NSImage.alloc().initWithContentsOfFile_(
        os.path.join(assets_dir, "demo.gif")
    )
    widget_img = NSImage.alloc().initWithContentsOfFile_(gif_path)

    # Both GIFs at same height; widths from aspect ratios
    GIF_H = 480
    def _w_for_h(img, h):
        if not img:
            return 200
        iw, ih = img.size().width, img.size().height
        return int(h * iw / ih) if ih > 0 else 200

    # Left GIF: fixed wider width, fill-scaled (crops top/bottom)
    demo_w = 320
    widget_w = _w_for_h(widget_img, GIF_H)
    WIN_W = PAD + demo_w + GAP + widget_w + PAD
    WIN_H = 70 + GIF_H + 20 + 150 + 54  # header + gifs + labels + info + button

    # Centre on screen
    screen = NSScreen.mainScreen().frame()
    sx = (screen.size.width - WIN_W) / 2
    sy = (screen.size.height - WIN_H) / 2

    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(sx, sy, WIN_W, WIN_H),
        (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
         | NSWindowStyleMaskFullSizeContentView),
        NSBackingStoreBuffered,
        False,
    )
    win.setTitle_("")
    win.setTitlebarAppearsTransparent_(True)
    win.setTitleVisibility_(1)
    win.setLevel_(NSFloatingWindowLevel)
    win.setMovableByWindowBackground_(True)

    content = win.contentView()
    content.setWantsLayer_(True)

    # ── Vibrancy background ──
    blur = NSVisualEffectView.alloc().initWithFrame_(content.bounds())
    blur.setAutoresizingMask_(18)
    blur.setBlendingMode_(0)
    blur.setMaterial_(3)
    blur.setState_(1)
    content.addSubview_(blur)

    border_color = Quartz.CGColorCreateGenericRGB(1, 1, 1, 0.08)

    def _make_gif(img, x, y, w, h, fill=False):
        c = NSView.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        c.setWantsLayer_(True)
        c.layer().setCornerRadius_(10)
        c.layer().setMasksToBounds_(True)
        c.layer().setBorderWidth_(0.5)
        c.layer().setBorderColor_(border_color)
        content.addSubview_(c)
        if img:
            if fill:
                # Scale to fill: size image view to cover container, clip overflow
                iw, ih = img.size().width, img.size().height
                ratio = iw / ih if ih > 0 else 1.0
                # Scale by width → compute height needed
                iv_w = w
                iv_h = int(w / ratio)
                iv_y = h - iv_h  # align to top
                iv = NSImageView.alloc().initWithFrame_(
                    NSMakeRect(0, iv_y, iv_w, iv_h))
            else:
                iv = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
            iv.setImage_(img)
            iv.setAnimates_(True)
            iv.setImageScaling_(3)
            iv.setImageAlignment_(0)
            iv.setWantsLayer_(True)
            iv.layer().setMagnificationFilter_(Quartz.kCAFilterTrilinear)
            iv.layer().setMinificationFilter_(Quartz.kCAFilterTrilinear)
            iv.layer().setShouldRasterize_(True)
            iv.layer().setRasterizationScale_(3.0)
            c.addSubview_(iv)

    def _label(text, x, y, w, align=NSTextAlignmentCenter, size=11, weight=0.3):
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, 14))
        lbl.setStringValue_(text)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setAlignment_(align)
        lbl.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
        lbl.setTextColor_(NSColor.secondaryLabelColor())
        content.addSubview_(lbl)

    # ── Title + subtitle ──
    y_top = WIN_H - 52
    t = NSTextField.alloc().initWithFrame_(
        NSMakeRect(PAD, y_top, WIN_W - PAD * 2, 28)
    )
    t.setStringValue_("Welcome to AIQuotaBar")
    t.setBezeled_(False)
    t.setDrawsBackground_(False)
    t.setEditable_(False)
    t.setSelectable_(False)
    t.setAlignment_(NSTextAlignmentCenter)
    t.setFont_(NSFont.systemFontOfSize_weight_(20, 0.56))
    content.addSubview_(t)

    _label("Monitor your Claude and ChatGPT usage limits in real time.",
           PAD, y_top - 22, WIN_W - PAD * 2, size=12)

    # ── Side-by-side GIFs ──
    gif_y = y_top - 22 - 14 - GIF_H
    _make_gif(demo_img, PAD, gif_y, demo_w, GIF_H, fill=True)
    _label("Menu Bar", PAD, gif_y - 16, demo_w)

    widget_x = PAD + demo_w + GAP
    _make_gif(widget_img, widget_x, gif_y, widget_w, GIF_H)
    _label("Desktop Widget", widget_x, gif_y - 16, widget_w)

    # ── Info rows ──
    info_y = gif_y - 40
    inner_w = WIN_W - PAD * 2
    rows = [
        ("Menu Bar",
         "Click the diamond icon to see session limits, weekly caps, and reset times."),
        ("Desktop Widget",
         "Installed and synced. Right-click desktop > Edit Widgets > 'AI Quota'."
         if widget_installed else
         "Available to install. Check 'Desktop Widget' in the menu bar."),
        ("Auto Refresh",
         "Data updates every 60 seconds. Alerts at 80% and 95% usage."),
    ]
    for heading, desc in rows:
        h = NSTextField.alloc().initWithFrame_(
            NSMakeRect(PAD, info_y, inner_w, 16)
        )
        h.setStringValue_(heading)
        h.setBezeled_(False)
        h.setDrawsBackground_(False)
        h.setEditable_(False)
        h.setSelectable_(False)
        h.setAlignment_(NSTextAlignmentCenter)
        h.setFont_(NSFont.systemFontOfSize_weight_(12, 0.4))
        content.addSubview_(h)
        info_y -= 16

        d = NSTextField.alloc().initWithFrame_(
            NSMakeRect(PAD, info_y, inner_w, 14)
        )
        d.setStringValue_(desc)
        d.setBezeled_(False)
        d.setDrawsBackground_(False)
        d.setEditable_(False)
        d.setSelectable_(False)
        d.setAlignment_(NSTextAlignmentCenter)
        d.setFont_(NSFont.systemFontOfSize_(11))
        d.setTextColor_(NSColor.secondaryLabelColor())
        content.addSubview_(d)
        info_y -= 22

    # ── "Got it" button ──
    btn_w, btn_h = 120, 30
    btn = NSButton.alloc().initWithFrame_(
        NSMakeRect((WIN_W - btn_w) / 2, 14, btn_w, btn_h)
    )
    btn.setTitle_("Got it")
    btn.setBezelStyle_(NSBezelStyleRounded)
    btn.setKeyEquivalent_("\r")
    btn.setAction_(b"performClose:")
    btn.setTarget_(win)
    content.addSubview_(btn)

    win.makeKeyAndOrderFront_(None)
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    _show_welcome_window._active_win = win


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
    line1 = f"  {row.label}  {row.pct}%"
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
        bar = _bar(pd.pct)
        lines.append(f"  {sym}{pd.spent:.2f} / {sym}{pd.limit:.2f} {pd.period}")
        lines.append(f"  {bar}  {pd.pct}%")
    elif pd.balance is not None:
        lines.append(f"  {sym}{pd.balance:.2f} remaining")
    elif pd.spent is not None:
        lines.append(f"  {sym}{pd.spent:.2f} {pd.period}")
    return lines


def _mi(title: str) -> rumps.MenuItem:
    """Disabled (display-only) menu item."""
    item = rumps.MenuItem(title)
    item.set_callback(None)
    return item


def _colored_mi(title: str, color_hex: str) -> rumps.MenuItem:
    """Disabled menu item with brand-colored text."""
    item = rumps.MenuItem(title)
    item.set_callback(None)
    try:
        from AppKit import NSColor, NSForegroundColorAttributeName
        from Foundation import NSAttributedString
        r = int(color_hex[1:3], 16) / 255
        g = int(color_hex[3:5], 16) / 255
        b = int(color_hex[5:7], 16) / 255
        color = NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 0.75)
        astr = NSAttributedString.alloc().initWithString_attributes_(
            title, {NSForegroundColorAttributeName: color}
        )
        item._menuitem.setAttributedTitle_(astr)
    except Exception as e:
        log.debug("_colored_mi: %s", e)
    return item


def _menu_icon(filename: str, tint_hex: str | None = None, size: int = 16):
    """Load an NSImage for use in a menu item, optionally tinted."""
    try:
        from AppKit import NSImage, NSColor
        path = os.path.join(_ICON_DIR, filename)
        raw = NSImage.alloc().initWithContentsOfFile_(path)
        if not raw:
            return None
        img = raw.copy()
        img.setSize_((size, size))
        if tint_hex:
            r = int(tint_hex[1:3], 16) / 255
            g = int(tint_hex[3:5], 16) / 255
            b = int(tint_hex[5:7], 16) / 255
            color = NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)
            img.setTemplate_(True)
            if hasattr(img, "imageWithTintColor_"):
                img = img.imageWithTintColor_(color)
        return img
    except Exception as e:
        log.debug("_menu_icon %s: %s", filename, e)
        return None


def _section_header_mi(title: str, icon_filename: str | None,
                        color_hex: str, icon_tint: str | None = None) -> rumps.MenuItem:
    """Disabled section header with brand icon and colored bold title."""
    item = rumps.MenuItem(title)
    item.set_callback(None)
    try:
        from AppKit import (NSColor, NSFont,
                            NSForegroundColorAttributeName, NSFontAttributeName)
        from Foundation import NSAttributedString
        r = int(color_hex[1:3], 16) / 255
        g = int(color_hex[3:5], 16) / 255
        b = int(color_hex[5:7], 16) / 255
        color = NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)
        font = NSFont.boldSystemFontOfSize_(13)
        attrs = {NSFontAttributeName: font, NSForegroundColorAttributeName: color}
        astr = NSAttributedString.alloc().initWithString_attributes_(title, attrs)
        item._menuitem.setAttributedTitle_(astr)
        if icon_filename:
            img = _menu_icon(icon_filename, tint_hex=icon_tint)
            if img:
                item._menuitem.setImage_(img)
    except Exception as e:
        log.debug("_section_header_mi: %s", e)
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
    python_exe = sys.executable
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claudebar</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_exe}</string>
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

BROWSERS = [
    'firefox', 'librewolf', 'chrome', 'arc', 'brave',
    'edge', 'chromium', 'opera', 'vivaldi', 'safari',
]

# Collect candidates from every browser that has the target cookie.
# Pick the one with the latest expiry on the target cookie so we always
# use the freshest session (handles the case where the user is logged in
# to multiple browsers simultaneously).
candidates = []  # list of (expires, cookie_str)

try:
    import browser_cookie3
    for name in BROWSERS:
        fn = getattr(browser_cookie3, name, None)
        if fn is None:
            continue
        try:
            jar = fn(domain_name=domain)
            cookies = {x.name: x for x in jar}
            if target not in cookies:
                continue
            expires = cookies[target].expires or 0
            cookie_str = '; '.join(f'{k}={c.value}' for k, c in cookies.items())
            candidates.append((expires, cookie_str))
        except Exception:
            pass
except Exception:
    pass

# Best = latest expiry; tie-break by longest cookie string (richest jar)
result = None
if candidates:
    candidates.sort(key=lambda x: (x[0], len(x[1])), reverse=True)
    result = candidates[0][1]

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


def _auto_detect_copilot_cookies() -> str | None:
    """Detect github.com session cookies from the browser (crash-safe subprocess)."""
    if not _BROWSER_COOKIE3_OK:
        return None
    return _run_cookie_detection("github.com", "user_session")


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
        self._prev_pcts: dict[str, int] = {}  # previous pct per row key (reset detection)
        self._auth_fail_count = 0
        self._fetching = False
        self._last_updated: datetime | None = None

        self._refresh_interval = self.config.get("refresh_interval", DEFAULT_REFRESH)
        self._cc_stats: dict | None = None   # Claude Code local stats
        self._history = _load_history()       # usage history for burn rate / sparkline
        self._pacing_alerted: set[str] = set()  # track which providers we've pacing-alerted

        # Thread-safe UI update queue (background thread → main thread)
        self._ui_pending_title: str | None = None
        self._ui_pending_data: UsageData | None = None
        self._ui_lock = threading.Lock()

        if not _is_login_item():
            _add_login_item()

        self._rebuild_menu(None)
        self._timer = rumps.Timer(self._on_timer, self._refresh_interval)
        self._timer.start()
        # Fast ticker: drains pending UI updates on the main thread (avoids AppKit crashes)
        self._ui_ticker = rumps.Timer(self._flush_ui, 0.25)
        self._ui_ticker.start()

        # Deferred startup info (runs after the run loop is active)
        self._welcome_timer = rumps.Timer(self._deferred_welcome, 2)
        self._welcome_timer.start()

        # Always try to fetch on startup — browser JS works even without saved cookies
        self._schedule_fetch()

    # ── menu ─────────────────────────────────────────────────────────────────

    def _rebuild_menu(self, data: UsageData | None):
        items: list = []

        # ── ◆  CLAUDE section ─────────────────────────────────────────────
        items.append(_section_header_mi("  Claude", "claude_icon.png", "#D97757"))

        if data is None or not any([data.session, data.weekly_all, data.weekly_sonnet]):
            items.append(_mi("  No data — click Auto-detect from Browser"))
        else:
            if data.session:
                lines = _row_lines(data.session)
                items.append(_mi(lines[0]))
                items.append(_colored_mi(lines[1], "#D97757"))
                # ETA + sparkline for Claude session
                eta = _calc_eta_minutes(self._history, "claude")
                if eta is not None:
                    items.append(_mi(f"  ⏱ Limit in ~{eta} min"))
                spark = _sparkline(self._history, "claude")
                if spark:
                    items.append(_mi(f"  {spark}"))
                items.append(None)

            for row in [data.weekly_all, data.weekly_sonnet]:
                if row:
                    lines = _row_lines(row)
                    items.append(_mi(lines[0]))
                    items.append(_colored_mi(lines[1], "#D97757"))
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
            items.append(_section_header_mi("  ChatGPT", "chatgpt_icon_clean.png",
                                            "#74AA9C", icon_tint="#74AA9C"))
            rows = getattr(chatgpt_pd, "_rows", None)
            if rows:
                for row in rows:
                    lines = _row_lines(row)
                    items.append(_mi(lines[0]))
                    items.append(_colored_mi(lines[1], "#74AA9C"))
                    items.append(None)
            else:
                for line in _provider_lines(chatgpt_pd):
                    if line:
                        items.append(_mi(line))
                items.append(None)
            # ETA + sparkline for ChatGPT
            eta = _calc_eta_minutes(self._history, "chatgpt")
            if eta is not None:
                items.append(_mi(f"  ⏱ Limit in ~{eta} min"))
            spark = _sparkline(self._history, "chatgpt")
            if spark:
                items.append(_mi(f"  {spark}"))
                items.append(None)

        # ── ◇  COPILOT section (if detected) ─────────────────────────────────
        copilot_pd = next(
            (pd for pd in self._provider_data if pd.name == "Copilot"), None
        )
        if copilot_pd:
            items.append(_section_header_mi("  GitHub Copilot", None, "#6E40C9"))
            for line in _provider_lines(copilot_pd):
                if line:
                    items.append(_mi(line))
            # ETA + sparkline for Copilot
            eta = _calc_eta_minutes(self._history, "copilot")
            if eta is not None:
                items.append(_mi(f"  ⏱ Limit in ~{eta} min"))
            spark = _sparkline(self._history, "copilot")
            if spark:
                items.append(_mi(f"  {spark}"))
            items.append(None)

        # ── ◆  CLAUDE CODE section ────────────────────────────────────────────
        if self._cc_stats:
            cc = self._cc_stats
            items.append(_section_header_mi("  Claude Code", "claude_icon.png", "#D97757"))
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
            if pd.name in ("ChatGPT", "Copilot"):
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
        items.append(rumps.MenuItem("Share on X / Twitter…", callback=self._share_on_x))
        items.append(rumps.MenuItem("⭐ Star on GitHub", callback=self._open_github))
        items.append(None)

        # Refresh interval submenu
        interval_menu = rumps.MenuItem("Refresh Interval")
        for label, secs in REFRESH_INTERVALS.items():
            item = rumps.MenuItem(label, callback=self._make_interval_cb(secs, label))
            item._menuitem.setState_(1 if secs == self._refresh_interval else 0)
            interval_menu.add(item)
        items.append(interval_menu)

        # Notifications submenu
        notif_menu = rumps.MenuItem("Notifications")
        _notif_labels = [
            ("claude_warning",  "Claude — usage warnings (80% / 95%)"),
            ("claude_reset",    "Claude — reset alerts"),
            ("claude_pacing",   "Claude — pacing alert (ETA < 30 min)"),
            ("chatgpt_warning", "ChatGPT — usage warnings (80% / 95%)"),
            ("chatgpt_reset",   "ChatGPT — reset alerts"),
            ("chatgpt_pacing",  "ChatGPT — pacing alert (ETA < 30 min)"),
            ("copilot_pacing",  "Copilot — pacing alert (ETA < 30 min)"),
        ]
        for nkey, nlabel in _notif_labels:
            item = rumps.MenuItem(nlabel, callback=self._make_notif_toggle_cb(nkey))
            item._menuitem.setState_(1 if _notif_enabled(self.config, nkey) else 0)
            notif_menu.add(item)
        items.append(notif_menu)
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

        login_item = rumps.MenuItem("Launch at Login", callback=self._toggle_login_item)
        login_item._menuitem.setState_(1 if _is_login_item() else 0)
        items.append(login_item)

        # Desktop Widget status
        if _is_widget_installed():
            widget_item = rumps.MenuItem(
                "Desktop Widget  ✓  Installed",
                callback=self._open_widget_settings,
            )
        else:
            widget_item = rumps.MenuItem(
                "Desktop Widget  ·  Not Installed",
                callback=self._install_widget_prompt,
            )
        items.append(widget_item)

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

    # ── widget ─────────────────────────────────────────────────────────────────

    def _deferred_welcome(self, _timer):
        """Runs once after the run loop is active, then stops itself."""
        _timer.stop()
        self._check_widget_status()

    def _check_widget_status(self):
        """Show startup info about what the app is doing."""
        seen_welcome = self.config.get("seen_welcome", False)
        widget_ok = _is_widget_installed()

        if not seen_welcome:
            # First launch — show native welcome window with GIF
            gif_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "assets", "widget_info.gif",
            )
            if os.path.isfile(gif_path):
                _show_welcome_window(gif_path, widget_ok)
            else:
                # Fallback to notification if GIF missing
                rumps.notification(
                    title="Welcome to AIQuotaBar",
                    subtitle="Monitoring Claude + ChatGPT usage",
                    message="Click the diamond in your menu bar to get started.",
                    sound=True,
                )
            self.config["seen_welcome"] = True
            save_config(self.config)
        else:
            # Subsequent launches — brief notification
            if widget_ok:
                rumps.notification(
                    title="AIQuotaBar",
                    subtitle="Running",
                    message="Menu bar and desktop widget are synced.",
                    sound=False,
                )
            else:
                rumps.notification(
                    title="AIQuotaBar",
                    subtitle="Running",
                    message=(
                        "Tracking usage from your menu bar. "
                        "A desktop widget is also available — check the menu."
                    ),
                    sound=False,
                )

    def _open_widget_settings(self, _sender):
        """Open the widget host app (shows add-widget instructions)."""
        subprocess.Popen(
            ["open", "-a", "AIQuotaBarHost"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _install_widget_prompt(self, _sender):
        """Show instructions for building/installing the widget."""
        widget_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "AIQuotaBarWidget"
        )
        if os.path.isdir(widget_dir):
            build_script = os.path.join(widget_dir, "build_widget.sh")
            if os.path.isfile(build_script):
                rumps.alert(
                    title="Install Desktop Widget",
                    message=(
                        "To install the desktop widget, run in Terminal:\n\n"
                        f"cd {widget_dir}\n"
                        "./build_widget.sh\n\n"
                        "Then right-click desktop → Edit Widgets → search 'AI Quota'."
                    ),
                )
                return
        rumps.alert(
            title="Desktop Widget Not Found",
            message=(
                "The widget project was not found.\n\n"
                "Make sure the AIQuotaBarWidget folder exists "
                "in the AIQuotaBar directory."
            ),
        )

    # ── fetch ─────────────────────────────────────────────────────────────────

    def _on_timer(self, _timer):
        self._schedule_fetch()

    def _schedule_fetch(self):
        if self._fetching:
            return
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        self._fetching = True

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
            self._check_provider_warnings(self._provider_data)
            self._cc_stats = fetch_claude_code_stats()

            # ── record usage history ──
            if data.session:
                _append_history(self._history, "claude", data.session.pct)
            chatgpt_pd = next(
                (pd for pd in self._provider_data if pd.name == "ChatGPT"), None
            )
            if chatgpt_pd and not chatgpt_pd.error:
                rows = getattr(chatgpt_pd, "_rows", None)
                if rows:
                    worst = max(r.pct for r in rows)
                    _append_history(self._history, "chatgpt", worst)
            copilot_pd = next(
                (pd for pd in self._provider_data if pd.name == "Copilot"), None
            )
            if copilot_pd and not copilot_pd.error and copilot_pd.pct is not None:
                _append_history(self._history, "copilot", copilot_pd.pct)
            _save_history(self._history)

            self._check_pacing_alerts()

            self._post_data(data)          # ← main thread applies title + menu
            _write_widget_cache(data, self._provider_data, self._cc_stats)
        except CurlHTTPError as e:
            resp = getattr(e, "response", None)
            code = getattr(resp, "status_code", 0) or 0
            log.error("HTTP error: %s (status=%s)", e, code, exc_info=True)
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
        """Send macOS notification when a Claude limit crosses a threshold or resets."""
        rows = [
            (data.session,       "session"),
            (data.weekly_all,    "weekly_all"),
            (data.weekly_sonnet, "weekly_sonnet"),
        ]
        warn_enabled = _notif_enabled(self.config, "claude_warning")
        reset_enabled = _notif_enabled(self.config, "claude_reset")

        for row, key in rows:
            if row is None:
                continue
            warn_key = f"{key}_{WARN_THRESHOLD}"
            crit_key = f"{key}_{CRIT_THRESHOLD}"
            prev = self._prev_pcts.get(key)

            # Reset detection: pct dropped significantly (≥10 pp) from above-warn to below
            if (reset_enabled and prev is not None
                    and prev >= WARN_THRESHOLD and row.pct < WARN_THRESHOLD
                    and (prev - row.pct) >= 10):
                self._warned_pcts.discard(warn_key)
                self._warned_pcts.discard(crit_key)
                _notify(
                    "Claude Usage Bar ✅",
                    f"{row.label} has reset!",
                    f"Now at {row.pct}% — you're good to go.",
                )

            if warn_enabled:
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
                    self._warned_pcts.discard(warn_key)
                    self._warned_pcts.discard(crit_key)

            self._prev_pcts[key] = row.pct

    def _check_provider_warnings(self, provider_data: list):
        """Send macOS notification when ChatGPT rate limits cross a threshold or reset."""
        chatgpt_pd = next((pd for pd in provider_data if pd.name == "ChatGPT"), None)
        if chatgpt_pd is None or chatgpt_pd.error:
            return

        rows = getattr(chatgpt_pd, "_rows", None) or []
        warn_enabled = _notif_enabled(self.config, "chatgpt_warning")
        reset_enabled = _notif_enabled(self.config, "chatgpt_reset")

        for row in rows:
            key = f"chatgpt_{row.label}"
            warn_key = f"{key}_{WARN_THRESHOLD}"
            crit_key = f"{key}_{CRIT_THRESHOLD}"
            prev = self._prev_pcts.get(key)

            if (reset_enabled and prev is not None
                    and prev >= WARN_THRESHOLD and row.pct < WARN_THRESHOLD
                    and (prev - row.pct) >= 10):
                self._warned_pcts.discard(warn_key)
                self._warned_pcts.discard(crit_key)
                _notify(
                    "Claude Usage Bar ✅",
                    f"ChatGPT {row.label} has reset!",
                    f"Now at {row.pct}% — you're good to go.",
                )

            if warn_enabled:
                if row.pct >= CRIT_THRESHOLD and crit_key not in self._warned_pcts:
                    self._warned_pcts.add(crit_key)
                    _notify(
                        "Claude Usage Bar 🔴",
                        f"ChatGPT {row.label} is at {row.pct}%!",
                        row.reset_str or "Limit almost reached",
                    )
                elif row.pct >= WARN_THRESHOLD and warn_key not in self._warned_pcts:
                    self._warned_pcts.add(warn_key)
                    _notify(
                        "Claude Usage Bar 🟡",
                        f"ChatGPT {row.label} is at {row.pct}%",
                        row.reset_str or "Approaching limit",
                    )
                elif row.pct < WARN_THRESHOLD:
                    self._warned_pcts.discard(warn_key)
                    self._warned_pcts.discard(crit_key)

            self._prev_pcts[key] = row.pct

    def _check_pacing_alerts(self):
        """Send predictive notification when ETA drops below PACING_ALERT_MINUTES."""
        _pacing_map = [
            ("claude",  "claude_pacing",  "Claude session"),
            ("chatgpt", "chatgpt_pacing", "ChatGPT"),
            ("copilot", "copilot_pacing", "Copilot"),
        ]
        for hkey, nkey, label in _pacing_map:
            if not _notif_enabled(self.config, nkey):
                continue
            eta = _calc_eta_minutes(self._history, hkey)
            if eta is not None and eta <= PACING_ALERT_MINUTES:
                if hkey not in self._pacing_alerted:
                    self._pacing_alerted.add(hkey)
                    _notify(
                        "Claude Usage Bar ⏱",
                        f"Slow down — {label} limit in ~{eta} min",
                        "At your current pace you'll hit the cap soon.",
                    )
            else:
                # Pace slowed down — allow re-alerting if it picks up again
                self._pacing_alerted.discard(hkey)

    def _set_bar_title(self, pct: int, extra: str = "",
                       chatgpt_pct: int | None = None,
                       cc_msgs: int | None = None):
        """Multi-indicator attributed title with brand logo icons.

        [Claude icon] 36%  [ChatGPT icon] 0%  ◆ 3.2k

        Falls back to colored text symbols if AppKit / icons unavailable.
        """
        try:
            from AppKit import (NSColor, NSFont,
                                NSForegroundColorAttributeName, NSFontAttributeName)
            from Foundation import NSMutableAttributedString, NSAttributedString

            def _rgb(hex_str):
                r = int(hex_str[1:3], 16) / 255
                g = int(hex_str[3:5], 16) / 255
                b = int(hex_str[5:7], 16) / 255
                return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)

            CLAUDE_COLOR  = _rgb("#D97757")
            CHATGPT_COLOR = _rgb("#74AA9C")

            font = NSFont.menuBarFontOfSize_(0)
            base = {NSFontAttributeName: font} if font else {}

            s = NSMutableAttributedString.alloc().initWithString_("",)

            # ── Claude.ai  [icon] 36% ───────────────────────
            claude_img = _bar_icon("claude_icon.png")
            if claude_img:
                s.appendAttributedString_(_icon_astr(claude_img, base))
            else:
                seg = NSMutableAttributedString.alloc().initWithString_attributes_("● ", base)
                seg.addAttribute_value_range_(NSForegroundColorAttributeName, CLAUDE_COLOR, (0, 2))
                s.appendAttributedString_(seg)
            s.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_(f" {pct}%{extra}", base)
            )

            # ── ChatGPT  [icon] 0% ──────────────────────────
            if chatgpt_pct is not None:
                s.appendAttributedString_(
                    NSAttributedString.alloc().initWithString_attributes_("   ", base)
                )
                chatgpt_img = _bar_icon("chatgpt_icon_clean.png", tint_hex="#74AA9C")
                if chatgpt_img:
                    s.appendAttributedString_(_icon_astr(chatgpt_img, base))
                else:
                    seg = NSMutableAttributedString.alloc().initWithString_attributes_("◇ ", base)
                    seg.addAttribute_value_range_(NSForegroundColorAttributeName, CHATGPT_COLOR, (0, 2))
                    s.appendAttributedString_(seg)
                s.appendAttributedString_(
                    NSAttributedString.alloc().initWithString_attributes_(f" {chatgpt_pct}%", base)
                )

            # ── Claude Code  ◆ 3.2k ────────────────────────
            if cc_msgs is not None and cc_msgs > 0:
                seg = NSMutableAttributedString.alloc().initWithString_attributes_(
                    f"   ◆ {_fmt_count(cc_msgs)}", base
                )
                seg.addAttribute_value_range_(NSForegroundColorAttributeName, CLAUDE_COLOR, (3, 2))
                s.appendAttributedString_(seg)

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
        _cookie_detectors = {
            "chatgpt_cookies": _auto_detect_chatgpt_cookies,
            "copilot_cookies": _auto_detect_copilot_cookies,
        }
        for cfg_key in _COOKIE_PROVIDERS:
            if not self.config.get(cfg_key):
                detect_fn = _cookie_detectors.get(cfg_key)
                if detect_fn:
                    ck = detect_fn()
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

    def _open_github(self, _sender):
        subprocess.Popen(["open", "https://github.com/yagcioglutoprak/AIQuotaBar"])

    def _share_on_x(self, _sender):
        data = self._last_data
        if data and data.session:
            pct = int(data.session.pct)
            icon = _status_icon(pct)
            text = (
                f"I'm at {pct}% of my Claude session limit {icon}\n"
                f"Tracking Claude.ai + ChatGPT usage live in my macOS menu bar "
                f"— zero setup, auto-detects from browser\n"
                f"github.com/yagcioglutoprak/AIQuotaBar"
            )
        else:
            text = (
                "Track Claude.ai + ChatGPT usage live in your macOS menu bar "
                "— zero setup, auto-detects from browser\n"
                "github.com/yagcioglutoprak/AIQuotaBar"
            )
        url = "https://x.com/intent/post?text=" + urllib.parse.quote(text)
        subprocess.Popen(["open", url])

    def _make_provider_key_cb(self, cfg_key: str, name: str):
        def _cb(_sender):
            if cfg_key in _COOKIE_PROVIDERS:
                # Cookie-based: re-run auto-detect
                _detectors = {
                    "chatgpt_cookies": _auto_detect_chatgpt_cookies,
                    "copilot_cookies": _auto_detect_copilot_cookies,
                }
                detect_fn = _detectors.get(cfg_key)
                if detect_fn:
                    ck = detect_fn()
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

    def _make_notif_toggle_cb(self, nkey: str):
        def _cb(sender):
            current = _notif_enabled(self.config, nkey)
            _set_notif(self.config, nkey, not current)
            sender._menuitem.setState_(0 if current else 1)
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
                "Paste ALL cookies from claude.ai (needed to bypass Cloudflare)\n\n"
                "How to get them:\n"
                "  1. Open https://claude.ai/settings/usage in Chrome\n"
                "  2. F12 → Network tab → click any request to claude.ai\n"
                "  3. In Headers, find the 'cookie:' row\n"
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

    def _toggle_login_item(self, sender):
        if _is_login_item():
            _remove_login_item()
            sender._menuitem.setState_(0)
            _notify("Claude Usage Bar", "Removed from Login Items", "")
        else:
            _add_login_item()
            sender._menuitem.setState_(1)
            _notify("Claude Usage Bar", "Added to Login Items", "Will launch automatically on login")

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
