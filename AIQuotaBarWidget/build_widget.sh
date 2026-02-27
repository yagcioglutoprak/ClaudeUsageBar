#!/bin/bash
# Build the AIQuotaBar WidgetKit widget
# Requires: Xcode 15+, macOS 14+

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
BUILD_DIR="$PROJECT_DIR/build"
APP_NAME="AIQuotaBarHost.app"

echo ""
echo "  AIQuotaBar Widget — builder"
echo "  ───────────────────────────"
echo ""

# ── Check Xcode ──────────────────────────────────────────────────────────────
if ! command -v xcodebuild &>/dev/null; then
    echo "  ✗  Xcode not found. Install from the App Store."
    echo "     The widget is optional — the menu bar app works without it."
    exit 1
fi

XCODE_VER=$(xcodebuild -version 2>/dev/null | head -1 | awk '{print $2}')
echo "  ✓  Xcode: $XCODE_VER"

# ── Check for project ────────────────────────────────────────────────────────
if [ ! -d "$PROJECT_DIR/AIQuotaBarWidget.xcodeproj" ]; then
    echo ""
    echo "  ✗  No Xcode project found."
    echo ""
    echo "  To create the project:"
    echo "    1. Open Xcode → File → New → Project"
    echo "    2. Choose macOS → App"
    echo "    3. Product Name: AIQuotaBarHost"
    echo "    4. Add Widget Extension target: AIQuotaBarWidgetExtension"
    echo "    5. Set App Group: group.com.aiquotabar on both targets"
    echo "    6. Drag the existing Swift files into the project"
    echo ""
    echo "  Alternatively, open this directory in Xcode and it will"
    echo "  detect the source files automatically."
    echo ""
    echo "  For detailed instructions, see the README."
    exit 1
fi

# ── Build ────────────────────────────────────────────────────────────────────
echo "  ↓  Building widget…"
xcodebuild \
    -project "$PROJECT_DIR/AIQuotaBarWidget.xcodeproj" \
    -scheme AIQuotaBarHost \
    -configuration Release \
    -derivedDataPath "$BUILD_DIR" \
    CODE_SIGN_IDENTITY="-" \
    CODE_SIGNING_REQUIRED=NO \
    CODE_SIGNING_ALLOWED=NO \
    DEVELOPMENT_TEAM="" \
    2>&1 | tail -5

# ── Install ──────────────────────────────────────────────────────────────────
BUILT_APP=$(find "$BUILD_DIR" -name "$APP_NAME" -type d | head -1)
if [ -z "$BUILT_APP" ]; then
    echo "  ✗  Build failed — app bundle not found."
    exit 1
fi

INSTALL_PATH="/Applications/$APP_NAME"
echo "  ↓  Installing to $INSTALL_PATH…"
rm -rf "$INSTALL_PATH"
cp -R "$BUILT_APP" "$INSTALL_PATH"

# Launch once to register the widget with the system
open "$INSTALL_PATH"
sleep 2
osascript -e 'quit app "AIQuotaBarHost"' 2>/dev/null || true

echo "  ✓  Widget installed!"
echo ""
echo "  Right-click your desktop → Edit Widgets → search \"AI Quota\""
echo ""
