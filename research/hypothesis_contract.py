"""
research/hypothesis_contract.py
----------------------------------
Slice 4 (Hypothesis Execution Contract, 2026-08-19) — a minimal,
deterministic, PATH-AGNOSTIC provenance contract for the upcoming
H001-H020 controlled rerun. Built to close the gaps a direct audit
found in the current H0XX bespoke-script path (research/experiments/
H0*.py), not to replace it or Mission Center's own pipeline.

Confirmed gaps this closes (audited against a real committed manifest,
research/results/h019_crypto_positioning_ab_20260724_manifest.json, and
H101_smc_structural_bias_ab.py's actual dataset_fingerprint() call site):
  - every H0XX script calls dataset_fingerprint(csv_path) WITHOUT the df
    kwarg, so no manifest today records bar count or date range;
  - no dataset-completeness check exists on that path at all (Slice 1's
    dataset_completeness_pct() lives only in backtest/runner.py and
    backtest/mission_validator.py);
  - no explicit train/OOS date boundaries are recorded (H101 uses an
    index-fraction split, not ISO dates);
  - research/hypotheses/H0XX_*.md prose docs have no content hash tying
    a specific document VERSION to a run;
  - no multiple-testing concept exists on the bespoke-script path at all
    (Slice 3's _compute_mission_family_significance() is Mission-
    Center-specific, keyed to research_missions.existing_trials()).

Deliberately reuses, never duplicates:
  - research.manifest.dataset_fingerprint() IS already generic
    file-fingerprinting (SHA256 + size, despite its dataset-sounding
    name) -- reused verbatim here for the hypothesis-definition .md file
    too. No new hashing code exists in this module.
  - research.manifest.git_state() is reused verbatim for the git block.
  - Persistence stays entirely in research.manifest.write_manifest() --
    this module produces a plain dict a caller embeds into an existing
    build_manifest() payload (e.g. manifest["hypothesis_contract"] = ...);
    it never writes a file itself, so it introduces no second metadata
    storage system alongside the *_manifest.json convention 26 existing
    call sites already use.

Deliberately NOT done here (out of this slice's scope, per the operator's
own instructions): no engine/statistical logic touched, no H0XX script
or mission_validator.py wired to call this module yet (that IS "launching
the rerun", the explicit next, separate step), no registry.json write,
no fabricated hypothesis definitions -- resolve_hypothesis_definition_
path() refuses (raises) rather than inventing a path for an unknown ID.

Fail-closed / fail-open split (Requirements 8-9): validate_for_new_run()
is opt-in -- a caller building provenance for a NEW controlled run calls
it and gets a hard error on any missing required field. Nothing here
ever runs against, mutates, or re-validates an EXISTING historical
manifest -- those stay plain, unrestricted JSON, exactly as today.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.manifest import PROJECT_ROOT, dataset_fingerprint, git_state

HYPOTHESES_DIR = PROJECT_ROOT / "research" / "hypotheses"

# The exact set of contract keys a compute_execution_identity() hash is
# derived from -- deliberately excludes verdict/classification/
# generated_at/git: identity describes WHAT WAS TESTED (the controlled
# setup), not WHAT WAS FOUND, so re-running the identical setup is
# provably the identical identity regardless of when it's re-run.
_IDENTITY_FIELDS: tuple[str, ...] = (
    "hypothesis_id", "symbol", "timeframe", "train_period", "oos_period",
    "engine_config", "risk_config", "sampler", "seed",
)

# Required for a NEW controlled run (Requirement 8) -- validate_for_new_run()
# raises HypothesisContractError if any of these is missing/empty/None.
# Everything else on the contract is optional and MUST stay None rather
# than be fabricated when not applicable (Requirement 9's sibling rule).
_REQUIRED_FIELDS: tuple[str, ...] = (
    "hypothesis_id", "hypothesis_definition", "symbol", "timeframe",
    "dataset_fingerprints", "train_period", "oos_period",
    "engine_config", "risk_config", "trial_family_size", "git",
)


class HypothesisContractError(Exception):
    """Raised by resolve_hypothesis_definition_path() (an unknown/
    ambiguous hypothesis ID -- never invented) and by
    validate_for_new_run() (a required provenance field missing for a
    NEW controlled run -- fail closed, per Requirement 8)."""


def resolve_hypothesis_definition_path(hypothesis_id: str, hypotheses_dir: Path | None = None) -> Path:
    """research/hypotheses/{hypothesis_id}_*.md, resolved deterministically.
    Refuses (raises) rather than inventing a definition for an unknown ID
    (Requirement 7) or guessing among several matches."""
    base = hypotheses_dir or HYPOTHESES_DIR
    matches = sorted(base.glob(f"{hypothesis_id}_*.md"))
    if not matches:
        raise HypothesisContractError(
            f"No hypothesis definition file found for {hypothesis_id!r} in {base} "
            f"(expected {hypothesis_id}_*.md). Refusing to invent one -- "
            f"pre-register the hypothesis first (CLAUDE.md rule 1)."
        )
    if len(matches) > 1:
        raise HypothesisContractError(
            f"Ambiguous hypothesis definition for {hypothesis_id!r}: "
            f"{[m.name for m in matches]} -- resolve manually before building a contract."
        )
    return matches[0]


def build_hypothesis_contract(
    *,
    hypothesis_id: str,
    hypothesis_definition_path: Path | None = None,
    symbol: str,
    timeframe: str,
    dataset_paths: list[Path],
    dataset_frames: dict[str, Any] | None = None,
    train_period: dict[str, str | None],
    oos_period: dict[str, str | None],
    engine_config: dict[str, Any],
    risk_config: dict[str, Any],
    trial_family_size: int = 1,
    dataset_completeness_pct: float | None = None,
    provider: str | None = None,
    direction: str | None = None,
    regime: str | None = None,
    sampler: str | None = None,
    seed: int | None = None,
    validation_verdict: str | None = None,
    multiple_testing_classification: str | None = None,
    final_evidence_verdict: str | None = None,
) -> dict[str, Any]:
    """Assembles the full contract dict for one hypothesis execution.
    Pure with respect to its inputs except for the two provenance reads
    (git_state(), file hashing via dataset_fingerprint()) -- no engine
    run, no statistical computation happens here; every measured value
    (dataset_completeness_pct, verdicts, classification) is supplied by
    the caller, computed by the EXISTING function that already owns that
    computation (never re-derived here).

    dataset_frames (Slice 5 fix, 2026-08-19): optional {str(path):
    DataFrame}. Slice 5's own audit found build_hypothesis_contract()
    never received a DataFrame, so its dataset_fingerprints never carried
    bar count/date range -- reproducing, inside the just-shipped contract
    itself, the exact gap Slice 4's audit found in the H0XX bespoke
    scripts. Backward compatible: omitted (or a path with no matching
    entry) falls through to dataset_fingerprint(p, None), byte-identical
    to Slice 4's original behavior -- every existing caller/test is
    unaffected."""
    definition_path = hypothesis_definition_path or resolve_hypothesis_definition_path(hypothesis_id)
    frames = dataset_frames or {}
    from datetime import datetime, timezone

    return {
        "hypothesis_id": hypothesis_id,
        "hypothesis_definition": dataset_fingerprint(definition_path),
        "symbol": symbol,
        "timeframe": timeframe,
        "dataset_fingerprints": [dataset_fingerprint(p, frames.get(str(p))) for p in dataset_paths],
        "dataset_completeness_pct": dataset_completeness_pct,
        "provider": provider,
        "train_period": dict(train_period),
        "oos_period": dict(oos_period),
        "engine_config": engine_config,
        "risk_config": risk_config,
        "direction": direction,
        "regime": regime,
        "sampler": sampler,
        "seed": seed,
        "trial_family_size": trial_family_size,
        "validation_verdict": validation_verdict,
        "multiple_testing_classification": multiple_testing_classification,
        "final_evidence_verdict": final_evidence_verdict,
        "git": git_state(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def compute_execution_identity(contract: dict[str, Any]) -> str:
    """SHA256 over the identity-relevant subset of the contract (see
    _IDENTITY_FIELDS). Deterministic and pure: dict key order never
    matters (json.dumps(sort_keys=True)); dataset_fingerprints' list
    order never matters (sorted by sha256 before hashing). Two contracts
    built from the identical hypothesis+dataset+configuration always
    produce the identical identity; changing the dataset fingerprint OR
    the hypothesis definition's own sha256 always changes it."""
    identity_payload = {k: contract[k] for k in _IDENTITY_FIELDS}
    identity_payload["hypothesis_definition_sha256"] = contract["hypothesis_definition"]["sha256"]
    identity_payload["dataset_sha256s"] = sorted(d["sha256"] for d in contract["dataset_fingerprints"])
    canonical = json.dumps(identity_payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_for_new_run(contract: dict[str, Any]) -> None:
    """Fail-closed gate for a NEW controlled run only (Requirement 8) --
    never applied to an existing historical record (Requirement 9: those
    stay plain, unrestricted JSON, read via json.loads exactly as
    today -- this function is never called on read). Raises
    HypothesisContractError naming every missing field at once (not just
    the first), so a caller sees the full gap in one pass."""
    missing = [f for f in _REQUIRED_FIELDS if not contract.get(f)]
    if missing:
        raise HypothesisContractError(
            f"Hypothesis execution contract for {contract.get('hypothesis_id')!r} is missing "
            f"required provenance field(s): {missing} -- refusing to produce controlled evidence "
            f"from an incomplete contract."
        )
    if contract["trial_family_size"] < 1:
        raise HypothesisContractError(
            f"trial_family_size must be >= 1 (got {contract['trial_family_size']!r})."
        )
    if not contract["dataset_fingerprints"]:
        raise HypothesisContractError("dataset_fingerprints must be non-empty for a new controlled run.")
