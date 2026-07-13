#!/bin/bash
# scripts/setup_cloudflare_tunnel.sh
# -----------------------------------
# Sets up Cloudflare Tunnel for secure HTTPS access to IATIS API
# without opening any ports on the VPS firewall.
#
# Prerequisites:
#   - Cloudflare account (free)
#   - Domain added to Cloudflare
#
# Usage:
#   chmod +x scripts/setup_cloudflare_tunnel.sh
#   ./scripts/setup_cloudflare_tunnel.sh
#
# After setup, IATIS API will be accessible at:
#   https://iatis.yourdomain.com/health
#   https://iatis.yourdomain.com/dashboard (requires X-API-Key header)

set -e

echo "=== IATIS Cloudflare Tunnel Setup ==="
echo ""

# Step 1: Install cloudflared
if ! command -v cloudflared &> /dev/null; then
    echo "Installing cloudflared..."
    # Arch-aware: the previous hardcoded amd64 .deb silently produced an
    # unrunnable binary ("exec format error") on arm64 hosts (e.g. Oracle
    # Cloud Ampere / any aarch64 VPS).
    case "$(dpkg --print-architecture)" in
        amd64) CF_ARCH=amd64 ;;
        arm64) CF_ARCH=arm64 ;;
        *) echo "Unsupported architecture: $(dpkg --print-architecture)" >&2; exit 1 ;;
    esac
    curl -L --output /tmp/cloudflared.deb \
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}.deb"
    sudo dpkg -i /tmp/cloudflared.deb
    rm /tmp/cloudflared.deb
    echo "cloudflared installed: $(cloudflared --version)"
else
    echo "cloudflared already installed: $(cloudflared --version)"
fi

echo ""
echo "=== Authentication ==="
echo "This will open a browser window to authenticate with Cloudflare."
echo "If you're on a headless server, copy the URL and open it manually."
echo ""
cloudflared tunnel login

echo ""
echo "=== Creating Tunnel ==="
TUNNEL_NAME="iatis-tunnel"
cloudflared tunnel create $TUNNEL_NAME

# Get tunnel ID
TUNNEL_ID=$(cloudflared tunnel list | grep $TUNNEL_NAME | awk '{print $1}')
echo "Tunnel ID: $TUNNEL_ID"

echo ""
echo "=== Configure DNS ==="
echo "Enter your domain (e.g. iatis.yourdomain.com):"
read -r DOMAIN

cloudflared tunnel route dns $TUNNEL_NAME $DOMAIN

# Create config file
mkdir -p ~/.cloudflared
cat > ~/.cloudflared/config.yml << EOF
tunnel: $TUNNEL_ID
credentials-file: /root/.cloudflared/$TUNNEL_ID.json

ingress:
  - hostname: $DOMAIN
    service: http://localhost:8000
  - service: http_status:404
EOF

echo ""
echo "=== Installing as systemd service ==="
sudo cloudflared service install

cat > /etc/systemd/system/cloudflared.service << EOF
[Unit]
Description=Cloudflare Tunnel for IATIS
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/cloudflared tunnel --config /root/.cloudflared/config.yml run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable cloudflared
sudo systemctl start cloudflared

echo ""
echo "=== Setup Complete ==="
echo "IATIS API now accessible at: https://$DOMAIN"
echo ""
echo "Test with:"
echo "  curl -H 'X-API-Key: YOUR_KEY' https://$DOMAIN/health"
echo "  curl -H 'X-API-Key: YOUR_KEY' https://$DOMAIN/dashboard"
echo ""
echo "Cloudflare tunnel status:"
sudo systemctl status cloudflared --no-pager
