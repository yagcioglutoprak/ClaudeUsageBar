# AIQuotaBar

**Stop getting rate-limited by surprise.** See your Claude, ChatGPT, Cursor, and Copilot usage live in the macOS menu bar.

No Electron. No browser extension. One command to install.

<p align="center">
<img src="assets/demo.gif" alt="Menu Bar" width="380">
</p>
<p align="center">
<img src="assets/widget_info.gif" alt="Desktop Widget" width="600">
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/yagcioglutoprak/AIQuotaBar?style=social)](https://github.com/yagcioglutoprak/AIQuotaBar/stargazers)
[![Downloads](https://img.shields.io/github/downloads/yagcioglutoprak/AIQuotaBar/total)](https://github.com/yagcioglutoprak/AIQuotaBar/releases)
[![Latest Release](https://img.shields.io/github/v/release/yagcioglutoprak/AIQuotaBar)](https://github.com/yagcioglutoprak/AIQuotaBar/releases/latest)

**Featured on Hacker News (161 points, 49 comments) · Product Hunt (382 upvotes)**

---

## Install

**One-line (recommended):**
```bash
curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash
```

**Homebrew:**
```bash
brew tap yagcioglutoprak/aiquotabar
brew install --HEAD aiquotabar
aiquotabar &
```

The app launches immediately and auto-detects your Claude, ChatGPT, Cursor, and Copilot sessions from Chrome, Arc, Firefox, or Safari — no copy-pasting cookies.

---

### Why I built this

I kept getting cut off mid-session on Claude Pro with zero warning. Claude.ai doesn't show your usage until you hit the wall. Same with ChatGPT, Cursor, and Copilot. So I built a tiny menu bar app that shows them all.

---

## What it shows

| Menu bar | Meaning |
|---|---|
| 🟢 12% | Session usage is low — you're good |
| 🟡 83% | Approaching the 5-hour limit |
| 🔴 100% | Rate-limited — shows time until reset |
| 🔴 100% · | Session is fine but weekly limit is maxed |

Open the menu for full detail:

```
CLAUDE

  🟢 Current Session
  ██░░░░░░░░░░░░  12%
  resets in 3h 41m

  🟡 All Models
  ████████████░░  83%
  resets Wed 23:00

  🟢 Sonnet Only
  ███░░░░░░░░░░░  22%
  resets Wed 23:00

CHATGPT

  🟢 Codex Tasks
  █░░░░░░░░░░░░░  0%
  resets Thu 05:38

GITHUB COPILOT

  0 / 300 this month
  █░░░░░░░░░░░░░  0%

CURSOR

  🟢 Auto
  █░░░░░░░░░░░░░  0%
  resets in 27d

  🟢 API
  █░░░░░░░░░░░░░  0%
  resets in 27d
```

---

## Desktop Widget (NEW)

Native macOS WidgetKit widget — see your AI usage right on your desktop or in Notification Center.

**Small widget:** Claude + ChatGPT percentages at a glance, color-coded by brand.

**Medium widget:** Side-by-side breakdown with session limits, weekly caps, progress bars, and reset times.

The widget syncs automatically with the menu bar app — no extra setup. Data updates every 60 seconds.

```bash
# Build the widget (requires Xcode)
cd AIQuotaBarWidget && ./build_widget.sh
# Then: right-click desktop → Edit Widgets → search "AI Quota"
```

> The widget is entirely optional — the menu bar app works without it. Requires macOS 14+ and Xcode 15+.

---

## Features

- **Zero-setup auth** — reads cookies directly from your browser (Chrome, Arc, Brave, Edge, Firefox, Safari)
- **Claude + ChatGPT + Cursor + Copilot** — tracks Claude.ai session/weekly limits, ChatGPT rate limits, Cursor Auto/API usage, and GitHub Copilot premium requests — all in one place
- **Desktop widget** — native macOS WidgetKit widget with brand-colored progress bars
- **Multi-provider** — add OpenAI, MiniMax, GLM (Zhipu) API keys to see spending alongside usage
- **Burn rate + ETA** — predicts when you'll hit each limit based on your current pace
- **Pacing alerts** — notifies you when you're on track to hit a limit within 30 minutes
- **Auto-refresh on session expiry** — silently grabs fresh cookies when your session expires
- **macOS notifications** — alerts at 80% and 95% usage for Claude, ChatGPT, and Cursor
- **Configurable refresh** — 1 / 5 / 15 min
- **Runs at login** — via LaunchAgent, toggle from the menu
- **Tiny footprint** — ~900 lines of Python, no Electron, no background services beyond the app itself

---

## Why not just check the settings page?

| | AIQuotaBar | Open settings page | Browser extension |
|---|---|---|---|
| Always visible | ✅ Menu bar + desktop widget | ❌ Manual tab switch | ⚠️ Badge only |
| Notifications | ✅ 80% + 95% + pacing alerts | ❌ None | ⚠️ Varies |
| Claude + ChatGPT + Cursor + Copilot | ✅ All in one place | ❌ One at a time | ❌ |
| Desktop widget | ✅ Native WidgetKit | ❌ | ❌ |
| Privacy | ✅ Local only | ✅ | ⚠️ Depends on extension |
| Install | ✅ One command | ✅ Nothing | ❌ Store + permissions |
| No Electron | ✅ ~900 lines Python | ✅ | ❌ Often Electron |

---

## Requirements

- macOS 12+
- Python 3.10+
- A Claude.ai paid account
- Chrome, Arc, Brave, Edge, Firefox, or Safari with an active Claude session

---

## Manual install

```bash
git clone https://github.com/yagcioglutoprak/AIQuotaBar.git
cd AIQuotaBar
pip install -r requirements.txt
python3 claude_bar.py
```

---

## How it works

The app calls the same private usage API that `claude.ai/settings/usage` uses. It authenticates using your browser's existing session cookies (read locally — never transmitted anywhere except to `claude.ai`).

[`curl_cffi`](https://github.com/yifeikong/curl_cffi) is used to mimic a Chrome TLS fingerprint, which is required to pass Cloudflare's bot protection.

| API field | Displayed as |
|---|---|
| `five_hour` | Current Session |
| `seven_day` | All Models (weekly) |
| `seven_day_sonnet` | Sonnet Only (weekly) |
| `extra_usage` | Extra Usage toggle |

---

## Troubleshooting

**App doesn't appear in menu bar**
```bash
tail -50 ~/.claude_bar.log
```

**Cookies not detected**
Make sure you're logged into [claude.ai](https://claude.ai) in your browser, then click **Auto-detect from Browser** in the menu.

**Session expired / showing ◆ !**
The app will try to auto-detect fresh cookies from your browser. If that fails, click **Set Session Cookie…**.

---

## Roadmap

- [x] Homebrew tap (`brew tap yagcioglutoprak/aiquotabar && brew install --HEAD aiquotabar`)
- [x] Native macOS desktop widget (WidgetKit)
- [x] Cursor IDE usage tracking (Auto + API)
- [x] GitHub Copilot premium request tracking
- [x] Burn rate ETA + pacing alerts
- [ ] Linux system tray support
- [ ] Windows tray app
- [ ] Customizable notification thresholds
- [ ] Usage history graph
- [ ] Multiple Claude account support

---

## Contributing

PRs welcome. Open an issue first for large changes. See [Manual install](#manual-install) for dev setup. Logs: `~/.claude_bar.log`.

---

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

Not affiliated with or endorsed by Anthropic. Uses undocumented internal APIs that may change without notice.
