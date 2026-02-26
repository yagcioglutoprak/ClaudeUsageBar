#!/bin/zsh
# One-time setup for Claude Usage Bar

set -e

echo "Installing dependencies…"
pip install -r requirements.txt

echo ""
echo "Done. To run:"
echo "  python3 claude_bar.py"
echo ""
echo "To run at login, add it to System Settings → General → Login Items."
