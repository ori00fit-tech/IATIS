"""
execution/telegram_bot.py
-----------------------------
Phase 2: Telegram notification layer.

Design principles:
- Uses raw Telegram Bot API via requests (no extra library dependency)
- Never crashes the pipeline — send failures are logged, not raised
- Formats every message with full context: regime, engines, score,
  verdict, and reasons — so you understand the decision without
  opening any other file
- Respects Telegram's 4096-char message limit via automatic truncation
- Supports both EXECUTE and NO_TRADE messages with distinct formatting

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


def _get_credentials() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    return token, chat_id


def _build_message(report: dict) -> str:
    """Format a pipeline report into a readable Telegram message."""
    verdict = report.get("final_verdict", "UNKNOWN")
    symbol = report.get("symbol", "?")
    summary = report.get("summary", "")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # verdict icon
    icon = "✅" if verdict == "EXECUTE" else "⛔"
    regime_info = report.get("regime", {})
    regime = regime_info.get("state", "?")
    volatility = regime_info.get("volatility", "?")
    confidence = regime_info.get("confidence", 0)
    trend = regime_info.get("trend_strength", 0)

    # engine outputs
    engines = report.get("engine_outputs", [])
    engine_lines = []
    for e in engines:
        bias = e.get("bias", "?")
        score = e.get("score", 0)
        name = e.get("engine", "?")
        bias_icon = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}.get(bias, "⚪")
        engine_lines.append(f"  {bias_icon} {name}: {bias} ({score:.0f}/100)")

    confluence = report.get("confluence", {})
    score = confluence.get("score", 0)
    direction = confluence.get("directional_score", 0)
    participating = confluence.get("engines_participating", 0)
    total = confluence.get("engines_total", 0)
    weight_share = confluence.get("participating_weight_share", 0)

    fail_reasons = confluence.get("fail_reasons", [])
    risk = report.get("risk", {})
    risk_reasons = risk.get("reasons", []) if risk else []

    lines = [
        f"{icon} *IATIS — {symbol}*",
        f"🕐 {now}",
        "",
        f"📊 *Regime:* {regime} | vol: {volatility} | confidence: {confidence:.0%} | trend: {trend:+.2f}",
        "",
        "🧠 *Engines:*",
    ]
    lines.extend(engine_lines)
    lines += [
        "",
        f"⚖️ *Confluence:* {score:.1f}/100 (dir: {direction:+.1f})",
        f"   Engines: {participating}/{total} voted | weight coverage: {weight_share:.0%}",
    ]

    if verdict == "EXECUTE":
        entry = report.get("entry_price", "—")
        sl = report.get("stop_loss", "—")
        tp = report.get("take_profit", "—")
        rr = report.get("risk_reward", "—")
        risk_pct = risk.get("recommended_risk_pct", 0) if risk else 0

        def _fmt_price(v) -> str:
            return f"{v:.5f}" if isinstance(v, float) else str(v)

        lines += [
            "",
            "💰 *Trade Setup:*",
            f"   Entry: {_fmt_price(entry)}",
            f"   SL:    {_fmt_price(sl)}",
            f"   TP:    {_fmt_price(tp)}",
            f"   R:R    {rr}",
            f"   Risk:  {risk_pct:.2%} of account",
        ]
    else:
        if fail_reasons:
            lines += ["", "❌ *Confluence failed:*"]
            for r in fail_reasons:
                lines.append(f"   • {r}")
        if risk_reasons and risk.get("passed") is False:
            lines += ["", "🛡 *Risk gate failed:*"]
            for r in risk_reasons:
                lines.append(f"   • {r}")

    lines += ["", f"📋 *Verdict: {verdict}*", f"_{summary}_"]

    message = "\n".join(lines)
    _SUFFIX = "\n\n_(message truncated)_"
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[: MAX_MESSAGE_LENGTH - len(_SUFFIX)] + _SUFFIX
    return message


def send_signal(report: dict, token: str = "", chat_id: str = "") -> bool:
    """Send pipeline report to Telegram. Returns True on success.

    Never raises — failures are logged as warnings so the pipeline
    continues regardless of Telegram availability.

    Args:
        report: the full dict returned by main.run_pipeline()
        token: bot token (falls back to TELEGRAM_BOT_TOKEN env var)
        chat_id: chat id (falls back to TELEGRAM_CHAT_ID env var)
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
    url = TELEGRAM_API.format(token=token)

    try:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            logger.warning(f"Telegram API error: {data.get('description')}")
            return False

        logger.info(f"Telegram signal sent: verdict={report.get('final_verdict')}")
        return True

    except requests.RequestException as exc:
        logger.warning(f"Telegram send failed (non-fatal): {exc}")
        return False


def send_raw(text: str, token: str = "", chat_id: str = "") -> bool:
    """Send a plain text message — used for system alerts, errors, startup."""
    env_token, env_chat_id = _get_credentials()
    token = token or env_token
    chat_id = chat_id or env_chat_id

    if not token or not chat_id:
        return False

    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("ok", False)
    except requests.RequestException as exc:
        logger.warning(f"Telegram send_raw failed: {exc}")
        return False


def test_connection(token: str = "", chat_id: str = "") -> bool:
    """Send a test message to verify credentials work."""
    return send_raw(
        "🤖 *IATIS connected* — Telegram notifications are working.",
        token=token,
        chat_id=chat_id,
    )
