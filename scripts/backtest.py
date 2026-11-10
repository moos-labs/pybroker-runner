#!/usr/bin/env python3
"""PyBroker rules-based backtest (generic runner script).

Strategy: buy when the close crosses above its N-day simple moving
average (SMA, computed with PyBroker's indicator API) and sell (exit
the whole long position) when the close crosses below it.

The ticker and every parameter arrive as CLI arguments — nothing is
tied to one symbol or market beyond the pilot defaults below.

Pilot defaults: AAPL, last ~2 years, 20-day SMA, $100,000 initial cash,
data from PyBroker's built-in YFinance data source (the ``yfinance``
source PyBroker registers, which downloads via yfinance internally).

Outputs ``results/metrics.json`` containing exactly the metrics the
library returned (metric names as-is) plus the data range used. Any
failure — including a Yahoo download failure from the runner — is
recorded verbatim in that file (``status``/``error``/``traceback``).
"""

import argparse
import json
import os
import sys
import traceback
from datetime import date, timedelta

import numpy as np
import pandas as pd


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ticker", nargs="?", default="AAPL",
                   help="Ticker symbol (default: AAPL)")
    p.add_argument("--start", default=None,
                   help="Start date YYYY-MM-DD (default: ~2 years ago)")
    p.add_argument("--end", default=None,
                   help="End date YYYY-MM-DD (default: today)")
    p.add_argument("--sma-period", type=int, default=20,
                   help="SMA lookback in bars (default: 20)")
    p.add_argument("--initial-cash", type=float, default=100_000,
                   help="Initial cash (default: 100000)")
    p.add_argument("--out", default="results/metrics.json",
                   help="Where to write metrics JSON")
    return p.parse_args(argv)


def to_pybroker_date(d: date) -> str:
    """PyBroker examples use M/D/YYYY date strings."""
    return f"{d.month}/{d.day}/{d.year}"


def sma(bar_data, lookback):
    """Simple moving average of close, via PyBroker's indicator API."""
    close = pd.Series(np.asarray(bar_data.close, dtype=float))
    return close.rolling(lookback).mean().to_numpy()


def make_exec_fn(indicator_name):
    def exec_fn(ctx):
        sma_vals = ctx.indicator(indicator_name)
        close = ctx.close
        if len(close) < 2 or len(sma_vals) < 2:
            return
        if np.isnan(sma_vals[-1]) or np.isnan(sma_vals[-2]):
            return
        crossed_up = close[-2] <= sma_vals[-2] and close[-1] > sma_vals[-1]
        crossed_down = close[-2] >= sma_vals[-2] and close[-1] < sma_vals[-1]
        if not ctx.long_pos() and crossed_up:
            ctx.buy_shares = ctx.calc_target_shares(1)
        elif ctx.long_pos() and crossed_down:
            ctx.sell_all_shares()

    return exec_fn


def jsonable(value):
    """Convert library values (numpy/Decimal/datetime...) to JSON-safe."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return f if np.isfinite(f) else str(f)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, (str, int)) or value is None:
        return value
    # Decimal and anything else numeric-ish
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def extract_metrics(result):
    """Pull headline metrics from a PyBroker TestResult, names as-is.

    PyBroker exposes metrics as ``result.metrics_df`` (a DataFrame with
    name/value columns) in current versions; older/other shapes
    (``result.metrics``) are handled defensively. Values are copied
    verbatim — nothing is recomputed here.
    """
    metrics = {}

    metrics_df = getattr(result, "metrics_df", None)
    if metrics_df is not None:
        try:
            df = metrics_df() if callable(metrics_df) else metrics_df
            if hasattr(df, "itertuples"):
                cols = list(df.columns)
                if "name" in cols and "value" in cols:
                    for row in df.itertuples(index=False):
                        metrics[str(getattr(row, "name"))] = jsonable(
                            getattr(row, "value"))
                else:
                    metrics = jsonable(df.to_dict())
            else:
                metrics = jsonable(df)
        except Exception:
            metrics = {}

    if not metrics:
        raw = getattr(result, "metrics", None)
        if raw is not None:
            try:
                raw = raw() if callable(raw) else raw
                if hasattr(raw, "to_dict"):
                    metrics = jsonable(raw.to_dict())
                elif isinstance(raw, dict):
                    metrics = jsonable(raw)
                else:
                    metrics = jsonable(vars(raw))
            except Exception:
                metrics = {}

    return metrics


def main(argv=None):
    args = parse_args(argv)
    end = date.fromisoformat(args.end) if args.end else date.today()
    start = (date.fromisoformat(args.start) if args.start
             else end - timedelta(days=2 * 365))

    payload = {
        "tool": "pybroker",
        "ticker": args.ticker,
        "strategy": (
            f"long when close crosses above {args.sma_period}-day SMA, "
            f"exit all when close crosses below it"
        ),
        "initial_cash": args.initial_cash,
        "sma_period": args.sma_period,
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "status": "not_run",
    }

    try:
        import pybroker
        from pybroker import Strategy, StrategyConfig, YFinance

        indicator_name = f"sma_{args.sma_period}"
        sma_ind = pybroker.indicator(
            indicator_name, sma, lookback=args.sma_period)

        yfinance = YFinance()
        start_s = to_pybroker_date(start)
        end_s = to_pybroker_date(end)

        # Verify the download first so a data failure is reported as
        # exactly that, and record the actual range returned.
        df = yfinance.query(args.ticker, start_s, end_s)
        if df is None or len(df) == 0:
            raise RuntimeError(
                f"YFinance query returned no rows for {args.ticker} "
                f"{start_s}..{end_s}")
        dates = pd.to_datetime(df["date"]) if "date" in df.columns else None
        payload["data_range"] = {
            "rows": int(len(df)),
            "first_date": (str(dates.min().date()) if dates is not None
                           else None),
            "last_date": (str(dates.max().date()) if dates is not None
                          else None),
        }
        print(f"Downloaded {len(df)} bars for {args.ticker}: "
              f"{payload['data_range']}")

        config = StrategyConfig(initial_cash=args.initial_cash)
        try:
            strategy = Strategy(yfinance, start_s, end_s, config=config)
        except TypeError:
            strategy = Strategy(yfinance, start_date=start_s,
                                end_date=end_s, config=config)
        strategy.add_execution(
            make_exec_fn(indicator_name), args.ticker, indicators=sma_ind)
        result = strategy.backtest(warmup=args.sma_period)

        metrics = extract_metrics(result)
        payload["metrics"] = metrics
        payload["status"] = "ok"

        # Headline figures, copied from the library metrics when present.
        for key in ("total_return_pct", "end_market_value",
                    "initial_market_value"):
            if key in metrics:
                payload[key] = metrics[key]

        try:
            orders = getattr(result, "orders", None)
            if orders is not None and hasattr(orders, "__len__"):
                payload["orders_count"] = int(len(orders))
        except Exception:
            pass

        print(json.dumps(payload, indent=2, default=str))

    except Exception as exc:  # record the exact failure, do not invent data
        payload["status"] = "error"
        payload["error"] = f"{type(exc).__name__}: {exc}"
        payload["traceback"] = traceback.format_exc()
        print(payload["error"], file=sys.stderr)
        traceback.print_exc()
        write_out(args.out, payload)
        return 1

    write_out(args.out, payload)
    return 0


def write_out(path, payload):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"Wrote {path}")


if __name__ == "__main__":
    sys.exit(main())
