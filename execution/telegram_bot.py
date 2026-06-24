"""
execution/telegram_bot.py
-----------------------------
Phase 2: Telegram notification layer.

Design principles:
- Uses raw Telegram Bot API via requests (no extra library dependency)
- parse_mode: HTML (not Markdown) — HTML is far more predictable in
  Telegram because special characters in dynamic content (prices,
  reasons, symbol names) don't accidentally trigger Markdown parsing.
  The only thing that can break HTML is unescaped < > & — we handle
  that with _escape().
- Never crashes the pipeline — send failures are logged with the full
  API error body, not just the exception message.
- Full error logging: when the API returns a non-ok response, we log
  the exact status code and response body so the cause is diagnosable.

Setup: add to .env
    TELEGRAM_BOT_TOKEN=<your token>
    TELEGRAM_CHAT_ID=<your chat id>
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_LENGTH = 4096


class TelegramError(Exception):
    """Raised when Telegram API returns an error (used in tests only —
    send_signal itself never raises to avoid crashing the pipeline)."""


def _escape(text: str) -> str:
    """Escape HTML special characters in dynamic content.
    Telegram HTML mode only requires escaping < > &.
    """
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _get_credentials() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    return token, chat_id


def _build_message(report: dict) -> str:
    """Format a pipeline report into an HTML Telegram message."""
    verdict = report.get("final_verdict", "UNKNOWN")
    symbol = _escape(report.get("symbol", "?"))
    summary = _escape(report.get("summary", ""))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    icon = "✅" if verdict == "EXECUTE" else "⛔"

    regime_info = report.get("regime", {})
    regime = _escape(regime_info.get("state", "?"))
    volatility = _escape(regime_info.get("volatility", "?"))
    confidence = regime_info.get("confidence", 0)
    trend = regime_info.get("trend_strength", 0)

    engines = report.get("engine_outputs", [])
    engine_lines = []
    for e in engines:
        bias = _escape(e.get("bias", "?"))
        score = e.get("score", 0)
        name = _escape(e.get("engine", "?"))
        bias_icon = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}.get(
            e.get("bias", ""), "⚪"
        )
        engine_lines.append(f"  {bias_icon} {name}: {bias} ({score:.0f}/100)")

    confluence = report.get("confluence", {})
    cf_score = confluence.get("score", 0)
    cf_dir = confluence.get("directional_score", 0)
    participating = confluence.get("engines_participating", 0)
    total = confluence.get("engines_total", 0)
    weight_share = confluence.get("participating_weight_share", 0)

    fail_reasons = confluence.get("fail_reasons", [])
    risk = report.get("risk", {})
    risk_reasons = risk.get("reasons", []) if risk else []

    lines = [
        f"{icon} <b>IATIS — {symbol}</b>",
        f"🕐 {now}",
        "",
        f"📊 <b>Regime:</b> {regime} | vol: {volatility} | "
        f"confidence: {confidence:.0%} | trend: {trend:+.2f}",
        "",
        "🧠 <b>Engines:</b>",
    ]
    lines.extend(engine_lines)
    lines += [
        "",
        f"⚖️ <b>Confluence:</b> {cf_score:.1f}/100 (dir: {cf_dir:+.1f})",
        f"   Engines: {participating}/{total} voted | "
        f"weight coverage: {weight_share:.0%}",
    ]

    if verdict == "EXECUTE":
        entry = report.get("entry_price", "—")
        sl = report.get("stop_loss", "—")
        tp = report.get("take_profit", "—")
        rr = report.get("risk_reward", "—")
        risk_pct = risk.get("recommended_risk_pct", 0) if risk else 0

        def _fmt_price(v) -> str:
            return f"{v:.5f}" if isinstance(v, float) else _escape(str(v))

        lines += [
            "",
            "💰 <b>Trade Setup:</b>",
            f"   Entry: {_fmt_price(entry)}",
            f"   SL:    {_fmt_price(sl)}",
            f"   TP:    {_fmt_price(tp)}",
            f"   R:R    {_escape(str(rr))}",
            f"   Risk:  {risk_pct:.2%} of account",
        ]
    else:
        if fail_reasons:
            lines += ["", "❌ <b>Confluence failed:</b>"]
            for r in fail_reasons:
                lines.append(f"   • {_escape(r)}")
        if risk_reasons and risk.get("passed") is False:
            lines += ["", "🛡 <b>Risk gate failed:</b>"]
            for r in risk_reasons:
                lines.append(f"   • {_escape(r)}")

    lines += [
        "",
        f"📋 <b>Verdict: {_escape(verdict)}</b>",
        f"<i>{summary}</i>",
    ]

    message = "\n".join(lines)
    _SUFFIX = "\n\n<i>(message truncated)</i>"
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[: MAX_MESSAGE_LENGTH - len(_SUFFIX)] + _SUFFIX
    return message


def _post(url: str, payload: dict, token_hint: str = "") -> tuple[bool, str]:
    """HTTP POST with error logging that never exposes the full token."""
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
        detail = type(exc).__name__  # never include URL (contains token)
        if resp is not None:
            try:
                detail += f" status={resp.status_code}"
            except Exception:
                pass
        logger.warning(f"Telegram request failed (non-fatal): {detail}")
        return False, detail


def send_signal(report: dict, token: str = "", chat_id: str = "") -> bool:
    """Send pipeline report to Telegram. Returns True on success.

    Never raises — failures are logged so the pipeline continues
    regardless of Telegram availability.
    """
    env_token, env_chat_id = _get_credentials()
    token = token or env_token
    chat_id = chat_id or env_chat_id

    if not token or not chat_id:
        logger.warning(
            "Telegram credentials not set. "
            "Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env"
        )
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
    """Send a plain text message — used for system alerts and startup."""
    env_token, env_chat_id = _get_credentials()
    token = token or env_token
    chat_id = chat_id or env_chat_id

    if not token or not chat_id:
        return False

    ok, _ = _post(
        TELEGRAM_API.format(token=token),
        {"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
    )
    return ok


def test_connection(token: str = "", chat_id: str = "") -> bool:
    """Send a test message to verify credentials work. Costs 0 API credits."""
    return send_raw(
        "🤖 <b>IATIS connected</b> — Telegram notifications are working.",
        token=token,
        chat_id=chat_id,
    )
