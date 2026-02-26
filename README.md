# Claude Usage Bar

A lightweight macOS menu bar app that shows your [Claude.ai](https://claude.ai) usage limits in real time — right from your status bar.

![Claude Usage Bar screenshot](screenshot.png)

## Features

- **Current session** usage (5-hour plan limit)
- **Weekly limits** — All models & Sonnet only
- **Extra usage** toggle status
- Auto-refreshes every 5 minutes
- Native macOS menu bar, no Electron

## Requirements

- macOS 12+
- Python 3.10+
- A Claude.ai account (any paid plan)

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/ClaudeUsageBar.git
cd ClaudeUsageBar
bash setup.sh
python3 claude_bar.py
```

> **Tip:** To launch at login, add `claude_bar.py` to  
> **System Settings → General → Login Items**.

## Getting Your Session Cookie

The app needs your Claude.ai session cookie to fetch usage data.

1. Open [claude.ai/settings/usage](https://claude.ai/settings/usage) in Chrome
2. Press **F12** → open the **Network** tab
3. Click any request to `claude.ai`
4. In **Headers**, find the `cookie:` row
5. Right-click → **Copy value** (a long string with semicolons)
6. Click **Set Session Cookie…** in the menu bar app and paste it

Your cookie is stored locally in `~/.claude_bar_config.json` and never sent anywhere except to `claude.ai`.

## How It Works

The app calls the private `claude.ai` usage API (the same one used by the settings page) using a Chrome TLS fingerprint via [`curl_cffi`](https://github.com/yifeikong/curl_cffi) to bypass Cloudflare bot protection.

| API field | Displayed as |
|---|---|
| `five_hour` | Current Session |
| `seven_day` | All Models (weekly) |
| `seven_day_sonnet` | Sonnet Only (weekly) |
| `extra_usage` | Extra Usage toggle |

## Files

| File | Purpose |
|---|---|
| `claude_bar.py` | Main application |
| `setup.sh` | One-time dependency installer |

## Logs & Debugging

Logs are written to `~/.claude_bar.log`. Raw API data can be inspected via **Show Raw API Data…** in the menu.

## Contributing

Pull requests welcome! Please open an issue first for major changes.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This project is not affiliated with or endorsed by Anthropic. It uses undocumented internal APIs that may change without notice.
