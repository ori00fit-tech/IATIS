#!/usr/bin/env bash
# scripts/deploy_vps.sh — one-command deploy/update for the IATIS VPS.
#
# The remote audit/build environment cannot reach the VPS over SSH
# (HTTPS-only network policy), so everything an operator would do by
# hand over SSH is codified here instead. Run ON the VPS, as root:
#
#   cd /root/IATIS && git pull && bash scripts/deploy_vps.sh
#
# What it does (idempotent — safe to re-run):
#   1. venv + dependencies from requirements.txt
#   2. sanity: compileall + the fast hermetic test slice (skippable)
#   3. installs/refreshes systemd units (scheduler, api, D1 backup timer)
#   4. restarts services and prints their status
#   5. --security-check: read-only review of SSH auth, open ports, .env perms
#
# Flags:
#   --no-test          skip the pytest sanity slice (faster)
#   --security-check   only run the security review, change nothing
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_DIR/venv"
UNITS=(iatis-scheduler.service iatis-api.service iatis-d1-backup.service iatis-d1-backup.timer)

say() { echo -e "\n[deploy] $*"; }

security_check() {
  say "=== SECURITY REVIEW (read-only) ==="
  say "SSH logins since Jul 4 (verify every one is YOU):"
  last -s "2026-07-04" 2>/dev/null | head -20 || true
  say "Failed SSH attempts (last 10):"
  (journalctl -u ssh -u sshd --since "2026-07-04" 2>/dev/null | grep -i "failed\|invalid" | tail -10) || true
  say "PasswordAuthentication should be 'no':"
  sshd -T 2>/dev/null | grep -iE "^passwordauthentication|^permitrootlogin" || true
  say "authorized_keys entries (remove any you don't recognize):"
  wc -l /root/.ssh/authorized_keys 2>/dev/null || true
  say "Port 8000 exposure (API should NOT be world-open; firewall or bind 127.0.0.1):"
  (ss -tlnp | grep -E ":8000|:22") || true
  say ".env permissions (should be 600):"
  stat -c "%a %n" "$REPO_DIR/.env" 2>/dev/null || echo "  no .env found"
  say "Reminder (audit C1): rotate ALL credentials that were pasted into chat."
}

if [[ "${1:-}" == "--security-check" ]]; then
  security_check
  exit 0
fi

say "Deploying from $REPO_DIR (branch: $(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD), commit: $(git -C "$REPO_DIR" rev-parse --short HEAD))"

# 1. Python environment
if [[ ! -d "$VENV" ]]; then
  say "Creating venv"
  python3 -m venv "$VENV"
fi
say "Installing dependencies"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"

# 2. Sanity checks before touching services
say "Byte-compiling sources"
"$VENV/bin/python" -m compileall -q "$REPO_DIR"/{main.py,scheduler.py,core,engines,confluence,risk,storage,execution,fundamentals,regimes,utils}
if [[ "${1:-}" != "--no-test" ]]; then
  say "Running fast test slice (use --no-test to skip)"
  "$VENV/bin/pip" install --quiet pytest
  "$VENV/bin/python" -m pytest "$REPO_DIR/tests/test_storage_resilience.py" \
    "$REPO_DIR/tests/test_logger_config.py" "$REPO_DIR/tests/test_d1_client.py" -q
fi

# 3. systemd units
say "Installing systemd units: ${UNITS[*]}"
for u in "${UNITS[@]}"; do
  [[ -f "$REPO_DIR/$u" ]] && cp "$REPO_DIR/$u" /etc/systemd/system/
done
systemctl daemon-reload

# 4. (Re)start services
say "Restarting services"
systemctl enable --now iatis-scheduler.service iatis-api.service
systemctl enable --now iatis-d1-backup.timer
systemctl restart iatis-scheduler.service iatis-api.service

say "Status:"
systemctl --no-pager --lines=0 status iatis-scheduler.service iatis-api.service | grep -E "●|Active:" || true
systemctl list-timers iatis-d1-backup.timer --no-pager | head -3 || true

# 5. Post-deploy health probe
sleep 3
say "API health probe:"
curl -s -m 5 http://127.0.0.1:8000/health || echo "  (API not answering yet — check: journalctl -u iatis-api -n 50)"
echo

security_check

say "Done. Logs: journalctl -u iatis-scheduler -f"
