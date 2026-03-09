#!/usr/bin/env bash
# =============================================================================
#  setup.sh — One-shot bot environment setup for Oracle Cloud (Ubuntu 22.04)
#  Drop this in your repo root and run: bash setup.sh
# =============================================================================

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()     { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Config ────────────────────────────────────────────────────────────────────
BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # repo root = script location
SERVICE_NAME="phemex_sim_bot"
PYTHON_MIN="3.10"
VENV_DIR="$BOT_DIR/.venv"
MAIN_SCRIPT="core/sim_bot.py"
ENV_FILE="$BOT_DIR/.env"

echo -e "\n${CYAN}══════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}   Phemex Sim Bot — Oracle Cloud Setup${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════════${NC}\n"


# ── 2. Python version check ───────────────────────────────────────────────────
PYTHON_BIN=$(command -v python3)
PY_VERSION=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Found Python $PY_VERSION"
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" \
    || die "Python $PYTHON_MIN+ required. Got $PY_VERSION — upgrade first."

# ── 3. Virtual environment ────────────────────────────────────────────────────
if [[ -d "$VENV_DIR" ]]; then
    warn "Existing venv found at $VENV_DIR — skipping creation."
else
    info "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    success "Venv created at $VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# ── 4. Dependencies ───────────────────────────────────────────────────────────
info "Installing Python dependencies..."

if [[ -f "$BOT_DIR/requirements.txt" ]]; then
    pip install --quiet --upgrade pip
    pip install --quiet -r "$BOT_DIR/requirements.txt"
    success "Installed from requirements.txt"
else
    warn "No requirements.txt found — installing known deps directly."
    pip install --quiet --upgrade pip
    pip install --quiet \
        numpy requests colorama python-dotenv \
        websocket-client blessed urllib3
    success "Core dependencies installed."

    info "Generating requirements.txt from current environment..."
    pip freeze > "$BOT_DIR/requirements.txt"
    success "requirements.txt written."
fi

# ── 5. .env file ──────────────────────────────────────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
    success ".env file already exists — skipping."
else
    warn ".env not found. Creating template..."
    cat > "$ENV_FILE" << 'EOF'
# Phemex Bot Environment Variables
# Fill these in before starting the bot.

PHEMEX_BASE_URL=https://api.phemex.com
INITIAL_BALANCE=150.0

# Optional integrations
CRYPTOPANIC_API_KEY=
DEEPSEEK_API_KEY=
ENTITY_API_KEY=
ENTITY_API_BASE_URL=https://acoustic-trade-scan-now.base44.app
ENTITY_APP_ID=69a3845341f04ab2db0682fb

# Telegram alerts (optional)
TG_BOT_TOKEN=
TG_CHAT_ID=

# Scanner defaults (optional overrides)
MIN_VOLUME=1000000
TIMEFRAME=15m
TOP_N=20
MIN_SCORE=25
MAX_WORKERS=100
RATE_LIMIT_RPS=100.0
EOF
    warn ".env template created at $ENV_FILE — fill in your keys before starting!"
fi

# ── 6. Validate main script exists ───────────────────────────────────────────
[[ -f "$BOT_DIR/$MAIN_SCRIPT" ]] || die "$MAIN_SCRIPT not found in $BOT_DIR — is this the right directory?"

# ── 7. systemd service ────────────────────────────────────────────────────────
info "Setting up systemd service: $SERVICE_NAME"

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=Phemex Sim Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$BOT_DIR
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$VENV_DIR/bin/python3 $BOT_DIR/$MAIN_SCRIPT
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME" > /dev/null 2>&1
success "systemd service installed and enabled on boot."

# ── 8. tmux helper script ─────────────────────────────────────────────────────
TMUX_SCRIPT="$BOT_DIR/run_bot.sh"
cat > "$TMUX_SCRIPT" << EOF
#!/usr/bin/env bash
# Quick launcher — runs the bot in a persistent tmux session.
# Usage:  bash run_bot.sh [start|stop|attach|status]

SESSION="$SERVICE_NAME"
BOT_DIR="$BOT_DIR"
VENV="$VENV_DIR"

cmd="\${1:-start}"

case "\$cmd" in
  start)
    if tmux has-session -t "\$SESSION" 2>/dev/null; then
        echo "Session '\$SESSION' already running. Use 'attach' to connect."
    else
        tmux new-session -d -s "\$SESSION" -c "\$BOT_DIR" \\
            "\$VENV/bin/python3 \$BOT_DIR/$MAIN_SCRIPT"
        echo "Bot started in tmux session '\$SESSION'."
        echo "Attach with:  bash run_bot.sh attach"
    fi
    ;;
  stop)
    tmux kill-session -t "\$SESSION" 2>/dev/null && echo "Session stopped." || echo "No session found."
    ;;
  attach)
    tmux attach -t "\$SESSION"
    ;;
  status)
    tmux has-session -t "\$SESSION" 2>/dev/null && echo "Running ✓" || echo "Not running ✗"
    ;;
  *)
    echo "Usage: bash run_bot.sh [start|stop|attach|status]"
    ;;
esac
EOF
chmod +x "$TMUX_SCRIPT"
success "tmux helper written to run_bot.sh"

# ── 9. Summary ────────────────────────────────────────────────────────────────
echo -e "\n${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}   Setup complete!${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e ""
echo -e "  ${CYAN}Start bot (tmux):${NC}     bash run_bot.sh start"
echo -e "  ${CYAN}Attach to session:${NC}    bash run_bot.sh attach"
echo -e "  ${CYAN}Stop bot:${NC}             bash run_bot.sh stop"
echo -e "  ${CYAN}Status:${NC}               bash run_bot.sh status"
echo -e ""
echo -e "  ${CYAN}Start as service:${NC}     sudo systemctl start $SERVICE_NAME"
echo -e "  ${CYAN}Service logs:${NC}         journalctl -u $SERVICE_NAME -f"
echo -e ""
if grep -q "^DEEPSEEK_API_KEY=$" "$ENV_FILE" 2>/dev/null; then
    echo -e "  ${YELLOW}⚠  Don't forget to fill in your .env keys!${NC}"
fi
echo -e ""
