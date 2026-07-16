"""
scripts/archive_results.py
---------------------------
Research-artifact archiver (institutional gap analysis S3; production
audit Phase 5 "not reproducible" remediation).

The manifests (research/manifest.py) already bind every run to git
commit + config hash + dataset SHA-256s and are committed to git. What
a third-party reviewer still cannot get are the BYTES those hashes
point at: raw *_result.json files and the datasets are gitignored.
This script closes that gap:

  1. Collects research/results/*.json (manifests AND raw results when
     present locally) plus every dataset referenced by any manifest's
     `datasets` block (resolved by basename under data/ — manifests may
     record absolute VPS paths like /opt/iatis/data/...).
  2. VERIFIES each dataset against the SHA-256 its manifest recorded —
     a mismatch means the dataset drifted since the run and is reported
     loudly (archiving a drifted file under the old claim would forge
     evidence).
  3. Copies everything into a content-addressed store
     (archive/research/<sha256[:2]>/<sha256>__<name>) — identical bytes
     dedupe naturally, nothing is ever overwritten with different
     content.
  4. Writes research/results/ARTIFACTS.md — a committed, human-readable
     index (name, bytes, sha256, status) so the record of WHAT evidence
     exists is in git even while the bytes live off-site.
  5. --upload syncs the store to an rclone remote (same convention as
     scripts/backup_d1.sh: ARCHIVE_RCLONE_REMOTE=r2:iatis-artifacts);
     without credentials it prints the exact command instead.
  6. --verify re-hashes every archived file against its own
     content-address — bit-rot / tamper detection.

Usage (VPS):
    python -m scripts.archive_results                # stage + index
    python -m scripts.archive_results --upload       # + rclone sync
    python -m scripts.archive_results --verify       # audit the store
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "research" / "results"
DATA_DIR = PROJECT_ROOT / "data"
ARCHIVE_DIR = PROJECT_ROOT / "archive" / "research"
INDEX_PATH = RESULTS_DIR / "ARTIFACTS.md"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _store_path(archive_dir: Path, sha: str, name: str) -> Path:
    return archive_dir / sha[:2] / f"{sha}__{name}"


def _archive_file(path: Path, archive_dir: Path,
                  expected_sha: str | None = None) -> dict[str, Any]:
    """Copy one file into the content-addressed store. Returns its index
    entry. `expected_sha` (from a manifest) turns a drifted dataset into
    a loud SHA_MISMATCH entry instead of silent forged evidence."""
    sha = _sha256(path)
    entry: dict[str, Any] = {
        "name": path.name,
        "source": str(path),
        "sha256": sha,
        "size_bytes": path.stat().st_size,
        "status": "archived",
    }
    if expected_sha and sha != expected_sha:
        entry["status"] = "SHA_MISMATCH"
        entry["expected_sha256"] = expected_sha
        return entry  # never archive drifted bytes under an old claim

    dest = _store_path(archive_dir, sha, path.name)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    entry["archived_as"] = str(dest.relative_to(archive_dir))
    return entry


def _dataset_refs(results_dir: Path) -> dict[str, str]:
    """basename → sha256 for every dataset referenced by any manifest."""
    refs: dict[str, str] = {}
    for mf in sorted(results_dir.glob("*_manifest.json")):
        try:
            manifest = json.loads(mf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for ds in manifest.get("datasets") or []:
            name = Path(str(ds.get("file", ""))).name
            sha = ds.get("sha256")
            if name and sha:
                # Last manifest wins on conflict — both hashes end up in
                # the store anyway if both file versions are ever seen.
                refs[name] = sha
    return refs


def archive_all(results_dir: Path = RESULTS_DIR, data_dir: Path = DATA_DIR,
                archive_dir: Path = ARCHIVE_DIR) -> list[dict[str, Any]]:
    """Stage every artifact; returns the index entries."""
    entries: list[dict[str, Any]] = []

    for f in sorted(results_dir.glob("*.json")):
        entries.append(_archive_file(f, archive_dir))

    for name, expected_sha in sorted(_dataset_refs(results_dir).items()):
        local = data_dir / name
        if local.exists():
            entries.append(_archive_file(local, archive_dir, expected_sha=expected_sha))
        else:
            entries.append({
                "name": name, "source": str(local), "sha256": expected_sha,
                "size_bytes": None, "status": "MISSING_LOCALLY",
            })
    return entries


def write_index(entries: list[dict[str, Any]], index_path: Path = INDEX_PATH,
                remote: str | None = None) -> None:
    lines = [
        "# Research artifact index (generated — do not edit)",
        "",
        f"Generated by `python -m scripts.archive_results` at "
        f"{datetime.now(timezone.utc).isoformat()}.",
        "",
        "Bytes live in the content-addressed store `archive/research/` "
        "(gitignored) and its off-site mirror"
        + (f" `{remote}`" if remote else " (set ARCHIVE_RCLONE_REMOTE)")
        + ". This index is committed so the record of what evidence exists "
        "is in git even while the bytes live off-site. An entry's file is "
        "retrievable at `<sha256[:2]>/<sha256>__<name>`.",
        "",
        "| artifact | bytes | sha256 | status |",
        "|---|---|---|---|",
    ]
    for e in sorted(entries, key=lambda x: x["name"]):
        size = "?" if e.get("size_bytes") is None else str(e["size_bytes"])
        lines.append(f"| {e['name']} | {size} | `{e['sha256']}` | {e['status']} |")
    problems = [e for e in entries if e["status"] != "archived"]
    if problems:
        lines += ["", f"**{len(problems)} artifact(s) need attention** "
                      "(SHA_MISMATCH = dataset drifted since the run; "
                      "MISSING_LOCALLY = bytes only exist on the machine "
                      "that ran the experiment)."]
    index_path.write_text("\n".join(lines) + "\n")


def verify_store(archive_dir: Path = ARCHIVE_DIR) -> list[str]:
    """Re-hash every stored file against its content-address."""
    failures = []
    for f in sorted(archive_dir.rglob("*__*")):
        claimed = f.name.split("__", 1)[0]
        if _sha256(f) != claimed:
            failures.append(str(f))
    return failures


def upload(archive_dir: Path = ARCHIVE_DIR) -> int:
    remote = os.environ.get("ARCHIVE_RCLONE_REMOTE", "")
    cmd = ["rclone", "sync", str(archive_dir),
           f"{remote or 'r2:iatis-artifacts'}/research", "--checksum"]
    if not remote:
        print("ARCHIVE_RCLONE_REMOTE not set — run this yourself:")
        print("  " + " ".join(cmd))
        return 0
    print(f"Uploading to {remote} ...")
    return subprocess.run(cmd, check=False).returncode


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    if args.verify:
        failures = verify_store()
        print(f"verify: {len(failures)} corrupted file(s)")
        for f in failures:
            print(f"  CORRUPT: {f}")
        return 1 if failures else 0

    entries = archive_all()
    remote = os.environ.get("ARCHIVE_RCLONE_REMOTE") or None
    write_index(entries, remote=remote)
    archived = sum(1 for e in entries if e["status"] == "archived")
    problems = [e for e in entries if e["status"] != "archived"]
    print(f"archived/refreshed {archived} artifact(s) → {ARCHIVE_DIR}")
    print(f"index written → {INDEX_PATH}")
    for e in problems:
        print(f"  {e['status']}: {e['name']}")
    rc = 0
    if args.upload:
        rc = upload()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
