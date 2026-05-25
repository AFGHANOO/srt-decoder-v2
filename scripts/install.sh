#!/bin/bash
# ============================================================
#  SRT Decoder Dashboard v2 — Raspberry Pi 4 Installer
#  Run as: sudo bash scripts/install.sh
# ============================================================
set -e
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error() { echo -e "${RED}[ERROR]${RESET} $*"; exit 1; }

echo -e "\n${BOLD}╔════════════════════════════════════════╗"
echo -e "║  SRT Decoder v2 — RPi4 Installer      ║"
echo -e "╚════════════════════════════════════════╝${RESET}\n"

[[ $EUID -ne 0 ]] && error "Please run as root: sudo bash scripts/install.sh"

# Detect Pi user (pi or first non-root user)
PI_USER=$(getent passwd 1000 | cut -d: -f1 2>/dev/null || echo "pi")
info "Install user: $PI_USER"

# ── 1. System packages ────────────────────────────────────────
info "Updating package lists..."
apt-get update -qq

info "Installing core dependencies..."
apt-get install -y -qq \
  python3 python3-pip python3-venv \
  ffmpeg \
  libsrt-dev srt-tools \
  git curl wget > /dev/null 2>&1
ok "ffmpeg + SRT tools installed"

# ── 2. Optional: mpv (better HDMI player) ────────────────────
info "Installing mpv (low-latency HDMI player)..."
apt-get install -y -qq mpv > /dev/null 2>&1 && ok "mpv installed" || warn "mpv install failed — ffplay will be used as fallback"

# ── 3. Optional: VLC ─────────────────────────────────────────
info "Installing VLC..."
apt-get install -y -qq vlc > /dev/null 2>&1 && ok "VLC installed" || warn "VLC install skipped"

# ── 4. Python venv ────────────────────────────────────────────
INSTALL_DIR="/opt/srt-decoder"
info "Setting up Python environment in $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

# Copy project files
cp -r "$(dirname "$0")"/.. "$INSTALL_DIR/" 2>/dev/null || {
  warn "Could not auto-copy — assuming files already in $INSTALL_DIR"
}

python3 -m venv "$INSTALL_DIR/venv"
source "$INSTALL_DIR/venv/bin/activate"
pip install -q --upgrade pip
pip install -q flask flask-socketio eventlet
ok "Python venv ready"

# ── 5. Systemd service ────────────────────────────────────────
info "Creating systemd service..."
cat > /etc/systemd/system/srt-decoder.service << EOF
[Unit]
Description=SRT Decoder Dashboard v2
After=network.target graphical.target

[Service]
Type=simple
User=$PI_USER
WorkingDirectory=$INSTALL_DIR
Environment=PYTHONUNBUFFERED=1
Environment=DISPLAY=:0
ExecStart=$INSTALL_DIR/venv/bin/python app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable srt-decoder.service
ok "Systemd service installed (srt-decoder)"

# ── 6. Firewall ───────────────────────────────────────────────
if command -v ufw &>/dev/null; then
  info "Configuring firewall..."
  ufw allow 5000/tcp comment "SRT Decoder Dashboard" > /dev/null 2>&1 || true
  ufw allow 1935/udp comment "SRT Stream"            > /dev/null 2>&1 || true
  ok "Firewall: 5000/tcp and 1935/udp open"
fi

# ── 7. HLS temp dir ───────────────────────────────────────────
mkdir -p /tmp/srt_hls
chown -R "$PI_USER:$PI_USER" /tmp/srt_hls 2>/dev/null || true

# ── 8. DISPLAY env for HDMI from service ─────────────────────
# Allow the service user to access the X display
if command -v xhost &>/dev/null; then
  info "Granting display access..."
  sudo -u "$PI_USER" DISPLAY=:0 xhost +local: 2>/dev/null || true
fi

# Persist xhost in .bashrc of the Pi user
BASHRC="/home/$PI_USER/.bashrc"
if ! grep -q "xhost +local:" "$BASHRC" 2>/dev/null; then
  echo "xhost +local: > /dev/null 2>&1" >> "$BASHRC"
fi

# ── Done ──────────────────────────────────────────────────────
IP=$(hostname -I | awk '{print $1}')
echo ""
echo -e "${BOLD}${GREEN}✓ Installation complete!${RESET}"
echo ""
echo -e "  Start service  : ${CYAN}sudo systemctl start srt-decoder${RESET}"
echo -e "  Dashboard URL  : ${CYAN}http://${IP}:5000${RESET}"
echo -e "  View logs      : ${CYAN}journalctl -u srt-decoder -f${RESET}"
echo ""
echo -e "${YELLOW}HDMI Output notes:${RESET}"
echo "  - Ensure the Pi is connected to a display via HDMI"
echo "  - ffplay is always available (comes with ffmpeg)"
echo "  - mpv gives lowest latency + GPU acceleration on Pi 4"
echo "  - The DISPLAY=:0 env is set in the service automatically"
echo ""
