#!/bin/bash
# etyper installer - sets up dependencies and systemd service

set -e

echo "=== etyper installer ==="

# Install system dependencies
echo "Installing dependencies..."
apt-get update -qq
apt-get install -y python3-spidev python3-libgpiod python3-pil python3-evdev

# Create documents directory
DOCS_DIR="$HOME/etyper_docs"
mkdir -p "$DOCS_DIR"
echo "Documents directory: $DOCS_DIR"

# Install systemd service (optional)
read -p "Install as boot service (auto-start on boot)? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    SERVICE_FILE="/etc/systemd/system/etyper.service"

    # Update service file with correct path
    sed "s|__INSTALL_DIR__|$SCRIPT_DIR|g" "$SCRIPT_DIR/etyper.service" > "$SERVICE_FILE"

    systemctl daemon-reload
    systemctl enable etyper
    echo "Service installed. Will start on next boot."
    echo "  Start now:  sudo systemctl start etyper"
    echo "  Stop:       sudo systemctl stop etyper"
    echo "  Logs:       journalctl -u etyper -f"
else
    echo "Skipped service install."
    echo "Run manually: sudo python3 typewriter.py"
fi

echo
echo "=== Setup complete ==="
echo "Documents saved to: $DOCS_DIR"
