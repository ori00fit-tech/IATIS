"""
research/h021_readiness.py
-----------------------------
Read-only H021 data-readiness computation (MarketAux sentiment A/B,
research/results/registry.json). Computes, fresh from the real collected
log on every call, whether each primary carrier symbol (XAUUSD, BTCUSD,
ETHUSD) has enough sentiment-informative TEST-slice records to make H021's
own pre-registered decision rule meaningful.

Single source of truth. Nothing in this module or any caller may hardcode
a readiness number, a per-symbol count, or a verdict -- every value here
is derived from data/marketaux_sentiment_log.jsonl (gitignored, VPS-only,
written by scripts/collect_marketaux_sentiment.py) as it actually exists
at call time. If the file is absent, empty, or thin, that is reported
honestly as NOT_READY -- never guessed, never backfilled, never faked.

Two distinct counts are computed and reported separately per carrier,
because conflating them is exactly the mistake this module exists to
prevent:
  - "raw" TEST record count -- every collector run recorded for this
    symbol in the TEST slice, regardless of whether MarketAux returned
    any real signal that run.
  - "informative" TEST record count -- TEST records where
    mean_sentiment != 0 (a real, non-zero sentiment score was returned).
    This is the number H021's own decision rule's "~30 sentiment-informed
    decisions per carrier" threshold is written against -- NOT the raw
    count, which is a strictly weaker (larger, more optimistic) number.

Chronological TRAIN(65%)/TEST(35%) split matches H021's own registered
method verbatim (research/results/registry.json's H021.method field).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "data" / "marketaux_sentiment_log.jsonl"

PRIMARY_CARRIERS: tuple[str, ...] = ("XAUUSD", "BTCUSD", "ETHUSD")
TRAIN_FRACTION = 0.65
MIN_INFORMATIVE_TEST_RECORDS = 30  # H021's own decision rule: "~30 sentiment-informed decisions per carrier on TEST"


@dataclass(frozen=True)
class CarrierReadiness:
    symbol: str
    total_records: int
    train_records: int
    test_records: int
    train_article_gt_zero: int
    test_article_gt_zero: int
    train_sentiment_informative: int
    test_sentiment_informative: int
    earliest_collected_at: str | None
    latest_collected_at: str | None
    ready: bool
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "total_records": self.total_records,
            "train_records": self.train_records,
            "test_records": self.test_records,
            "train_article_gt_zero": self.train_article_gt_zero,
            "test_article_gt_zero": self.test_article_gt_zero,
            "train_sentiment_informative": self.train_sentiment_informative,
            "test_sentiment_informative": self.test_sentiment_informative,
            "earliest_collected_at": self.earliest_collected_at,
            "latest_collected_at": self.latest_collected_at,
            "ready": self.ready,
            "note": self.note,
        }


@dataclass(frozen=True)
class H021ReadinessReport:
    log_exists: bool
    log_path: str
    total_records_all_symbols: int
    carriers: list[CarrierReadiness] = field(default_factory=list)
    overall_ready: bool = False
    min_informative_test_records: int = MIN_INFORMATIVE_TEST_RECORDS
    train_fraction: float = TRAIN_FRACTION
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "log_exists": self.log_exists,
            "log_path": self.log_path,
            "total_records_all_symbols": self.total_records_all_symbols,
            "carriers": [c.to_dict() for c in self.carriers],
            "overall_ready": self.overall_ready,
            "min_informative_test_records": self.min_informative_test_records,
            "train_fraction": self.train_fraction,
            "note": self.note,
        }


def load_records(path: Path = LOG_PATH) -> list[dict]:
    """Every line of the real jsonl log, parsed. Malformed lines are
    skipped (never fabricated into a fake record, never aborts the whole
    read) -- append-only files written by a long-running collector can
    legitimately have a torn last line from a mid-write crash."""
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
    return records


def _is_informative(record: dict) -> bool:
    sentiment = record.get("mean_sentiment")
    return isinstance(sentiment, (int, float)) and sentiment != 0


def _has_articles(record: dict) -> bool:
    count = record.get("article_count")
    return isinstance(count, (int, float)) and count > 0


def compute_carrier_readiness(
    records: list[dict],
    symbol: str,
    *,
    train_fraction: float = TRAIN_FRACTION,
    min_informative_test_records: int = MIN_INFORMATIVE_TEST_RECORDS,
) -> CarrierReadiness:
    """Chronological TRAIN/TEST split (by collected_at, ascending) for one
    symbol's real collected records. Readiness is judged ONLY against the
    TEST-slice informative count -- matching H021's own decision rule,
    which needs sentiment-informed decisions on the held-out slice, not
    the training slice."""
    own = [r for r in records if r.get("symbol") == symbol]
    own.sort(key=lambda r: str(r.get("collected_at") or ""))

    total = len(own)
    split = round(total * train_fraction)
    train, test = own[:split], own[split:]

    train_article_gt_zero = sum(1 for r in train if _has_articles(r))
    test_article_gt_zero = sum(1 for r in test if _has_articles(r))
    train_informative = sum(1 for r in train if _is_informative(r))
    test_informative = sum(1 for r in test if _is_informative(r))

    ready = test_informative >= min_informative_test_records
    if total == 0:
        note = "No collected records for this symbol at all."
    elif ready:
        note = f"TEST-slice informative count ({test_informative}) meets the {min_informative_test_records}-record threshold."
    else:
        note = (
            f"TEST-slice informative count ({test_informative}) is below the "
            f"{min_informative_test_records}-record threshold — INSUFFICIENT_DATA per H021's decision rule."
        )

    return CarrierReadiness(
        symbol=symbol,
        total_records=total,
        train_records=len(train),
        test_records=len(test),
        train_article_gt_zero=train_article_gt_zero,
        test_article_gt_zero=test_article_gt_zero,
        train_sentiment_informative=train_informative,
        test_sentiment_informative=test_informative,
        earliest_collected_at=str(own[0]["collected_at"]) if own and own[0].get("collected_at") else None,
        latest_collected_at=str(own[-1]["collected_at"]) if own and own[-1].get("collected_at") else None,
        ready=ready,
        note=note,
    )


def compute_h021_readiness(
    path: Path = LOG_PATH,
    *,
    carriers: tuple[str, ...] = PRIMARY_CARRIERS,
    train_fraction: float = TRAIN_FRACTION,
    min_informative_test_records: int = MIN_INFORMATIVE_TEST_RECORDS,
) -> H021ReadinessReport:
    """The single, real, computed-fresh-on-every-call H021 data-readiness
    verdict. overall_ready requires EVERY primary carrier individually
    ready -- 2 of 3 is not sufficient (H021's decision rule is a joint
    carrier-group condition, not a per-symbol opt-in)."""
    log_exists = path.exists()
    records = load_records(path)

    carrier_reports = [
        compute_carrier_readiness(
            records, symbol,
            train_fraction=train_fraction,
            min_informative_test_records=min_informative_test_records,
        )
        for symbol in carriers
    ]
    overall_ready = bool(carrier_reports) and all(c.ready for c in carrier_reports)

    if not log_exists:
        note = f"{path} does not exist — collector has not run (or has not been enabled) yet."
    elif not records:
        note = f"{path} exists but contains zero parseable records."
    elif overall_ready:
        note = "All primary carriers meet the TEST-slice informative-record threshold. H021 DATA READY."
    else:
        not_ready = [c.symbol for c in carrier_reports if not c.ready]
        note = f"H021 DATA NOT READY — not yet sufficient for: {', '.join(not_ready)}."

    return H021ReadinessReport(
        log_exists=log_exists,
        log_path=str(path),
        total_records_all_symbols=len(records),
        carriers=carrier_reports,
        overall_ready=overall_ready,
        min_informative_test_records=min_informative_test_records,
        train_fraction=train_fraction,
        note=note,
    )
