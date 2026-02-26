# CLAUDE.md — Claude Usage Bar

## Project goal

**Reach as many Claude users as possible on GitHub.**
Every change should make the app easier to install, more reliable, and more shareable.
The north star metric is GitHub stars — driven by a great README, zero-friction install, and a working app.

## What this is

A native macOS menu bar app (Python + rumps) that shows live Claude.ai usage limits.
It reads cookies from the user's browser (no manual copy-paste), calls the private
`claude.ai/api/organizations/{org}/usage` endpoint, and displays the result as a
status bar icon (`🟢 4%`, `🟡 83%`, `🔴 100%`).

## Architecture

Single file: `claude_bar.py` (~900 lines). No build step. No framework.

```
claude_bar.py
├── Config         load_config / save_config  (~/.claude_bar_config.json)
├── Claude API     fetch_raw → _get / _org_id_from_api
├── Provider APIs  fetch_openai / fetch_minimax / fetch_glm → ProviderData
│                  PROVIDER_REGISTRY: cfg_key → (name, fetch_fn)
├── Parser         parse_usage → UsageData(session, weekly_all, weekly_sonnet)
├── Display        _bar / _status_icon / _row_lines / _provider_lines
├── Cookie mgmt    _auto_detect_cookies → browser-cookie3 (Firefox first, then Chromium)
└── App            ClaudeBar(rumps.App) — timer, menu rebuild, callbacks
```

## Adding a new provider

1. Write `fetch_myprovider(api_key: str) -> ProviderData` — return `ProviderData` with `spent`/`limit` or `balance`
2. Add one entry to `PROVIDER_REGISTRY`: `"myprovider_key": ("MyProvider", fetch_myprovider)`
3. That's it — the menu item, key dialog, and display are all automatic.

## Key decisions to preserve

- **Session (5-hour) drives the status bar icon**, not the max of all limits.
  Weekly limits appear in the menu only. Rationale: session determines immediate access.
- **Firefox/LibreWolf first** in browser detection order — no Keychain prompt, zero friction.
  Chromium browsers (Arc, Chrome, Brave) come after; they need one-time "Always Allow".
- **API utilization scale is inconsistent**: `five_hour` returns 0–1 fraction,
  `seven_day` / `seven_day_sonnet` return 0–100 percentage.
  Fix: `raw > 1.0 → already percentage, else multiply by 100`.
- **`rumps.notification` crashes** in dev (missing Info.plist CFBundleIdentifier).
  All notifications go through `_notify()` which swallows the exception silently.
- **Cookies are cached** in `~/.claude_bar_config.json`. Auto-detect runs on first launch
  and on repeated 401/403 failures to silently refresh the session.

## API behaviour (confirmed)

```
GET https://claude.ai/api/organizations/{org_id}/usage
```
Requires Cloudflare bypass — use `curl_cffi` with `impersonate="chrome131"`.

Response fields:
| Field              | Meaning                        | Utilization scale |
|--------------------|--------------------------------|-------------------|
| `five_hour`        | Current session (5-hr limit)   | 0–1 fraction      |
| `seven_day`        | Weekly all-models              | 0–100 percentage  |
| `seven_day_sonnet` | Weekly Sonnet-only             | 0–100 percentage  |
| `extra_usage`      | Overage toggle (null = off)    | —                 |

## Files

| File            | Purpose                                              |
|-----------------|------------------------------------------------------|
| `claude_bar.py` | Entire application                                   |
| `install.sh`    | One-line curl installer (detects Python, LaunchAgent)|
| `requirements.txt` | `rumps`, `curl_cffi`, `browser-cookie3`           |
| `setup.sh`      | Legacy manual installer (kept for reference)         |
| `assets/`       | demo.gif and screenshots for README                  |

## Growth / virality rules

- **README is a marketing page.** Keep the one-line install prominent at the top.
  Never bury it below a wall of text.
- **The demo GIF is the #1 driver of stars.** `assets/demo.gif` must exist and be compelling.
  It should show: app launching → auto-detecting cookies → live percentage in menu bar.
- **Zero-friction install is non-negotiable.**
  `curl -fsSL .../install.sh | bash` must work end-to-end without manual steps.
  If it breaks, fix it before anything else.
- **Keep the README concise.** One install command, one screenshot, short feature list.
  Long docs belong in a wiki, not the README.
- **GitHub topics to maintain** (set via repo Settings → About):
  `claude`, `anthropic`, `macos`, `menu-bar`, `usage-monitor`, `menubar-app`, `claude-ai`

## Dev workflow

```bash
# Run locally
python3 claude_bar.py

# Check logs
tail -f ~/.claude_bar.log

# Quick syntax check
python3 -m py_compile claude_bar.py

# Kill and restart
pkill -f claude_bar.py; sleep 1; python3 claude_bar.py &
```

## Do not

- Do not add a `session_key` field — the app uses full cookie strings, not just the session key.
- Do not multiply all utilization values by 100 — `five_hour` is already a fraction but `seven_day` fields are already percentages.
- Do not call `rumps.notification()` directly — always use `_notify()`.
- Do not store cookies in plaintext anywhere other than `~/.claude_bar_config.json` (which is gitignored).
- Do not add Electron, a web server, or any always-on background process beyond the menu bar app itself.
- Do not make the README longer — keep it short and punchy for maximum conversion to stars.
