"""
backtest/hypothesis_live_request.py
---------------------------------------
Hypothesis Discovery Engine, Phase 8C — Live Identity Request (adapter).

Completes the chain, one link past the Live Decision Gate (Phase 7):

    (human-curated) Live Evaluation Roster
        -> hypothesis_id (and NOTHING else — see the operator's own
           locked Roster Contract: no duplicate identity fields, ever)
                |
                v
    resolve_governed_identity()   -- fetch the ALREADY-immutable
                |                    Hypothesis row; this is the ONE
                |                    authoritative source for every
                |                    identity field this module uses.
                |                    Dispatches on the row's own
                |                    decision_type (never inferred from
                |                    `engine`'s string value) to either
                |                    the SINGLE_ENGINE shape (exactly one
                |                    engine, read directly off the
                |                    hypothesis's own `engine` column) or
                |                    the CONFLUENCE shape (the bundle's
                |                    own engine list, `engine`=CONFLUENCE
                |                    is the decision mechanism's own
                |                    identity, never an engine's).
                v
    build_governed_config()        -- construct a run_pipeline()-
                |                     compatible config dict from that
                |                     identity, per the operator's own
                |                     locked Risk Mapping Contract
                v
    run_pipeline(governed_config)  -- main.py, UNCHANGED, reused as-is
                |                     (confirmed by direct trace: it is
                |                     a pure function of its own config
                |                     parameter — load_config() is only
                |                     ever called from main.py's own
                |                     standalone CLI entry point, never
                |                     from inside run_pipeline() itself)
                v
    evaluate_live_decision()       -- Phase 7, UNCHANGED, reused as-is,
                |                      called ONLY when the live
                |                      computation itself produced
                |                      final_verdict == "EXECUTE"
                |                      (mirrors scheduler.py's own
                |                      existing "only gate an EXECUTE"
                |                      pattern) -- Kill Switch -> Policy
                |                      -> Live Decision, fresh, exactly
                |                      as already locked; this module
                |                      never reorders, bypasses, or
                |                      duplicates that gate.
                v
    storage.hypothesis_live_request.record_live_identity_request()
                                    -- persists the full governed risk
                                       provenance snapshot (risk_preset +
                                       preset_definition_hash +
                                       risk_parameters_used_json) as an
                                       IMMUTABLE record, never re-derived
                                       later from RISK_PRESETS or config/
                                       risk.yaml — a future edit to what a
                                       preset name means numerically can
                                       never reinterpret a past decision.

NOT part of this module's scope (deferred, per the operator's own
Roster Contract): how a hypothesis_id gets onto the Roster in the first
place -- storage shape, CRUD, authorization of that curation action.
This module's own entry point simply accepts hypothesis_id as an
already-supplied argument.

NON-NEGOTIABLE invariants (operator's own locked Phase 8C contract):

  1. NO enumeration of the Policy Registry. There is no function
     anywhere in this module shaped like `for grant in list_granted():
     run(grant)` -- resolve_governed_identity() takes a single,
     explicit hypothesis_id and nothing else.
  2. NO auto-selection. evaluate_live_identity_request()'s only
     parameter naming WHICH identity to evaluate is hypothesis_id --
     there is no "whatever is currently granted" code path; that shape
     of call is structurally impossible (no such parameter exists).
  3. NO inference from engine_outputs/contributions/voting/contradiction
     veto. The governed identity (decision_type, decision_version or
     engine_version, bundle_id, symbol, timeframe, risk_preset) is fixed
     BEFORE run_pipeline() is ever called, from the resolved hypothesis
     alone -- never derived from what the confluence computation
     concludes.
  4. NO inference from live numeric risk config. build_governed_risk_
     config() reads ONLY backtest.research_matrix.RISK_PRESETS (the
     SAME fixed, named presets Phases 1-8B already use) -- never
     "these numbers look like balanced."
  5. NO ambient fallback. An unrecognized risk_preset raises
     immediately (HypothesisExecutionError) -- never config/risk.yaml's
     defaults, never the current global risk config, never a symbol's
     own ambient `rr` override (explicitly neutralized -- see build_
     governed_config()'s own docstring for exactly how and why).
  6. NO change to main.py, scheduler.py, or backtest.hypothesis_
     decision_gate.py. All three are reused exactly as they already
     exist, and check_edge_gate()'s existing EdgeNotProvenError is never
     caught or absorbed here (see evaluate_live_identity_request()'s own
     docstring, and tests/test_hypothesis_live_request_edge_gate.py's
     real, non-mocked proof).
  7. Policy Registry remains authorization-only. This module never asks
     it "what should I run" -- only, via the unchanged Phase 7 Gate,
     "is the identity I already decided to compute now authorized."
  8. Every preset-governed parameter has exactly ONE effective source
     during governed computation -- see build_governed_config()'s own
     `twelve_data_symbols` handling for the concrete proof this holds
     even against main.py's own existing per-symbol `rr` override.
  9. PROCEED is not execution. This module's own return value never
     places an order, never constructs a TradeExecutor, and never calls
     anything under execution/*.py.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from backtest.hypothesis_decision_gate import evaluate_live_decision
from backtest.hypothesis_execution import HypothesisExecutionError
from backtest.hypothesis_factory import CONFLUENCE, SINGLE_ENGINE
from backtest.research_matrix import RISK_PRESETS


def resolve_governed_identity(hypothesis_id: str) -> dict[str, Any]:
    """The ONE authoritative source for a Live Identity Request's
    identity: the hypothesis row itself, re-fetched fresh from storage
    (never trusted from a caller-supplied dict — this function's own
    signature accepts only the id string). Raises HypothesisExecutionError
    for an unknown hypothesis_id or an unrecognized decision_type.

    Dispatches on the row's own `decision_type` — never inferred from
    `engine`'s string value:

      SINGLE_ENGINE — `engines_for_computation` is exactly the
      hypothesis's own single `engine` (attribution is direct: this IS
      the engine, not a guess from a bundle or a score). `bundle` stays
      None — a single-engine hypothesis has no bundle to speak of.

      CONFLUENCE — `engines_for_computation` is the bundle's own engine
      list, parsed fresh from the hypothesis's persisted bundle_json.
      `engine`/`engine_version` on a CONFLUENCE row are literally
      CONFLUENCE/decision_version (Phase 8B) — the confluence decision
      MECHANISM's own identity, never an engine's.

    Deliberately does NOT re-derive the hypothesis's own canonical
    fingerprint the way backtest.hypothesis_execution's own _verify_
    hypothesis_identity()/_verify_confluence_hypothesis_identity() do at
    BINDING time (Phase 3/8B) — those checks exist to protect against
    binding a tampered hypothesis into a NEW Mission/cell. By the time a
    hypothesis has a real Promotion and a real Policy Grant (Phase 5/6),
    its identity has already been independently re-verified at each of
    those stages, and no update path exists anywhere in this engine that
    could make it drift afterward. Re-deriving it a third time here would
    be re-litigating an already-structurally-guaranteed invariant, not
    adding a new one."""
    from storage import hypothesis_factory as storage_hypothesis_factory

    h = storage_hypothesis_factory.get_hypothesis(hypothesis_id)
    if h is None:
        raise HypothesisExecutionError(f"resolve_governed_identity: unknown hypothesis_id {hypothesis_id!r}.")
    decision_type = h.get("decision_type") or SINGLE_ENGINE
    if decision_type not in (SINGLE_ENGINE, CONFLUENCE):
        raise HypothesisExecutionError(
            f"resolve_governed_identity: hypothesis {hypothesis_id!r} has an unrecognized decision_type "
            f"{decision_type!r}."
        )

    identity: dict[str, Any] = {
        "hypothesis_id": hypothesis_id,
        "symbol": h["symbol"], "engine": h["engine"], "engine_version": h["engine_version"],
        "timeframe": h["timeframe"], "risk_preset": h["risk_preset"], "decision_type": decision_type,
        "bundle_id": h.get("bundle_id"), "bundle_version": h.get("bundle_version"),
    }
    if decision_type == CONFLUENCE:
        if not h.get("bundle_json"):
            raise HypothesisExecutionError(
                f"resolve_governed_identity: hypothesis {hypothesis_id!r} has decision_type={CONFLUENCE!r} but "
                f"no bundle_json — cannot resolve a governed identity without its own persisted bundle."
            )
        bundle = json.loads(h["bundle_json"])
        identity["bundle"] = bundle
        identity["engines_for_computation"] = list(bundle["engines"])
        identity["timeframes_for_computation"] = list(bundle["timeframes"])
    else:
        identity["bundle"] = None
        identity["engines_for_computation"] = [h["engine"]]
        identity["timeframes_for_computation"] = [h["timeframe"]]
    return identity


def build_governed_risk_config(risk_preset: str) -> dict[str, float]:
    """Phase 8C Contract B, locked mapping — implemented exactly, nothing
    guessed:

        sl_atr_multiplier -> risk.sl_atr_multiplier      (1:1)
        min_rr             -> risk.min_risk_reward         (requires the
                                symbol-level `rr` neutralization in
                                build_governed_config() below)
        risk_per_trade     -> risk.risk_per_trade_max      (1:1 semantic
                                match — confirmed by reading risk/
                                risk_engine.py::evaluate_risk() directly:
                                risk_per_trade_max is the NORMAL per-
                                trade risk value; risk_per_trade_min is a
                                separate, portfolio-level drawdown-
                                reduction floor, switched to only when
                                the account's OWN current_drawdown_pct
                                crosses max_drawdown_reduce — a live
                                account-state concept with nothing to do
                                with which hypothesis governs a given
                                decision. risk_per_trade_min, starting_
                                balance, max_drawdown_reduce/stop,
                                max_exposure, and the entire pretrade_
                                limits block are NEVER touched here —
                                global safety controls, not preset
                                inputs, per Contract B.

    Raises HypothesisExecutionError for an unrecognized risk_preset — no
    ambient fallback of any kind (Contract B's own explicit rule)."""
    preset = RISK_PRESETS.get(risk_preset)
    if preset is None:
        raise HypothesisExecutionError(
            f"build_governed_risk_config: unknown risk_preset {risk_preset!r} — no deterministic live risk "
            f"mapping exists for it. Refusing to fall back to config/risk.yaml's ambient values, a default "
            f"preset, or any other guess (Phase 8C Contract B's own 'no ambient fallback' rule)."
        )
    return {
        "sl_atr_multiplier": preset["sl_atr_multiplier"],
        "min_risk_reward": preset["min_rr"],
        "risk_per_trade_max": preset["risk_per_trade"],
    }


def compute_preset_definition_hash(risk_preset: str) -> str:
    """A canonical, deterministic hash of RISK_PRESETS[risk_preset]'s own
    field/value representation (sorted keys, fixed separators — never
    Python's default dict repr or an unsorted json.dumps, so the hash
    can never differ merely due to formatting/key order). Detects ANY
    future edit to what a preset name means numerically — even though
    backtest.research_matrix.compute_cell_fingerprint() (Phase 1, locked,
    unchanged) only fingerprints the preset NAME, not its definition (a
    real, pre-existing gap on the research side; fixing THAT is out of
    scope here, since it would mean touching the locked Matrix
    fingerprint function — tracked as separate technical debt, not
    conflated with this live-side provenance).

    Raises HypothesisExecutionError for an unrecognized risk_preset —
    same fail-closed rule as build_governed_risk_config()."""
    preset = RISK_PRESETS.get(risk_preset)
    if preset is None:
        raise HypothesisExecutionError(
            f"compute_preset_definition_hash: unknown risk_preset {risk_preset!r}."
        )
    canonical = json.dumps(preset, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_governed_config(identity: dict[str, Any], base_config: dict[str, Any]) -> dict[str, Any]:
    """Constructs a run_pipeline()-compatible config dict from a resolved
    governed identity, layered onto `base_config` (the caller's own
    already-loaded, ordinary IATIS config — this function never calls
    load_config() itself). Every field NOT explicitly overridden below
    is copied from base_config untouched — in particular pretrade_limits,
    starting_balance, max_drawdown_reduce/stop, max_exposure, and risk_
    per_trade_min all stay whatever the ambient config already has
    (Contract B: these are global safety controls, never preset inputs).

    engines.enabled: set True for every engine in `identity[
    "engines_for_computation"]` — for SINGLE_ENGINE this is exactly the
    hypothesis's own one engine (direct attribution, never a bundle
    guess); for CONFLUENCE it's the bundle's own engine list. Nothing
    else is set at all — confirmed by direct trace of main.py's own
    build_active_engines() (`enabled.get(key, False)`) that an OMITTED
    key is already treated identically to an explicit False, so no other
    engine needs to be explicitly disabled here.

    data.timeframes: `identity["timeframes_for_computation"]` — the
    bundle's own timeframe list for CONFLUENCE (already validated
    single-timeframe by generate_confluence_hypotheses() at proposal
    time), or the hypothesis's own single timeframe for SINGLE_ENGINE.
    Confirmed by direct trace that run_pipeline() degrades gracefully
    (an inert D1-confirmation gate, a logged warning) rather than raising
    when "D1" is absent from this list — never a crash.

    The symbol-level `rr` neutralization (Contract B's most important
    finding): main.py::_symbol_config() resolves a per-symbol override
    dict by matching config["data"]["twelve_data_symbols"] entries on
    `internal == symbol`, and main.py's own risk-gate code reads
    `symbol_cfg.get("rr") or config["risk"]["min_risk_reward"]` — meaning
    an EXISTING config/symbols.yaml entry's own `rr` value silently wins
    over risk.min_risk_reward whenever one is present (and one already
    is, for every currently-configured symbol). Setting risk.
    min_risk_reward alone would therefore NOT guarantee the governed
    preset actually controls the live RR — this function forces BOTH
    sides of that `or` to the SAME governed value, so which branch
    executes is structurally irrelevant. Every other field already on
    that symbol's entry (min_score, regime_filter, internal, ...) is
    preserved untouched; if no matching entry existed at all, one
    carrying only `internal`/`rr` is added so the governed RR still
    applies."""
    risk_overrides = build_governed_risk_config(identity["risk_preset"])

    governed = dict(base_config)

    governed["engines"] = dict(base_config.get("engines", {}))
    governed["engines"]["enabled"] = {engine: True for engine in identity["engines_for_computation"]}

    governed["data"] = dict(base_config.get("data", {}))
    governed["data"]["symbol"] = identity["symbol"]
    governed["data"]["timeframes"] = list(identity["timeframes_for_computation"])

    existing_entries = base_config.get("data", {}).get("twelve_data_symbols", [])
    governed_entries: list[dict[str, Any]] = []
    matched = False
    for entry in existing_entries:
        if entry.get("internal") == identity["symbol"]:
            entry = dict(entry)
            entry["rr"] = risk_overrides["min_risk_reward"]
            matched = True
        governed_entries.append(entry)
    if not matched:
        governed_entries.append({"internal": identity["symbol"], "rr": risk_overrides["min_risk_reward"]})
    governed["data"]["twelve_data_symbols"] = governed_entries

    governed["risk"] = dict(base_config.get("risk", {}))
    governed["risk"].update(risk_overrides)

    return governed


def evaluate_live_identity_request(hypothesis_id: str, base_config: dict[str, Any]) -> dict[str, Any]:
    """The SOLE entry point for this module. Accepts EXCLUSIVELY a
    hypothesis_id (the identity to compute) and a base_config (the
    caller's own already-loaded IATIS config, supplying every field this
    request does not itself govern) — there is no third parameter
    through which a caller could name a symbol/engine/timeframe/preset
    directly, and no parameter meaning "whatever is currently granted."

    Note on an existing, UNCHANGED safety layer this call inherits: main.
    build_active_engines() (invoked inside run_pipeline()) already calls
    research.edge_gate.check_edge_gate(), which can raise
    EdgeNotProvenError for an engine this system has no proven-edge
    backing for at all (read-only against research/results/registry.json
    — never written by this module or anything it calls). This function
    does not catch that exception — it is a genuine, structural
    configuration problem, not an ordinary NO_TRADE outcome, and
    deliberately propagates loudly rather than being silently absorbed
    into a result that could be mistaken for an authorization decision.
    Proven by a REAL (non-mocked) run_pipeline() call in tests/
    test_hypothesis_live_request_edge_gate.py — not merely documented.

    Kill Switch -> Policy -> Live Decision (Phase 7, unchanged) is
    consulted ONLY when the live computation itself resolves to
    final_verdict == "EXECUTE" — mirroring scheduler.py's own existing
    "only gate an EXECUTE" pattern exactly. A live computation that
    concludes NO_TRADE on its own has nothing to authorize; this
    function's own overall `decision` is then NO_TRADE with a reason
    naming the live verdict, and gate_result stays None (the Gate was
    never asked, not asked-and-blocked — a distinct, honestly-recorded
    reason, matching Phase 7's own 'missing vs unreadable' distinction).

    Every call persists one immutable governed-risk-provenance record via
    storage.hypothesis_live_request.record_live_identity_request() —
    risk_preset, preset_definition_hash, and risk_parameters_used_json
    (the EXACT numbers actually fed into this specific computation,
    never re-derived later from RISK_PRESETS or config/risk.yaml) — the
    same "every call is an independent, real observation, never
    deduplicated" discipline Phase 7's own audit trail already uses."""
    identity = resolve_governed_identity(hypothesis_id)
    governed_config = build_governed_config(identity, base_config)
    risk_overrides = build_governed_risk_config(identity["risk_preset"])
    preset_definition_hash = compute_preset_definition_hash(identity["risk_preset"])
    risk_parameters_used_json = json.dumps(risk_overrides, sort_keys=True, separators=(",", ":"))

    from main import run_pipeline
    from storage import hypothesis_live_request as storage_hypothesis_live_request

    report = run_pipeline(governed_config)
    live_verdict = report.get("final_verdict")

    if live_verdict != "EXECUTE":
        gate_result = None
        overall_decision = "NO_TRADE"
        overall_reason = f"live computation produced final_verdict={live_verdict!r}, not EXECUTE — nothing to gate."
    else:
        gate_result = evaluate_live_decision(
            identity["symbol"], identity["engine"], identity["engine_version"],
            identity["timeframe"], identity["risk_preset"],
        )
        overall_decision = gate_result["decision"]
        overall_reason = gate_result["decision_reason"]

    record = storage_hypothesis_live_request.record_live_identity_request(
        hypothesis_id=hypothesis_id,
        decision_type=identity["decision_type"],
        symbol=identity["symbol"], engine=identity["engine"], engine_version=identity["engine_version"],
        timeframe=identity["timeframe"], risk_preset=identity["risk_preset"],
        preset_definition_hash=preset_definition_hash, risk_parameters_used_json=risk_parameters_used_json,
        bundle_id=identity.get("bundle_id"),
        live_verdict=live_verdict,
        gate_decision=(gate_result["decision"] if gate_result else None),
        gate_policy_event_id=(gate_result.get("policy_event_id") if gate_result else None),
        decision=overall_decision, decision_reason=overall_reason,
    )
    return dict(record, identity=identity, gate_result=gate_result)
