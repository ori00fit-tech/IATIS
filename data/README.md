# Real Historical Data

This directory holds real (non-synthetic) historical OHLCV datasets used
for Phase 2 testing, hypothesis validation (`research/`), and eventually
backtesting (Phase 5).

## Current datasets

### `EURUSD_M1_2026-03-16_2026-06-19.csv`
- **Symbol:** EURUSD
- **Timeframe:** M1 (1-minute bars)
- **Range:** 2026-03-16 02:58 → 2026-06-19 20:59 UTC (~3 months)
- **Rows:** 100,000
- **Format:** headerless, tab-separated, column order
  `datetime, open, high, low, close, volume`
- **Loaded via:**
  ```python
  from core.data_loader import load_from_csv
  df = load_from_csv(
      "data/EURUSD_M1_2026-03-16_2026-06-19.csv",
      has_header=False,
      sep="\t",
  )
  ```
- **Validation status:** passes `core.data_validator.validate_ohlcv()` —
  no nulls, no duplicate timestamps, no malformed OHLC relationships,
  monotonically increasing index.

## Why this format needed a loader change

This dataset is headerless and tab-separated — a real export format that
`load_from_csv()` didn't originally support (it assumed a header row).
Rather than reformat the data to fit the loader, the loader was extended
(`has_header` / `sep` / `no_header_columns` parameters) to handle this
as a first-class, generally-supported case, since it's a common
broker/platform export shape, not a one-off. See
`tests/test_csv_loader.py::test_loads_headerless_tab_separated_format`.

## Usage note on file size / git

CSV historical datasets are excluded from version control via
`.gitignore` (`data/*.csv`) because they're large, regenerable-by-source,
and not meant to be diffed. This README is the durable record of what
data exists and where it came from — if you're cloning this repo fresh,
you'll need to re-add the actual CSV file(s) yourself.

## Adding a new dataset

1. Place the file here with a descriptive name:
   `<SYMBOL>_<TIMEFRAME>_<START>_<END>.csv`
2. Document it in this README (format, loader args, validation status).
3. Confirm it passes `validate_ohlcv()` before using it in any
   `research/experiments/` script — a hypothesis test result is only
   as trustworthy as the data behind it.
