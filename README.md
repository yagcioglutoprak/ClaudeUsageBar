# Claude Usage Bar

**See your Claude.ai usage limits live in your macOS menu bar.**

No Electron. No browser extension. One command to install.

![Claude Usage Bar demo](assets/demo.gif)

[![macOS](https://img.shields.io/badge/macOS-12%2B-black?logo=apple)](https://www.apple.com/macos/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/yagcioglutoprak/ClaudeUsageBar?style=social)](https://github.com/yagcioglutoprak/ClaudeUsageBar/stargazers)

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/ClaudeUsageBar/main/install.sh | bash
```

That's it. The app launches immediately and auto-detects your Claude session from Chrome, Arc, Firefox, or Safari — no copy-pasting cookies.

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
PLAN USAGE LIMITS

  🟢 Current Session
  ██░░░░░░░░░░░░  12%
  resets in 3h 41m

WEEKLY LIMITS

  🟡 All Models
  ████████████░░  83%
  resets Wed 23:00

  🟢 Sonnet Only
  ███░░░░░░░░░░░  22%
  resets Wed 23:00
```

---

## Features

- **Zero-setup auth** — reads cookies directly from your browser (Chrome, Arc, Brave, Edge, Firefox, Safari)
- **Multi-provider** — add OpenAI, MiniMax, GLM (Zhipu) API keys to see spending alongside Claude usage
- **Auto-refresh on session expiry** — silently grabs fresh cookies when your session expires
- **macOS notifications** — alerts at 80% and 95% usage
- **Configurable refresh** — 1 / 5 / 15 min
- **Runs at login** — via LaunchAgent, toggle from the menu
- **Tiny footprint** — ~900 lines of Python, no Electron, no background services beyond the app itself

---

## Requirements

- macOS 12+
- Python 3.10+
- A Claude.ai paid account
- Chrome, Arc, Brave, Edge, Firefox, or Safari with an active Claude session

---

## Manual install

```bash
git clone https://github.com/yagcioglutoprak/ClaudeUsageBar.git
cd ClaudeUsageBar
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

## Contributing

PRs welcome. Open an issue first for large changes.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

Not affiliated with or endorsed by Anthropic. Uses undocumented internal APIs that may change without notice.
