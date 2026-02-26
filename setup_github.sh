#!/bin/bash
# Run this once to set GitHub repo topics, description, and create v1.0.0 release
# Requires: gh CLI authenticated (run `gh auth login` first)

set -e

REPO="yagcioglutoprak/AIQuotaBar"

echo "Setting repo description and topics..."
gh api repos/$REPO \
  --method PATCH \
  -f description="See your Claude.ai usage limits live in your macOS menu bar" \
  -f homepage="https://github.com/yagcioglutoprak/AIQuotaBar"

gh api repos/$REPO/topics \
  --method PUT \
  -f 'names[]=macos' \
  -f 'names[]=menubar' \
  -f 'names[]=claude' \
  -f 'names[]=anthropic' \
  -f 'names[]=ai' \
  -f 'names[]=usage-tracker' \
  -f 'names[]=developer-tools' \
  -f 'names[]=python' \
  -f 'names[]=claude-ai' \
  -f 'names[]=menu-bar-app'

echo "Creating v1.0.0 release..."
gh release create v1.0.0 \
  --repo $REPO \
  --title "v1.0.0 — Initial Release" \
  --notes "## Claude Usage Bar v1.0.0

See your Claude.ai usage limits live in your macOS menu bar.

### Install
\`\`\`bash
curl -fsSL https://raw.githubusercontent.com/yagcioglutoprak/AIQuotaBar/main/install.sh | bash
\`\`\`

### What's included
- Real-time session (5-hour) and weekly usage display
- Color-coded menu bar indicator (green/yellow/red)
- macOS notifications at 80% and 95%
- Auto-detection from Chrome, Arc, Brave, Edge, Firefox, Safari
- Multi-provider support (OpenAI, MiniMax, GLM)
- Configurable refresh: 1 / 5 / 15 min
- Launch at login via LaunchAgent
- ~900 lines Python, no Electron"

echo "Done. Check https://github.com/$REPO"
