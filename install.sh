#!/bin/bash
# Claude Usage Bar — one-line installer
# Usage: curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/ClaudeUsageBar/main/install.sh | bash

set -e

REPO="https://github.com/yagcioglutoprak/ClaudeUsageBar"
INSTALL_DIR="$HOME/.claude-usage-bar"
PLIST="$HOME/Library/LaunchAgents/com.claudebar.plist"

echo ""
echo "  Claude Usage Bar — installer"
echo "  ────────────────────────────"
echo ""

# ── 1. Python 3.10+ ───────────────────────────────────────────────────────────
PYTHON=""
for candidate in python3 python3.12 python3.11 python3.10; do
  if command -v "$candidate" &>/dev/null; then
    VER=$("$candidate" -c 'import sys; print(sys.version_info >= (3,10))' 2>/dev/null)
    if [ "$VER" = "True" ]; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo "  ✗  Python 3.10+ not found."
  echo "     Install it from https://www.python.org/downloads/ and re-run."
  exit 1
fi
echo "  ✓  Python: $($PYTHON --version)"

# ── 2. Clone / update ─────────────────────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "  ↻  Updating existing install…"
  git -C "$INSTALL_DIR" pull --quiet
else
  echo "  ↓  Cloning repository…"
  git clone --quiet "$REPO" "$INSTALL_DIR"
fi

# ── 3. Dependencies ───────────────────────────────────────────────────────────
echo "  ↓  Installing Python dependencies…"
"$PYTHON" -m pip install --quiet --upgrade rumps curl_cffi browser-cookie3

# Fix rumps notification centre (requires CFBundleIdentifier in Info.plist)
PYTHON_BIN=$(dirname "$("$PYTHON" -c 'import sys; print(sys.executable)')")
PLIST_PATH="$PYTHON_BIN/Info.plist"
if [ ! -f "$PLIST_PATH" ]; then
  /usr/libexec/PlistBuddy -c 'Add :CFBundleIdentifier string "rumps"' "$PLIST_PATH" 2>/dev/null || true
fi
echo "  ✓  Dependencies installed"

# ── 4. LaunchAgent (run at login) ─────────────────────────────────────────────
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claudebar</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$INSTALL_DIR/claude_bar.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardErrorPath</key>
    <string>$HOME/.claude_bar.log</string>
</dict>
</plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "  ✓  Added to Login Items (runs at every login)"

# ── 5. Launch now ─────────────────────────────────────────────────────────────
pkill -f claude_bar.py 2>/dev/null || true
sleep 1
"$PYTHON" "$INSTALL_DIR/claude_bar.py" &>/dev/null &
echo "  ✓  Launched!"
echo ""
echo "  Look for the ◆ icon in your menu bar."
echo "  It will auto-detect your Claude session from your browser."
echo ""
