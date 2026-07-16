# Ops-closure runbook (gap analysis M4)

Three operator actions remain from the July audits that no code change can
substitute for. Each has a checklist and a **closure record** to fill in
when done — an unfilled record means the item is OPEN, and this file is the
single place a reviewer checks. Do not pre-fill; a fabricated closure is
worse than an open item.

---

## 1. Credential rotation confirmation (production audit C1 — CRITICAL)

The full production secret set was pasted into an external chat on
2026-07-05 (audit Phase 8). Rotation was mandated in this order; confirm
each, or rotate now if any was missed:

- [ ] `CLOUDFLARE_API_TOKEN` (highest blast radius)
- [ ] `D1_PROXY_TOKEN` (`wrangler secret put D1_PROXY_TOKEN` + VPS `.env`)
- [ ] `API_SERVER_KEY`
- [ ] `TELEGRAM_BOT_TOKEN` (@BotFather /revoke)
- [ ] cTrader `ACCESS/REFRESH_TOKEN` + client secret
- [ ] Data/AI keys: Twelve Data, Alpha Vantage, Finnhub, JBlanked, Perplexity,
      FCS API, MarketAux, TAAPI (any added since the audit)
- [ ] VPS SSH surface: `PasswordAuthentication no`, review `authorized_keys`,
      check `last` / auth logs since 2026-07-05

**Closure record** (fill in):
```
rotated_on:      YYYY-MM-DD
rotated_by:      
keys_rotated:    
ssh_reviewed:    yes/no — findings:
```

---

## 2. Non-root service migration (audit H4)

Everything is scripted — `scripts/setup_service_user.sh` covers all 8 units
(api, scheduler, watchdog+timer, backup+timer, d1-backup+timer), moves the
tree to `/opt/iatis` under user `iatis`, health-checks `/health` after
restart, and prints the rollback line.

- [ ] Read the script's header notes (backup of old unit files, rollback)
- [ ] Run it: `sudo bash scripts/setup_service_user.sh`
- [ ] Verify: `systemctl status iatis-scheduler iatis-api` shows `User=iatis`
- [ ] Verify timers: `systemctl list-timers 'iatis-*'` (3 timers active)
- [ ] Update habits: deploys now `cd /opt/iatis` (CLAUDE.md runbook note)

**Closure record** (fill in):
```
migrated_on:     YYYY-MM-DD
verified_by:     
rollback_tested: yes/no
```

---

## 3. Backup restore rehearsal (audit H5 — a backup is a hope until restored)

The nightly export exists (`iatis-d1-backup.timer` → `scripts/backup_d1.sh`).
What has never been evidenced is a RESTORE:

- [ ] Pick yesterday's dump from `backups/d1/`
- [ ] `wrangler d1 create iatis-restore-test`
- [ ] `wrangler d1 execute iatis-restore-test --remote --file=backups/d1/iatis-YYYY-MM-DD.sql`
- [ ] Row-count spot check vs production (decisions, outcomes, engine_votes)
- [ ] `wrangler d1 delete iatis-restore-test`
- [ ] Record wall-clock time-to-restore (that number IS the RTO)

**Closure record** (fill in):
```
rehearsed_on:    YYYY-MM-DD
dump_file:       
rows_checked:    decisions=?, outcomes=?, engine_votes=?
time_to_restore: ~X minutes
```

---

## 4. First-deploy notes for the gap-analysis branch (one-time)

On the first deploy that includes this branch:

- [ ] Schema migrations auto-apply at scheduler boot — confirm the log line
      `migration 2 'decision_provenance' applied` (or run
      `python -m storage.migrations --status`)
- [ ] Confirm new decisions carry fingerprints:
      `python -m scripts.philosophy_audit` → Axis 9 should flip from INFO
      ("no decision carries provenance yet") to PASS within one run
- [ ] Optional: enable `features.data_confidence_check: true` once API
      budget headroom is confirmed (costs ~1-2 provider calls/run)
- [ ] Archive artifacts from the machine that holds the datasets (the VPS):
      `python -m scripts.archive_results --upload` with
      `ARCHIVE_RCLONE_REMOTE=r2:iatis-artifacts` (create the bucket once) —
      the committed `research/results/ARTIFACTS.md` currently lists every
      dataset as MISSING_LOCALLY because the bytes exist only on the VPS
- [ ] TCA starts populating on the next real (non-dry-run) fill — check
      `GET /execution-quality` after the first demo trade
```
first_deploy_on: YYYY-MM-DD
axis9_status:    
```
