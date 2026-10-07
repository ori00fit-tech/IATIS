"""tests/test_run_shadow_observe_cycle.py -- tests for scripts/
run_shadow_observe_cycle.py (External Shadow Observe Runner, Design
Gate locked 2026-10, "External Scheduler/Runner"): the
list_active_entries() -> compose_shadow_observe_entries() ->
run_shadow_observe_cycle() wiring, returning 0 regardless of
captured_count ("cycle completed != evidence produced"), and fail-fast
propagation out of main() (the script-level try/except around main()
is the one, deliberate exception to this engine's no-try/except
convention -- see the module's own docstring)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from scripts.run_shadow_observe_cycle import main


@patch("backtest.shadow_observe_orchestrator.run_shadow_observe_cycle")
@patch("backtest.shadow_roster_composer.compose_shadow_observe_entries")
@patch("storage.live_roster.list_active_entries")
@patch("utils.helpers.load_config")
def test_main_wires_roster_through_composer_to_orchestrator(
    mock_load_config, mock_list_active, mock_compose, mock_cycle,
):
    mock_load_config.return_value = {"k": "v"}
    roster_entries = [{"hypothesis_id": "H1"}]
    mock_list_active.return_value = roster_entries
    entries = [{"roster_entry": roster_entries[0], "fresh_promotion_gate_result": {"eligibility": "ELIGIBLE"}}]
    mock_compose.return_value = entries
    mock_cycle.return_value = {"entries_count": 1, "observed_count": 1, "captured_count": 1, "results": []}

    exit_code = main()

    mock_list_active.assert_called_once_with()
    assert mock_compose.call_args.args[0] == roster_entries
    mock_cycle.assert_called_once_with(entries=entries, base_config={"k": "v"})
    assert exit_code == 0


@patch("backtest.shadow_observe_orchestrator.run_shadow_observe_cycle")
@patch("backtest.shadow_roster_composer.compose_shadow_observe_entries")
@patch("storage.live_roster.list_active_entries")
@patch("utils.helpers.load_config")
def test_main_composer_receives_the_live_evidence_resolver(
    mock_load_config, mock_list_active, mock_compose, mock_cycle,
):
    """The resolver passed to the composer must be backtest.
    shadow_live_evidence_resolver.resolve_live_shadow_evidence itself
    -- not a re-implementation."""
    from backtest.shadow_live_evidence_resolver import resolve_live_shadow_evidence

    mock_load_config.return_value = {}
    mock_list_active.return_value = []
    mock_compose.return_value = []
    mock_cycle.return_value = {"entries_count": 0, "observed_count": 0, "captured_count": 0, "results": []}

    main()
    assert mock_compose.call_args.kwargs["evidence_resolver"] is resolve_live_shadow_evidence


@patch("backtest.shadow_observe_orchestrator.run_shadow_observe_cycle")
@patch("backtest.shadow_roster_composer.compose_shadow_observe_entries")
@patch("storage.live_roster.list_active_entries")
@patch("utils.helpers.load_config")
def test_zero_captured_count_on_empty_roster_is_still_a_successful_return(
    mock_load_config, mock_list_active, mock_compose, mock_cycle,
):
    """Locked correction: a successful cycle with captured_count==0 is
    an ordinary outcome (e.g. an empty roster), never treated as or
    reported as a failure."""
    mock_load_config.return_value = {}
    mock_list_active.return_value = []
    mock_compose.return_value = []
    mock_cycle.return_value = {"entries_count": 0, "observed_count": 0, "captured_count": 0, "results": []}

    assert main() == 0


@patch("backtest.shadow_observe_orchestrator.run_shadow_observe_cycle")
@patch("storage.live_roster.list_active_entries")
@patch("utils.helpers.load_config")
def test_list_active_entries_failure_propagates_out_of_main(mock_load_config, mock_list_active, mock_cycle):
    mock_load_config.return_value = {}
    mock_list_active.side_effect = RuntimeError("d1 unreachable")
    with pytest.raises(RuntimeError, match="d1 unreachable"):
        main()
    mock_cycle.assert_not_called()


@patch("backtest.shadow_observe_orchestrator.run_shadow_observe_cycle")
@patch("backtest.shadow_roster_composer.compose_shadow_observe_entries")
@patch("storage.live_roster.list_active_entries")
@patch("utils.helpers.load_config")
def test_orchestrator_failure_propagates_out_of_main(
    mock_load_config, mock_list_active, mock_compose, mock_cycle,
):
    mock_load_config.return_value = {}
    mock_list_active.return_value = []
    mock_compose.return_value = []
    mock_cycle.side_effect = RuntimeError("provider down")
    with pytest.raises(RuntimeError, match="provider down"):
        main()


# ---------- structural: no scope creep --------------------------------------


def _source_without_docstrings() -> str:
    import inspect
    import re
    import scripts.run_shadow_observe_cycle as mod
    source = inspect.getsource(mod)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_main_has_no_try_except_only_the_module_level_guard_does():
    """Locked exception to this engine's no-try/except convention: the
    `if __name__ == "__main__":` guard IS allowed its own try/except
    (for exit-code/logging purposes), but main() itself -- the testable
    unit -- must not swallow anything."""
    import inspect
    main_source = inspect.getsource(main)
    assert "try:" not in main_source
    assert "except" not in main_source


def test_module_never_imports_promotion_gate_directly_or_execution():
    body = _source_without_docstrings()
    forbidden = (
        "import backtest.promotion_gate", "from backtest.promotion_gate",
        "execution.authorization", "execution.trade_executor", "import scheduler", "import main",
    )
    for pattern in forbidden:
        assert pattern not in body, f"run_shadow_observe_cycle unexpectedly references {pattern!r}"
