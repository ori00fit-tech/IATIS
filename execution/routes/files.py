"""
execution/routes/files.py
----------------------------
File Explorer (Mission Control module 11) — read-only. View, search,
download, diff. Never edit. Every path is confined to the repo root and
checked against a secret-shaped denylist before it's ever opened.
Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException, Query
from fastapi.responses import FileResponse

from execution.api_core import _REPO_ROOT, _check_auth

router = APIRouter()


# ---------------------------------------------------------------------------
# File Explorer (Mission Control module 11) — read-only. View, search,
# download, diff. Never edit. Every path is confined to the repo root and
# checked against a secret-shaped denylist before it's ever opened — this
# repo's own CLAUDE.md notes real credentials have leaked into chat/commits
# twice before, so path confinement here is defense-in-depth, not decoration.
# ---------------------------------------------------------------------------

# Directories excluded wholesale — .git can contain secrets from repo
# history even if the current tree is clean; the rest are generated/noise.
_DENY_DIR_NAMES = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", ".pytest_cache"}

# Exact "word" matches on a path segment's alnum-split tokens — deliberately
# whole-word (not substring) so e.g. dashboard/frontend/src/theme/tokens.css
# (a design-tokens stylesheet, not a secret) is never falsely denied.
# Checked against EVERY path segment, not just the basename (audit
# docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-4): a future
# config/secrets/db.json or credentials/aws.json would otherwise pass
# untouched, since only a fixed directory-name list (_DENY_DIR_NAMES) was
# checked for intermediate segments and the word filter applied to the
# basename alone.
_DENY_WORDS = {"credential", "credentials", "secret", "secrets", "token", "password", "passwords"}
_DENY_EXTENSIONS = {"pem", "key", "pfx", "p12", "crt", "cer"}
# Extensionless private-key filenames (ssh-keygen's default names) — an
# extension-based check alone misses these entirely.
_DENY_FILENAME_PREFIXES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
_DENY_PREFIXES = ("storage/sessions", "storage/td_cache")
_MAX_READ_BYTES = 512_000
_MAX_SEARCH_FILES = 4000
_MAX_SEARCH_FILE_BYTES = 512_000


def _is_denied_path(posix_rel: str) -> bool:
    parts = posix_rel.split("/")
    if any(p in _DENY_DIR_NAMES for p in parts):
        return True
    if any(posix_rel.startswith(pre) for pre in _DENY_PREFIXES):
        return True
    basename = parts[-1]
    if basename == ".env" or basename.startswith(".env."):
        return True
    if basename.startswith(_DENY_FILENAME_PREFIXES):
        return True
    stem_ext = basename.rsplit(".", 1)
    if len(stem_ext) == 2 and stem_ext[1].lower() in _DENY_EXTENSIONS:
        return True
    words = {
        w.lower()
        for part in parts
        for w in re.split(r"[^A-Za-z0-9]+", part)
        if w
    }
    if words & _DENY_WORDS:
        return True
    return False


def _resolve_safe_path(rel_path: str) -> tuple[Path, str]:
    """Resolve a client-supplied path against the repo root.

    Always returns a path inside _REPO_ROOT and outside the denylist, or
    raises HTTPException — callers never need to re-check.
    """
    rel_path = (rel_path or "").strip().lstrip("/")
    candidate = (_REPO_ROOT / rel_path).resolve() if rel_path else _REPO_ROOT
    try:
        posix_rel = candidate.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        raise HTTPException(status_code=400, detail="Path escapes the repository root.")
    if posix_rel == ".":
        posix_rel = ""
    if posix_rel and _is_denied_path(posix_rel):
        raise HTTPException(status_code=403, detail="This path is not accessible via the File Explorer.")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Path not found.")
    return candidate, posix_rel


@router.get("/files/tree")
async def files_tree(
    path: str = Query(default=""),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    target, posix_rel = _resolve_safe_path(path)
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory — use /files/read.")

    entries = []
    for child in target.iterdir():
        child_rel = (posix_rel + "/" + child.name) if posix_rel else child.name
        if _is_denied_path(child_rel):
            continue
        try:
            stat = child.stat()
            entries.append({
                "name": child.name,
                "path": child_rel,
                "type": "dir" if child.is_dir() else "file",
                "size": None if child.is_dir() else stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        except OSError:
            continue

    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return {"path": posix_rel, "entries": entries}


@router.get("/files/read")
async def files_read(
    path: str = Query(...),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    target, posix_rel = _resolve_safe_path(path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Not a file — use /files/tree.")

    size = target.stat().st_size
    if size > _MAX_READ_BYTES:
        return {
            "path": posix_rel, "size": size, "binary": False, "truncated": False,
            "content": None,
            "error": f"File is {size:,} bytes, over the {_MAX_READ_BYTES:,}-byte inline read limit — use /files/download.",
        }

    raw = target.read_bytes()
    try:
        content = raw.decode("utf-8")
        return {"path": posix_rel, "size": size, "binary": False, "truncated": False, "content": content, "error": None}
    except UnicodeDecodeError:
        return {
            "path": posix_rel, "size": size, "binary": True, "truncated": False,
            "content": None, "error": "Binary file — use /files/download to retrieve it.",
        }


@router.get("/files/download")
async def files_download(
    path: str = Query(...),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> FileResponse:
    _check_auth(x_api_key, iatis_session)
    target, posix_rel = _resolve_safe_path(path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Not a file.")
    return FileResponse(target, filename=target.name, media_type="application/octet-stream")


@router.get("/files/diff")
async def files_diff(
    path: str = Query(...),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Working-tree vs HEAD diff for one file, via `git diff` with a fixed
    argv (no shell=True, path is confined/denylisted by _resolve_safe_path
    before it ever reaches subprocess).
    """
    _check_auth(x_api_key, iatis_session)
    target, posix_rel = _resolve_safe_path(path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Not a file.")

    import subprocess
    try:
        result = subprocess.run(
            ["git", "diff", "--no-color", "HEAD", "--", posix_rel],
            capture_output=True, text=True, timeout=5, cwd=_REPO_ROOT,
        )
        diff_text = result.stdout
        error = None if result.returncode == 0 else (result.stderr or "git diff failed").strip()[:300]
    except FileNotFoundError:
        diff_text, error = "", "git is not available on this host."
    except subprocess.TimeoutExpired:
        diff_text, error = "", "git diff timed out."

    return {"path": posix_rel, "diff": diff_text, "has_changes": bool(diff_text.strip()), "error": error}


@router.get("/files/search")
async def files_search(
    query: str = Query(..., min_length=2, max_length=200),
    path: str = Query(default=""),
    max_results: int = Query(default=100, ge=1, le=500),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Bounded read-only search over filenames and file contents.

    Scans at most _MAX_SEARCH_FILES files under `path`, skips anything
    denylisted or over _MAX_SEARCH_FILE_BYTES, and stops as soon as
    max_results matches are found.
    """
    _check_auth(x_api_key, iatis_session)
    root, root_rel = _resolve_safe_path(path)
    if not root.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory.")

    needle = query.lower()
    results: list[dict[str, Any]] = []
    scanned = 0
    truncated = False

    for current_dir, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _DENY_DIR_NAMES]
        for fname in sorted(filenames):
            if len(results) >= max_results:
                truncated = True
                break
            scanned += 1
            if scanned > _MAX_SEARCH_FILES:
                truncated = True
                break

            fpath = Path(current_dir) / fname
            frel = fpath.relative_to(_REPO_ROOT).as_posix()
            if _is_denied_path(frel):
                continue

            if needle in fname.lower():
                results.append({"path": frel, "match_type": "filename", "line": None, "snippet": fname})
                continue

            try:
                if fpath.stat().st_size > _MAX_SEARCH_FILE_BYTES:
                    continue
                text = fpath.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            for lineno, line in enumerate(text.splitlines(), start=1):
                if needle in line.lower():
                    results.append({
                        "path": frel, "match_type": "content", "line": lineno,
                        "snippet": line.strip()[:200],
                    })
                    break

        if len(results) >= max_results or scanned > _MAX_SEARCH_FILES:
            truncated = True
            break

    return {"query": query, "path": root_rel, "results": results, "truncated": truncated}
