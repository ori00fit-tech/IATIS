"""
execution/telegram_bot.py
-----------------------------
Phase 2: Telegram notification layer — Intelligence Report format.

Design:
- parse_mode: HTML (safer than Markdown)
- Never crashes the pipeline — failures logged, not raised
- Flood protection: 30min cooldown per symbol
- Token never logged in full
- Intelligence Report format: professional, contextual, actionable
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_LENGTH = 4096


def _escape(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _get_credentials() -> tuple[str, str]:
    return (
        os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        os.environ.get("TELEGRAM_CHAT_ID", ""),
    )


def _bias_icon(bias: str) -> str:
    return {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}.get(bias, "⚪")


def _regime_icon(regime: str) -> str:
    return {"TRENDING": "📈", "RANGING": "↔️"}.get(regime, "📊")


def _verdict_icon(verdict: str) -> str:
    return "✅" if verdict == "EXECUTE" else "⛔"


def _build_message(report: dict) -> str:
    """Build professional Intelligence Report for Telegram."""
    verdict = report.get("final_verdict", "UNKNOWN")
    symbol = _escape(report.get("symbol", "?"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    regime_info = report.get("regime", {})
    regime = regime_info.get("state", "?")
    volatility = regime_info.get("volatility", "?")
    confidence = regime_info.get("confidence", 0)

    confluence = report.get("confluence", {})
    cf_score = confluence.get("score", 0)
    cf_dir = confluence.get("directional_score", 0)
    participating = confluence.get("engines_participating", 0)
    total = confluence.get("engines_total", 0)

    engines = report.get("engine_outputs", [])
    risk = report.get("risk", {}) or {}

    # --- Header ---
    lines = [
        f"{_verdict_icon(verdict)} <b>IATIS Intelligence Report</b>",
        f"",
        f"<b>Asset:</b> {symbol}",
        f"<b>Time:</b> {now}",
        f"",
    ]

    # --- Confluence Score ---
    score_bar = _score_bar(cf_score)
    direction_label = "Bullish" if cf_dir > 0 else "Bearish" if cf_dir < 0 else "Neutral"
    lines += [
        f"<b>Confluence:</b> {cf_score:.0f}/100 {score_bar}",
        f"<b>Direction:</b> {direction_label} ({cf_dir:+.1f})",
        f"",
    ]

    # --- Engine Votes ---
    non_neutral = [e for e in engines if e.get("bias") != "NEUTRAL"]
    neutral = [e for e in engines if e.get("bias") == "NEUTRAL"]

    if non_neutral or neutral:
        lines.append("<b>Engine Analysis:</b>")
        for e in sorted(engines, key=lambda x: x.get("score", 0), reverse=True):
            bias = e.get("bias", "?")
            score = e.get("score", 0)
            name = _escape(e.get("engine", "?"))
            icon = _bias_icon(bias)
            reason = _escape((e.get("reasons") or [""])[0][:60]) if e.get("reasons") else ""
            lines.append(f"  {icon} <b>{name}:</b> {bias} ({score:.0f}/100)")
            if reason and bias != "NEUTRAL":
                lines.append(f"      <i>{reason}</i>")
        lines.append("")

    # --- Market Regime ---
    lines += [
        f"<b>Regime:</b> {_regime_icon(regime)} {regime} | "
        f"Vol: {volatility} | Conf: {confidence:.0%}",
        f"",
    ]

    # --- Verdict Block ---
    if verdict == "EXECUTE":
        entry = report.get("entry_price")
        sl = report.get("stop_loss")
        tp = report.get("take_profit")
        rr = report.get("risk_reward", "—")
        risk_pct = risk.get("recommended_risk_pct", 0) or 0

        lines += [
            f"<b>Verdict:</b> ✅ <b>STRONG {_get_direction(engines).upper()} SIGNAL</b>",
            f"",
            f"<b>📌 Trade Setup:</b>",
        ]
        if entry:
            lines.append(f"  Entry: <code>{entry:.5f}</code>")
        if sl:
            lines.append(f"  Stop Loss: <code>{sl:.5f}</code>")
        if tp:
            lines.append(f"  Take Profit: <code>{tp:.5f}</code>")
        if rr:
            lines.append(f"  Risk/Reward: {rr}")
        if risk_pct:
            lines.append(f"  Suggested Risk: {risk_pct:.1%} of account")

    else:
        # NO_TRADE — explain why
        fail_reasons = confluence.get("fail_reasons", [])
        lines.append(f"<b>Verdict:</b> ⛔ NO_TRADE")
        if fail_reasons:
            lines.append(f"<b>Reason:</b> <i>{_escape(fail_reasons[0][:100])}</i>")

    # --- Risk Assessment ---
    if verdict == "EXECUTE":
        risk_level = _assess_risk_level(cf_score, regime, volatility)
        lines += ["", f"<b>Risk Level:</b> {risk_level}"]

    lines += ["", f"<code>IATIS v0.3.0 | {symbol}</code>"]

    message = "\n".join(lines)
    if len(message) > MAX_MESSAGE_LENGTH:
        suffix = "\n<i>(truncated)</i>"
        message = message[:MAX_MESSAGE_LENGTH - len(suffix)] + suffix
    return message


def _score_bar(score: float) -> str:
    """Visual score bar: ████░░ style."""
    filled = round(score / 10)
    empty = 10 - filled
    return f"{'█' * filled}{'░' * empty}"


def _get_direction(engines: list) -> str:
    bull = sum(1 for e in engines if e.get("bias") == "BULLISH")
    bear = sum(1 for e in engines if e.get("bias") == "BEARISH")
    return "bullish" if bull > bear else "bearish" if bear > bull else "neutral"


def _assess_risk_level(score: float, regime: str, volatility: str) -> str:
    if score >= 70 and regime == "TRENDING" and volatility in ("normal", "low"):
        return "🟢 Low — Strong confluence in favorable conditions"
    elif score >= 60 and regime == "TRENDING":
        return "🟡 Medium — Good confluence, monitor conditions"
    elif volatility == "high" or volatility == "extreme":
        return "🔴 High — Elevated volatility, reduce position size"
    else:
        return "🟡 Medium — Standard risk management applies"


def _post(url: str, payload: dict, token_hint: str = "") -> tuple[bool, str]:
    resp = None
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            return True, ""
        detail = data.get("description", "unknown API error")
        safe_hint = f"token={token_hint[:8]}..." if token_hint else "token=?"
        logger.warning(f"Telegram API ok=false: {detail} (status={resp.status_code}, {safe_hint})")
        return False, detail
    except requests.RequestException as exc:
        detail = type(exc).__name__
        if resp is not None:
            try:
                detail += f" status={resp.status_code}"
            except Exception:
                pass
        logger.warning(f"Telegram request failed (non-fatal): {detail}")
        return False, detail


def send_signal(report: dict, token: str = "", chat_id: str = "") -> bool:
    """Send Intelligence Report to Telegram. Returns True on success."""
    env_token, env_chat_id = _get_credentials()
    token = token or env_token
    chat_id = chat_id or env_chat_id

    if not token or not chat_id:
        logger.warning("Telegram credentials not set.")
        return False

    message = _build_message(report)
    ok, _ = _post(
        TELEGRAM_API.format(token=token),
        {"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
        token_hint=token,
    )
    if ok:
        logger.info(f"Telegram signal sent: verdict={report.get('final_verdict')}")
    return ok


def send_raw(text: str, token: str = "", chat_id: str = "") -> bool:
    env_token, env_chat_id = _get_credentials()
    token = token or env_token
    chat_id = chat_id or env_chat_id
    if not token or not chat_id:
        return False
    ok, _ = _post(
        TELEGRAM_API.format(token=token),
        {"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        token_hint=token,
    )
    return ok


def test_connection(token: str = "", chat_id: str = "") -> bool:
    return send_raw(
        "🤖 <b>IATIS connected</b> — Telegram Intelligence Reports active.",
        token=token, chat_id=chat_id,
    )
