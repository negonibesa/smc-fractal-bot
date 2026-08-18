#!/bin/bash
# SMC Fractal Bot — Deploy to VPS
# Usage: bash deploy.sh [ip] [user]

set -e

VPS_IP=${1:-""}
VPS_USER=${2:-"root"}
REMOTE_DIR="/opt/smc-fractal-bot"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}  SMC FRACTAL BOT — DEPLOY TO VPS${NC}"
echo -e "${YELLOW}========================================${NC}"

if [ -z "$VPS_IP" ]; then
    read -p "VPS IP: " VPS_IP
    read -p "VPS User [$VPS_USER]: " input
    VPS_USER=${input:-$VPS_USER}
fi

echo -e "${GREEN}[1/6] Testing SSH connection...${NC}"
ssh -o ConnectTimeout=5 $VPS_USER@$VPS_IP "echo OK" || {
    echo -e "${RED}Cannot connect to VPS${NC}"
    exit 1
}

echo -e "${GREEN}[2/6] Installing Python & dependencies on VPS...${NC}"
ssh $VPS_USER@$VPS_IP << 'REMOTE'
    # Update system
    apt update -qq 2>/dev/null || yum update -qq 2>/dev/null
    
    # Install Python 3.11 + pip
    if ! command -v python3 &> /dev/null; then
        apt install -y python3 python3-pip python3-venv 2>/dev/null || \
        yum install -y python3 python3-pip 2>/dev/null
    fi
    
    echo "Python: $(python3 --version)"
REMOTE

echo -e "${GREEN}[3/6] Syncing files to VPS...${NC}"
# Create remote dir
ssh $VPS_USER@$VPS_IP "mkdir -p $REMOTE_DIR"

# Sync only necessary files
rsync -avz --progress \
    --exclude 'venv/' \
    --exclude '__pycache__/' \
    --exclude 'data/raw/' \
    --exclude '*.pyc' \
    --exclude '.git/' \
    --exclude 'logs/' \
    "$LOCAL_DIR/" $VPS_USER@$VPS_IP:$REMOTE_DIR/

# Copy .env
echo -e "${YELLOW}Copying .env...${NC}"
scp "$LOCAL_DIR/.env" $VPS_USER@$VPS_IP:$REMOTE_DIR/.env

echo -e "${GREEN}[4/6] Setting up virtual environment...${NC}"
ssh $VPS_USER@$VPS_IP << REMOTE
    cd $REMOTE_DIR
    python3 -m venv venv 2>/dev/null || true
    source venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
    echo "Dependencies installed"
REMOTE

echo -e "${GREEN}[5/6] Creating systemd service...${NC}"
ssh $VPS_USER@$VPS_IP << REMOTE
    cat > /etc/systemd/system/smc-bot.service << 'EOF'
[Unit]
Description=SMC Fractal Trading Bot
After=network.target

[Service]
Type=simple
User=$VPS_USER
WorkingDirectory=$REMOTE_DIR
Environment=PATH=$REMOTE_DIR/venv/bin:/usr/bin
ExecStart=$REMOTE_DIR/venv/bin/python main.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable smc-bot
    echo "Service created"
REMOTE

echo -e "${GREEN}[6/6] Starting bot...${NC}"
ssh $VPS_USER@$VPS_IP "systemctl restart smc-bot && sleep 2 && systemctl status smc-bot --no-pager"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  DEPLOY COMPLETE!${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e ""
echo -e "Commands:"
echo -e "  ssh $VPS_USER@$VPS_IP"
echo -e "  systemctl status smc-bot"
echo -e "  systemctl restart smc-bot"
echo -e "  journalctl -u smc-bot -f"
echo -e "  cat $REMOTE_DIR/logs/bot.log"
