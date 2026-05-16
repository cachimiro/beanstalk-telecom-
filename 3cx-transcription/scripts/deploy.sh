#!/usr/bin/env bash
# DigitalOcean Droplet deployment script
# Run once on a fresh Ubuntu 24.04 droplet as root.
# Usage: bash scripts/deploy.sh

set -euo pipefail

echo "==> Installing system dependencies"
apt-get update -qq
apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    git \
    ufw

# ── Docker ────────────────────────────────────────────────────────────────────
echo "==> Installing Docker"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl enable docker
systemctl start docker

# ── Firewall ──────────────────────────────────────────────────────────────────
echo "==> Configuring firewall"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp   # SSH
ufw allow 80/tcp   # HTTP
ufw --force enable

# ── App directory ─────────────────────────────────────────────────────────────
echo "==> Setting up app directory"
mkdir -p /opt/3cx-transcription
cd /opt/3cx-transcription

echo ""
echo "==> Deployment prerequisites installed."
echo ""
echo "Next steps:"
echo "  1. Copy your project files to /opt/3cx-transcription/"
echo "     e.g.: rsync -avz ./3cx-transcription/ root@YOUR_IP:/opt/3cx-transcription/"
echo ""
echo "  2. Copy .env.example to .env and fill in all values:"
echo "     cp .env.example .env && nano .env"
echo ""
echo "  3. Build and start the stack:"
echo "     bash scripts/start.sh"
