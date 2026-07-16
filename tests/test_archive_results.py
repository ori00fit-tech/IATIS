"""tests/test_archive_results.py — research artifact archiver (S3).

Content-addressing, dedup, manifest-hash verification (drifted datasets
must NOT be archived under the old claim), index generation, and store
verification.
"""
from __future__ import annotations

import hashlib
import json

from scripts.archive_results import (
    archive_all,
    verify_store,
    write_index,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _setup(tmp_path, dataset_bytes=b"ohlcv,rows\n1,2\n", drift=False):
    results = tmp_path / "results"
    data = tmp_path / "data"
    archive = tmp_path / "archive"
    results.mkdir()
    data.mkdir()

    (results / "h999_result.json").write_text('{"pf": 1.23}')
    (data / "EURUSD_H4_deep.csv").write_bytes(
        dataset_bytes + (b"DRIFTED" if drift else b""))

    manifest = {
        "kind": "test", "datasets": [{
            "symbol": "EURUSD",
            "file": "/opt/iatis/data/EURUSD_H4_deep.csv",  # VPS-absolute on purpose
            "sha256": _sha(dataset_bytes),
            "size_bytes": len(dataset_bytes),
        }],
    }
    (results / "h999_manifest.json").write_text(json.dumps(manifest))
    return results, data, archive


def test_archives_results_and_referenced_datasets(tmp_path):
    results, data, archive = _setup(tmp_path)
    entries = archive_all(results, data, archive)

    by_name = {e["name"]: e for e in entries}
    assert by_name["h999_result.json"]["status"] == "archived"
    assert by_name["h999_manifest.json"]["status"] == "archived"
    ds = by_name["EURUSD_H4_deep.csv"]
    assert ds["status"] == "archived"
    # Content-addressed layout: <sha[:2]>/<sha>__<name>
    stored = archive / ds["archived_as"]
    assert stored.exists()
    assert stored.name == f"{ds['sha256']}__EURUSD_H4_deep.csv"


def test_rerun_is_idempotent_dedup(tmp_path):
    results, data, archive = _setup(tmp_path)
    archive_all(results, data, archive)
    n_first = len(list(archive.rglob("*__*")))
    archive_all(results, data, archive)
    assert len(list(archive.rglob("*__*"))) == n_first


def test_drifted_dataset_flagged_but_current_bytes_archived(tmp_path):
    """A dataset whose bytes no longer match the manifest's recorded hash
    must be flagged — and today's bytes archived under their OWN content
    address (never under the old claim, which the content-addressed
    layout makes impossible by construction)."""
    results, data, archive = _setup(tmp_path, drift=True)
    entries = archive_all(results, data, archive)
    ds = next(e for e in entries if e["name"] == "EURUSD_H4_deep.csv")
    assert ds["status"] == "SHA_MISMATCH"
    assert "expected_sha256" in ds
    assert ds["sha256"] != ds["expected_sha256"]
    stored = archive / ds["archived_as"]
    assert stored.exists()
    assert stored.name.startswith(ds["sha256"])          # own hash
    assert not stored.name.startswith(ds["expected_sha256"])  # not the claim


def test_missing_dataset_is_reported(tmp_path):
    results, data, archive = _setup(tmp_path)
    (data / "EURUSD_H4_deep.csv").unlink()
    entries = archive_all(results, data, archive)
    ds = next(e for e in entries if e["name"] == "EURUSD_H4_deep.csv")
    assert ds["status"] == "MISSING_LOCALLY"


def test_index_lists_every_artifact_and_problems(tmp_path):
    results, data, archive = _setup(tmp_path, drift=True)
    entries = archive_all(results, data, archive)
    index = results / "ARTIFACTS.md"
    write_index(entries, index, remote="r2:test-bucket")
    text = index.read_text()
    assert "h999_result.json" in text
    assert "SHA_MISMATCH" in text
    assert "r2:test-bucket" in text
    assert "need attention" in text


def test_verify_store_detects_corruption(tmp_path):
    results, data, archive = _setup(tmp_path)
    archive_all(results, data, archive)
    assert verify_store(archive) == []

    victim = next(archive.rglob("*__h999_result.json"))
    victim.write_text('{"pf": 9.99}')  # tamper
    failures = verify_store(archive)
    assert len(failures) == 1 and "h999_result.json" in failures[0]


def test_upload_without_rclone_prints_instructions(tmp_path, monkeypatch, capsys):
    """rclone missing must yield instructions + exit code 1, not a
    traceback (observed live 2026-07-16)."""
    import subprocess
    from scripts import archive_results

    monkeypatch.setenv("ARCHIVE_RCLONE_REMOTE", "r2:iatis-artifacts")

    def no_rclone(*a, **k):
        raise FileNotFoundError(2, "No such file or directory", "rclone")

    monkeypatch.setattr(subprocess, "run", no_rclone)
    rc = archive_results.upload(tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "rclone is not installed" in out
    assert "rclone config" in out
