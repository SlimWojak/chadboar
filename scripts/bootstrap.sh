#!/usr/bin/env bash
# ChadBoar — VPS Bootstrap Script (Idempotent)
# Run as root on a fresh Ubuntu 24.04 server (Hostinger, Singapore)
#
# Usage: sudo bash bootstrap.sh
#
# This script:
# 1. Creates non-root user 'autistboar'
# 2. Hardens SSH (key-only, no password)
# 3. Configures firewall (SSH + 443 only)
# 4. Installs Node 22+ and Python 3.12+
# 5. Installs OpenClaw
# 6. Clones the repo
# 7. Sets up Python virtualenv
# 8. Configures systemd service

set -euo pipefail

echo "=== ChadBoar VPS Bootstrap ==="
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── 1. System user ──────────────────────────────────────────────────

if ! id -u autistboar &>/dev/null; then
    echo "[+] Creating user: autistboar"
    useradd -m -s /bin/bash autistboar
    usermod -aG sudo autistboar
else
    echo "[=] User autistboar already exists"
fi

# ── 2. SSH hardening ────────────────────────────────────────────────

echo "[+] Hardening SSH..."
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl reload sshd || systemctl reload ssh || true

# ── 3. Firewall ─────────────────────────────────────────────────────

echo "[+] Configuring firewall..."
apt-get update -qq
apt-get install -y -qq ufw fail2ban unattended-upgrades > /dev/null

ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 443/tcp
echo "y" | ufw enable || true

# fail2ban
systemctl enable fail2ban
systemctl start fail2ban

# Unattended security updates
dpkg-reconfigure -plow unattended-upgrades || true

# ── 4. Node.js 22+ ──────────────────────────────────────────────────

if ! command -v node &>/dev/null || [[ $(node -v | cut -d. -f1 | tr -d v) -lt 22 ]]; then
    echo "[+] Installing Node.js 22..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -y -qq nodejs > /dev/null
else
    echo "[=] Node.js $(node -v) already installed"
fi

# ── 5. Python 3.12+ ─────────────────────────────────────────────────

if ! command -v python3.12 &>/dev/null; then
    echo "[+] Installing Python 3.12..."
    apt-get install -y -qq python3.12 python3.12-venv python3-pip > /dev/null
else
    echo "[=] Python 3.12 already installed"
fi

# ── 6. OpenClaw ──────────────────────────────────────────────────────

if ! command -v openclaw &>/dev/null; then
    echo "[+] Installing OpenClaw..."
    npm install -g openclaw@latest
else
    echo "[=] OpenClaw already installed ($(openclaw --version 2>/dev/null || echo 'unknown'))"
fi

# ── 7. Clone repo ───────────────────────────────────────────────────

REPO_DIR="/home/autistboar/chadboar"
if [ ! -d "$REPO_DIR" ]; then
    echo "[+] Cloning repository..."
    sudo -u autistboar git clone https://github.com/SlimWojak/AutisticBoar.git "$REPO_DIR"
else
    echo "[=] Repository already cloned, pulling latest..."
    sudo -u autistboar bash -c "cd $REPO_DIR && git pull"
fi

# ── 8. Python virtualenv ────────────────────────────────────────────

VENV_DIR="$REPO_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "[+] Creating Python virtualenv..."
    sudo -u autistboar python3.12 -m venv "$VENV_DIR"
fi
sudo -u autistboar bash -c "source $VENV_DIR/bin/activate && pip install -r $REPO_DIR/requirements.txt"

# ── 9. Signer key directory ─────────────────────────────────────────

SIGNER_DIR="/etc/autistboar"
if [ ! -d "$SIGNER_DIR" ]; then
    echo "[+] Creating signer key directory..."
    mkdir -p "$SIGNER_DIR"
    chmod 700 "$SIGNER_DIR"
    chown root:root "$SIGNER_DIR"
    echo "# Place signer key here: echo '<base64_key>' > signer.key && chmod 400 signer.key" > "$SIGNER_DIR/README"
fi

# ── 10. OpenClaw workspace link ─────────────────────────────────────

OPENCLAW_DIR="/home/autistboar/.openclaw"
if [ ! -d "$OPENCLAW_DIR" ]; then
    echo "[+] Running OpenClaw onboard..."
    sudo -u autistboar openclaw onboard --install-daemon || true
fi

# ── 11. Permissions ──────────────────────────────────────────────────

echo "[+] Setting permissions..."
chmod 700 "$OPENCLAW_DIR" 2>/dev/null || true
chmod 600 "$OPENCLAW_DIR/openclaw.json" 2>/dev/null || true
chown -R autistboar:autistboar "$REPO_DIR"

# ── Done ─────────────────────────────────────────────────────────────

echo ""
echo "=== Bootstrap Complete ==="
echo ""
echo "Next steps:"
echo "  1. Copy .env to ~/.openclaw/.env (with real API keys)"
echo "  2. Edit ~/.openclaw/openclaw.json (set workspace, telegram, models)"
echo "  3. Set signer key: echo '<base64_key>' > /etc/autistboar/signer.key && chmod 400 /etc/autistboar/signer.key"
echo "  4. Set SIGNER_KEY_PATH in systemd unit env"
echo "  5. Run: openclaw doctor --fix"
echo "  6. Run: openclaw gateway --verbose  (test)"
echo "  7. Fund burner wallet"
echo "  8. Monitor via Telegram"
echo ""
echo "🐗 ChadBoar is ready to scout."
