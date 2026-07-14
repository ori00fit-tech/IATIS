#!/usr/bin/env bash
# scripts/setup_service_user.sh
# ------------------------------
# Non-root migration (production-audit item H4 / tier-1 gap #5):
# move the deploy from /root/IATIS to /opt/iatis, run every IATIS unit as
# a dedicated `iatis` system user, and install path-corrected units for
# all services (api, scheduler, watchdog timer, backup timer, D1 backup timer).
#
# Why a move is required (not just User=iatis): /root is mode 700 — a
# non-root user cannot even traverse it, and the venv's absolute shebangs
# (/root/IATIS/venv/bin/python3) break if merely copied, so the venv is
# REBUILT at the destination.
#
# Safe by design:
#   - idempotent: re-running skips completed steps
#   - /root/IATIS is left untouched as an instant rollback
#     (systemctl stop the new units, cp the old unit files back, start)
#   - services are verified via /health before the script declares success
#
# Usage (on the VPS, as root):
#   cd /root/IATIS && bash scripts/setup_service_user.sh
#
# SRC is overridable so a fresh box never has to clone into /root at all:
#   SRC=/home/ubuntu/IATIS bash scripts/setup_service_user.sh
#
# NOT done automatically (decide separately):
#   - binding the API to 127.0.0.1 instead of 0.0.0.0: correct hardening
#     IF cloudflared/nginx proxies from localhost — verify your tunnel's
#     ingress first (cloudflared: /etc/cloudflared/config.yml service:
#     http://localhost:8000 → safe to switch). Edit the installed
#     /etc/systemd/system/iatis-api.service afterwards.

set -euo pipefail

SRC="${SRC:-/root/IATIS}"
DST=/opt/iatis
SVC_USER=iatis
UNITS=(iatis-api.service iatis-scheduler.service
       iatis-watchdog.service iatis-watchdog.timer
       iatis-backup.service iatis-backup.timer
       iatis-d1-backup.service iatis-d1-backup.timer)

say()  { echo -e "\033[1;36m==> $*\033[0m"; }
fail() { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "run as root"
[[ -d $SRC ]] || fail "$SRC not found"
command -v rsync >/dev/null || apt-get install -y rsync

say "1/7 service user '$SVC_USER'"
id -u $SVC_USER &>/dev/null || useradd -r -s /usr/sbin/nologin -d $DST $SVC_USER

say "2/7 sync $SRC -> $DST (venv rebuilt, not copied)"
mkdir -p $DST
rsync -a --delete \
      --exclude venv/ --exclude __pycache__/ --exclude .pytest_cache/ \
      "$SRC/" "$DST/"

say "3/7 rebuild venv at $DST/venv"
if [[ ! -x $DST/venv/bin/python3 ]]; then
  python3 -m venv $DST/venv
fi
$DST/venv/bin/pip install -q --upgrade pip
$DST/venv/bin/pip install -q -r $DST/requirements.txt
# Separate from requirements.txt (2026-07-14): ctrader-open-api's pinned
# pyOpenSSL conflicts with our cryptography floor if resolved together —
# see requirements-ctrader.txt. Required for the live cTrader feed.
$DST/venv/bin/pip install -q -r $DST/requirements-ctrader.txt
# pytest isn't in requirements.txt (prod deps only) but step 6/7 below
# needs it in THIS freshly-rebuilt venv — it never inherits it from
# whatever venv existed at $SRC.
$DST/venv/bin/pip install -q pytest

say "4/7 ownership + permissions"
mkdir -p $DST/storage $DST/backups
chown -R $SVC_USER:$SVC_USER $DST
chmod 750 $DST
[[ -f $DST/.env ]] && chmod 600 $DST/.env && chown $SVC_USER:$SVC_USER $DST/.env

say "5/7 install path-corrected units (User=$SVC_USER, $DST)"
for unit in "${UNITS[@]}"; do
  [[ -f $DST/$unit ]] || { echo "   - $unit not in repo, skipped"; continue; }
  sed -e "s|/root/IATIS|$DST|g" \
      -e "s|^User=root|User=$SVC_USER|" \
      "$DST/$unit" > "/etc/systemd/system/$unit"
  echo "   + /etc/systemd/system/$unit"
done
systemctl daemon-reload

say "6/7 hermetic test run as $SVC_USER (quick subset)"
# cd into $DST first: pytest records the invocation cwd as session.startpath
# and os.chdir()s back to it during teardown even after a passing run. If
# invoked from wherever this script itself was launched (e.g. a non-root
# clone under /home/<user>, mode 750), $SVC_USER can't re-enter that
# directory and the whole step fails on teardown despite every test
# passing. $DST is chown'd to $SVC_USER above, so it's always enterable.
cd "$DST"
sudo -u $SVC_USER "$DST/venv/bin/python3" -m pytest "$DST/tests/test_phase1.py" -q \
  --rootdir="$DST" -p no:cacheprovider \
  || fail "tests failed under $SVC_USER — fix before switching services"

say "7/7 switch services"
systemctl stop iatis-api iatis-scheduler 2>/dev/null || true
systemctl enable --now iatis-api iatis-scheduler
systemctl enable --now iatis-watchdog.timer iatis-backup.timer iatis-d1-backup.timer
sleep 8
if curl -fsS http://127.0.0.1:8000/health >/dev/null; then
  echo -e "\033[1;32m✓ API healthy under $SVC_USER at $DST\033[0m"
else
  fail "API /health not responding — rollback: restore old units from $SRC, systemctl daemon-reload, restart"
fi

cat <<EOF

DONE. Services now run as '$SVC_USER' from $DST.
  - verify:   systemctl status iatis-scheduler iatis-api
  - timers:   systemctl list-timers 'iatis-*'
  - rollback: cp $SRC/iatis-{api,scheduler}.service /etc/systemd/system/ \\
                && systemctl daemon-reload && systemctl restart iatis-api iatis-scheduler
  - AFTER a few clean days: archive & remove /root/IATIS (it is now stale),
    and update your habits: cd $DST, git pull there.
  - Optional hardening still pending YOUR check of the tunnel ingress:
    bind API to 127.0.0.1 (edit /etc/systemd/system/iatis-api.service ExecStart --host).
EOF
