"""
ai/ai_analyzer.py
------------------
AI Orchestrator — the only file the rest of IATIS talks to for AI-backed
explanations. It selects a provider (Gemini by default, OpenAI or
Anthropic as drop-in alternatives — see ai/providers/), applies caching,
and always returns a well-formed result even when the AI call fails or
is disabled.

Scope, deliberately narrow:
    Trading Engines -> Confluence Score -> AIAnalyzer -> Provider API
        -> Natural Language Explanation -> Dashboard / Telegram / reports

AIAnalyzer NEVER produces a BUY/SELL/EXECUTE decision and is never
called from main.py's decision path. The confluence engine (confluence/)
and risk engine (risk/risk_engine.py) remain the sole authority for
final_verdict — this module only explains a decision that has already
been made, for a human reading the dashboard.

Config (config/ai.yaml, merged into `ai:` by utils/helpers.py::load_config
— no secrets):
    ai:
      enabled: false          # opt-in; false = every call below returns status="disabled"
      providers:               # first enabled name in fallback_order wins
        gemini: {enabled: true}
        openai: {enabled: false}
        anthropic: {enabled: false}
      fallback_order: [gemini, openai, anthropic]
      model: gemini-flash-latest
      temperature: 0.1
      max_tokens: 3072
      timeout: 20
      cache:
        news_ttl_min: 20
        macro_ttl_min: 60

API keys are read from the environment (GEMINI_API_KEY /
OPENAI_API_KEY / ANTHROPIC_API_KEY), matching every other integration in
this codebase (see .env.example) — never stored in config.yaml.
"""
from __future__ import annotations

import json
import os
from typing import Any

from ai.cache import TTLCache
from ai.models import HypothesisSuggestion, MacroAnalysis, MatrixResearchPlan, NewsAnalysis, TradeExplanation
from ai.providers.base import AIProvider, AIProviderError
from utils.logger import get_logger

logger = get_logger(__name__)

_PROVIDER_ENV_KEYS = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

_DEFAULT_MODELS = {
    "gemini": "gemini-flash-latest",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-6",
}


def _user_safe_error(exc: AIProviderError) -> str:
    """A plain-language failure message for the dashboard.

    Provider exceptions carry connection internals (hostname, port,
    timeout value, the requests library's exception class name) that are
    exactly what a server log needs and exactly what an end user doesn't
    — every caller already logs the full `str(exc)` via logger.warning
    before calling this, so nothing is lost, it just doesn't reach the
    rendered page too.
    """
    text = str(exc).lower()
    if "timed out" in text or "timeout" in text:
        return "The AI provider took too long to respond. Try again in a moment."
    if "name or service not known" in text or "failed to resolve" in text or "connection" in text:
        return "Could not reach the AI provider — check network connectivity."
    if "401" in text or "403" in text or "unauthorized" in text or "permission" in text:
        return "The AI provider rejected the request — check the configured API key."
    if "429" in text or "rate limit" in text or "quota" in text:
        return "The AI provider is rate-limiting requests right now. Try again shortly."
    if "response shape" in text or "no text content" in text or "non-json" in text:
        return "The AI provider returned an unexpected response. Try again."
    return "The AI provider request failed. See server logs for details."


def _resolve_provider_name(ai_cfg: dict) -> str:
    """Pick the active provider name from config.

    Prefers the `providers: {name: {enabled: bool}}` + `fallback_order`
    structure (config/ai.yaml, 2026-07-12) — the first name in
    fallback_order whose providers[name].enabled is true. Falls back to
    the legacy single `provider: <name>` string when the new keys are
    absent, so anything constructing an ai_cfg dict directly (tests,
    older callers) keeps working unchanged.
    """
    providers = ai_cfg.get("providers")
    fallback_order = ai_cfg.get("fallback_order")
    if providers and fallback_order:
        for name in fallback_order:
            if (providers.get(name) or {}).get("enabled"):
                return name
    return ai_cfg.get("provider", "gemini")


def _build_provider(provider_name: str, ai_cfg: dict) -> AIProvider | None:
    """Instantiate the configured provider, or None if disabled/misconfigured.

    Never raises — a bad AI config must not take down anything that
    happens to import this module.
    """
    env_key = _PROVIDER_ENV_KEYS.get(provider_name)
    if env_key is None:
        logger.warning(f"AIAnalyzer: unknown provider '{provider_name}'")
        return None

    api_key = os.environ.get(env_key, "")
    if not api_key:
        logger.info(f"AIAnalyzer: {env_key} not set — AI explanations disabled")
        return None

    model = ai_cfg.get("model") or _DEFAULT_MODELS.get(provider_name, "")
    temperature = float(ai_cfg.get("temperature", 0.1))
    max_tokens = int(ai_cfg.get("max_tokens", 3072))
    timeout = float(ai_cfg.get("timeout", 20))

    if provider_name == "gemini":
        from ai.providers.gemini import GeminiProvider
        return GeminiProvider(api_key, model, temperature, max_tokens, timeout)
    if provider_name == "openai":
        from ai.providers.openai import OpenAIProvider
        return OpenAIProvider(api_key, model, temperature, max_tokens, timeout)
    if provider_name == "anthropic":
        from ai.providers.anthropic import AnthropicProvider
        return AnthropicProvider(api_key, model, temperature, max_tokens, timeout)

    logger.warning(f"AIAnalyzer: no implementation wired for provider '{provider_name}'")
    return None


class AIAnalyzer:
    """Thin orchestrator: config -> provider -> cache -> typed result."""

    def __init__(self, config: dict) -> None:
        self.config = config
        ai_cfg = config.get("ai", {}) or {}
        self.enabled = bool(ai_cfg.get("enabled", False))
        self.provider_name = _resolve_provider_name(ai_cfg)
        self._cache = TTLCache()
        self._cache_cfg = ai_cfg.get("cache", {}) or {}

        self._provider: AIProvider | None = None
        if self.enabled:
            self._provider = _build_provider(self.provider_name, ai_cfg)
            if self._provider is None:
                # Config asked for AI but the key/provider is missing —
                # log once at startup rather than on every call.
                logger.warning(
                    "AIAnalyzer: ai.enabled=true but no usable provider "
                    f"('{self.provider_name}') — all calls will return status=disabled."
                )

    @property
    def available(self) -> bool:
        return self.enabled and self._provider is not None

    @property
    def resolved_model(self) -> str | None:
        """Phase 3B-H hardening — the ACTUAL model string the active
        provider was constructed with (AIProvider.__init__'s own `model`
        arg, already resolved through _build_provider's config-value-or-
        _DEFAULT_MODELS fallback), as opposed to re-deriving it from a raw
        config dict a caller might read independently (which can diverge
        from what was actually resolved when the config didn't set an
        explicit model). None when no provider is available (disabled/
        misconfigured) — callers must never fabricate a value here."""
        return self._provider.model if self._provider is not None else None

    # ── Trade explanation ──────────────────────────────────────────────

    def explain_trade(self, report: dict, cache_key: str | None = None) -> dict:
        """Explain an already-decided IATIS report dict (main.py's return
        value, or the equivalent row from storage/decision_db.py).

        `cache_key` (e.g. a decision_id) is optional — if given, repeated
        dashboard loads of the same signal don't re-call the API.
        """
        if not self.available:
            return TradeExplanation(status="disabled", provider=self.provider_name).to_dict()

        def _compute() -> dict:
            context = self._trade_context(report)
            try:
                raw = self._provider.explain_trade(context)
                return TradeExplanation(
                    summary=raw.get("summary", ""),
                    pros=raw.get("pros", []) or [],
                    cons=raw.get("cons", []) or [],
                    risk_level=raw.get("risk_level", "UNKNOWN"),
                    confidence=float(raw.get("confidence", 0) or 0),
                    recommendation=raw.get("recommendation", ""),
                    market_sentiment=raw.get("market_sentiment", "Neutral"),
                    news_risk=raw.get("news_risk", "Unknown"),
                    explanation=raw.get("explanation", ""),
                    warnings=raw.get("warnings", []) or [],
                    provider=self._provider.name,
                    status="ok",
                ).to_dict()
            except AIProviderError as exc:
                logger.warning(f"AIAnalyzer.explain_trade failed: {exc}")
                return TradeExplanation(
                    status="error", error=_user_safe_error(exc), provider=self.provider_name
                ).to_dict()

        if cache_key:
            # Long TTL: a fixed set of inputs for a past signal never changes.
            return self._cache.get_or_compute(f"explain:{cache_key}", 24 * 3600, _compute)
        return _compute()

    @staticmethod
    def _trade_context(report: dict) -> dict[str, str]:
        """Map an IATIS decision report onto the explain_trade prompt's
        placeholders. Every value is defensively stringified — the
        prompt template is the only thing that needs these to be strings.
        """
        confluence = report.get("confluence", {}) or {}
        vote = confluence.get("vote", {}) or {}
        regime = report.get("regime", {}) or {}
        risk = report.get("risk", {}) or {}
        news = report.get("news", {}) or {}
        meta = report.get("meta_decision", {}) or {}

        engine_lines = "; ".join(
            f"{e.get('engine')}={e.get('bias')}({e.get('score')})"
            for e in report.get("engine_outputs", []) or []
        )

        return {
            "symbol": str(report.get("symbol", "")),
            "direction": str(vote.get("winning_bias", "")),
            "trend": str(regime.get("state", "")),
            "momentum": str(regime.get("trend_strength", "")),
            "structure": engine_lines or "n/a",
            "smc": next(
                (e.get("reasons") for e in report.get("engine_outputs", []) or []
                 if e.get("engine") == "SMC"),
                "n/a",
            ),
            "risk": (
                f"RR-passed={risk.get('passed')}, "
                f"recommended_risk_pct={risk.get('recommended_risk_pct')}"
            ),
            "news": (
                f"risk_level={news.get('risk_level')}, "
                f"blackout_active={news.get('blackout_active')}"
            ),
            "confluence_score": str(confluence.get("score", "")),
            "confidence": str(meta.get("confidence", confluence.get("score", ""))),
        }

    # ── News analysis ──────────────────────────────────────────────────

    def analyze_news(self, news_items: list[dict], symbols: list[str] | None = None) -> dict:
        if not self.available:
            return NewsAnalysis(status="disabled", provider=self.provider_name).to_dict()

        symbols = symbols or []
        ttl = float(self._cache_cfg.get("news_ttl_min", 20)) * 60

        def _compute() -> dict:
            items_text = "\n".join(
                f"- {n.get('date', '?')} [{n.get('impact', '?')}] "
                f"{n.get('currency', '')} {n.get('name', '')}"
                for n in news_items
            ) or "No scheduled events."
            try:
                raw = self._provider.analyze_news(items_text, ", ".join(symbols) or "n/a")
                return NewsAnalysis(
                    sentiment=raw.get("sentiment", "NEUTRAL"),
                    impact=raw.get("impact", "LOW"),
                    affected_symbols=raw.get("affected_symbols", []) or [],
                    duration=raw.get("duration", ""),
                    confidence=float(raw.get("confidence", 0) or 0),
                    summary=raw.get("summary", ""),
                    provider=self._provider.name,
                    status="ok",
                ).to_dict()
            except AIProviderError as exc:
                logger.warning(f"AIAnalyzer.analyze_news failed: {exc}")
                return NewsAnalysis(
                    status="error", error=_user_safe_error(exc), provider=self.provider_name
                ).to_dict()

        cache_key = f"news:{','.join(sorted(symbols))}:{len(news_items)}"
        return self._cache.get_or_compute(cache_key, ttl, _compute)

    # ── Macro analysis ─────────────────────────────────────────────────

    def analyze_macro(self, context: dict | None = None) -> dict:
        if not self.available:
            return MacroAnalysis(status="disabled", provider=self.provider_name).to_dict()

        context = context or {}
        ttl = float(self._cache_cfg.get("macro_ttl_min", 60)) * 60

        def _compute() -> dict:
            prompt_ctx = {
                "dxy": str(context.get("dxy", "n/a")),
                "risk_inputs": str(context.get("risk_inputs", "n/a")),
                "rates_context": str(context.get("rates_context", "n/a")),
                "regime": str(context.get("regime", "n/a")),
            }
            try:
                raw = self._provider.macro_analysis(prompt_ctx)
                return MacroAnalysis(
                    summary=raw.get("summary", ""),
                    risk_on_off=raw.get("risk_on_off", "NEUTRAL"),
                    dxy_bias=raw.get("dxy_bias", "Neutral"),
                    key_drivers=raw.get("key_drivers", []) or [],
                    confidence=float(raw.get("confidence", 0) or 0),
                    provider=self._provider.name,
                    status="ok",
                ).to_dict()
            except AIProviderError as exc:
                logger.warning(f"AIAnalyzer.analyze_macro failed: {exc}")
                return MacroAnalysis(
                    status="error", error=_user_safe_error(exc), provider=self.provider_name
                ).to_dict()

        return self._cache.get_or_compute("macro", ttl, _compute)

    # ── Free-text summaries (daily report / research) ──────────────────

    def _summarize_text(self, text_blob: str) -> dict:
        """Shared plumbing for every "phrase these already-computed stats
        in plain English" call — AIAnalyzer only writes the prose, the
        numbers always come from storage/research modules upstream.
        """
        if not self.available:
            return {"status": "disabled", "provider": self.provider_name, "text": ""}
        try:
            summary = self._provider.summarize(text_blob)
            return {"status": "ok", "provider": self._provider.name, "text": summary}
        except AIProviderError as exc:
            logger.warning(f"AIAnalyzer summarize failed: {exc}")
            return {"status": "error", "provider": self.provider_name, "error": _user_safe_error(exc), "text": ""}

    def generate_daily_report(self, stats: dict) -> dict:
        """Plain-text daily summary from already-computed stats (e.g.
        storage/decision_db.summary() + storage/outcome_tracker.performance_summary()).
        """
        text_blob = (
            f"Decisions: total={stats.get('total')}, execute={stats.get('execute')}, "
            f"no_trade={stats.get('no_trade')}, execute_rate={stats.get('execute_rate')}. "
            f"Top NO_TRADE reasons: {stats.get('top_no_trade_reasons')}. "
            f"Outcomes: win_rate={stats.get('win_rate')}, "
            f"total_closed={stats.get('total_closed')}."
        )
        return self._summarize_text(text_blob)

    def generate_research_summary(self, stats: dict) -> dict:
        """Plain-text summary of the research/backtest state (hypothesis
        registry + latest full-pipeline backtest + regime performance
        matrix) — for the Research & Backtests dashboard tab. Never
        implies an engine should be enabled; that stays edge_gate.py's
        call, based on hypothesis status in registry.json.
        """
        text_blob = (
            f"Hypotheses: total={stats.get('total')}, passed={stats.get('passed')}, "
            f"failed={stats.get('failed')}, in_research={stats.get('research')}. "
            f"Latest full-pipeline backtest: avg_win_rate={stats.get('avg_wr')}, "
            f"avg_profit_factor={stats.get('avg_pf')}. "
            f"Regime performance matrix: {stats.get('regime_matrix')}."
        )
        return self._summarize_text(text_blob)

    def answer_research_question(self, context_text: str, question: str) -> dict:
        """Free-form Q&A grounded in already-computed research/backtest
        data (Backtesting Lab, AI Research Assistant) — the caller
        supplies both the question and the context (hypothesis registry,
        KPIs, sweep results, whatever's already loaded in the current
        dashboard view), so this never re-derives numbers none of
        AIAnalyzer's other methods trust upstream either. Same
        never-a-live-decision boundary as every method in this class:
        the prompt template itself instructs the model not to recommend
        changing a live parameter.
        """
        if not self.available:
            return {"status": "disabled", "provider": self.provider_name, "text": ""}
        if not question.strip():
            return {"status": "error", "provider": self.provider_name, "error": "Question is empty.", "text": ""}
        try:
            answer = self._provider.answer_question(context_text, question)
            return {"status": "ok", "provider": self._provider.name, "text": answer}
        except AIProviderError as exc:
            logger.warning(f"AIAnalyzer.answer_research_question failed: {exc}")
            return {"status": "error", "provider": self.provider_name, "error": _user_safe_error(exc), "text": ""}

    # ── Next-hypothesis suggestion (AI Copilot, Phase 4d) ────────────────

    _REQUIRED_SUGGESTION_FIELDS = (
        "title", "statement", "falsification_criteria", "distinct_from_prior_kill",
        "observation", "effect_size", "confidence", "possible_explanation",
    )
    _VALID_PRIORITIES = ("HIGH", "MEDIUM", "LOW")

    def suggest_next_hypothesis(self, context: dict, focus_hint: str = "") -> dict:
        """Draft a candidate for the operator's NEXT research hypothesis —
        never a decision, never written to research/results/registry.json
        by this method or anything it calls (execution/routes/ai.py's
        save_hypothesis_draft is the only thing allowed to persist a
        suggestion, and only into research/hypotheses/drafts/). `context`
        is the already-assembled registry summary + dead-list + backlog
        text (see execution/routes/ai.py::_build_copilot_context) — this
        method never re-derives it.
        """
        if not self.available:
            return HypothesisSuggestion(status="disabled", provider=self.provider_name).to_dict()
        try:
            context_text = json.dumps(context, indent=2, default=str)[:12000]
            raw = self._provider.suggest_hypothesis(context_text, focus_hint)
        except AIProviderError as exc:
            logger.warning(f"AIAnalyzer.suggest_next_hypothesis failed: {exc}")
            return HypothesisSuggestion(
                status="error", error=_user_safe_error(exc), provider=self.provider_name
            ).to_dict()

        missing = [f for f in self._REQUIRED_SUGGESTION_FIELDS if not str(raw.get(f, "")).strip()]
        suggested_experiments = raw.get("suggested_experiments")
        if not isinstance(suggested_experiments, list) or not suggested_experiments:
            missing.append("suggested_experiments")
        if raw.get("priority") not in self._VALID_PRIORITIES:
            missing.append("priority")
        if missing:
            logger.warning(f"AIAnalyzer.suggest_next_hypothesis: response missing fields {missing}")
            return HypothesisSuggestion(
                status="error",
                error="AI response was missing required fields — try again.",
                provider=self.provider_name,
            ).to_dict()

        return HypothesisSuggestion(
            title=raw.get("title", ""),
            statement=raw.get("statement", ""),
            why_this_might_be_true=raw.get("why_this_might_be_true", ""),
            data_required=raw.get("data_required", {}) or {},
            falsification_criteria=raw.get("falsification_criteria", ""),
            distinct_from_prior_kill=raw.get("distinct_from_prior_kill", ""),
            notes=raw.get("notes", ""),
            observation=raw.get("observation", ""),
            effect_size=raw.get("effect_size", ""),
            confidence=raw.get("confidence", ""),
            possible_explanation=raw.get("possible_explanation", ""),
            suggested_experiments=list(suggested_experiments or []),
            priority=raw.get("priority", ""),
            provider=self._provider.name,
            status="ok",
        ).to_dict()

    # ── Matrix research planner (Hypothesis Discovery Engine, Phase 3B) ──

    _REQUIRED_PLAN_FIELDS = ("reasoning_summary", "distinct_from_dead_list")
    _REQUIRED_CELL_FIELDS = ("symbol", "bundle_name", "timeframes", "engines", "risk_preset", "rationale")

    def propose_matrix_research_plan(self, context: dict, focus_hint: str = "") -> dict:
        """Draft a proposal of which NEW Matrix cells are worth generating
        next — a PLANNER output, never a verdict. `context` is the
        already-assembled evidence snapshot (backtest.matrix_research_
        planner.build_evidence_context()) — this method never re-derives
        it and never re-fetches D1 itself. The caller (execution/routes/
        matrix_ai.py) is the only thing allowed to persist this, always
        as status="DRAFT" in storage/matrix_ai_recommendations.py, together
        with the exact context dict's own hash — so if the Matrix evidence
        changes later, what the AI actually saw at proposal time stays
        reconstructable.

        Phase 3B-H hardening: NEVER silently truncates `context` before
        sending it. The audit's own P0 finding was that a prior [:16000]
        slice here meant the AI could reason over less evidence than what
        got hashed/persisted as the recommendation's "exact snapshot" —
        the ONE guarantee this whole audit trail exists to provide. The
        full canonical serialization is always sent whole; the caller
        (execution/routes/matrix_ai.py) is responsible for rejecting an
        oversized request BEFORE calling this method at all, never for
        truncating after the fact."""
        if not self.available:
            return MatrixResearchPlan(status="disabled", provider=self.provider_name).to_dict()
        try:
            context_text = json.dumps(context, indent=2, default=str)
            raw = self._provider.propose_matrix_plan(context_text, focus_hint)
        except AIProviderError as exc:
            logger.warning(f"AIAnalyzer.propose_matrix_research_plan failed: {exc}")
            return MatrixResearchPlan(
                status="error", error=_user_safe_error(exc), provider=self.provider_name
            ).to_dict()

        missing = [f for f in self._REQUIRED_PLAN_FIELDS if not str(raw.get(f, "")).strip()]
        proposed_cells = raw.get("proposed_next_cells")
        if not isinstance(proposed_cells, list) or not proposed_cells:
            missing.append("proposed_next_cells")
        else:
            for i, cell in enumerate(proposed_cells):
                if not isinstance(cell, dict) or any(not str(cell.get(f, "")).strip() for f in self._REQUIRED_CELL_FIELDS):
                    missing.append(f"proposed_next_cells[{i}]")
        if raw.get("priority") not in self._VALID_PRIORITIES:
            missing.append("priority")
        if missing:
            logger.warning(f"AIAnalyzer.propose_matrix_research_plan: response missing/malformed fields {missing}")
            return MatrixResearchPlan(
                status="error",
                error="AI response was missing required fields — try again.",
                provider=self.provider_name,
            ).to_dict()

        return MatrixResearchPlan(
            reasoning_summary=raw.get("reasoning_summary", ""),
            coverage_gaps=list(raw.get("coverage_gaps") or []),
            proposed_next_cells=proposed_cells,
            distinct_from_dead_list=raw.get("distinct_from_dead_list", ""),
            priority=raw.get("priority", ""),
            provider=self._provider.name,
            status="ok",
        ).to_dict()
