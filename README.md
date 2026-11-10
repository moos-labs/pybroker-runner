# pybroker-runner

Runs [PyBroker](https://www.pybroker.com/) analyses on GitHub Actions,
where the commands to execute live in a repository **Actions variable** —
not in code.

PyBroker is a Python framework for developing algorithmic trading
strategies — rules-based and machine-learning strategies alike — with a
fast Numba-accelerated backtesting engine, walkforward analysis, and
data sources for Yahoo Finance, Alpaca, AKShare, or your own provider.

## The pattern (one repo per tool)

1. **Edit the commands**: repo *Settings → Secrets and variables → Actions →
   Variables* → set the `COMMANDS` variable to whatever shell commands this
   run should execute (multi-line works). The same can be done through the
   GitHub API (`PUT /repos/{owner}/{repo}/actions/variables/COMMANDS`).
2. **Trigger manually**: *Actions* tab → “Run analysis” → *Run workflow*
   (or `POST /repos/{owner}/{repo}/actions/workflows/run.yml/dispatches`).
   The workflow has **no other triggers** — it runs only when dispatched.
3. **Collect results**: the workflow uploads everything under `results/` as
   the `results` build artifact (`if: always()`, so partial results survive
   a failing step). Download it from the run page or via the API.

The workflow itself is generic: checkout → Python 3.11 → `pip install -r
requirements.txt` → run `${{ vars.COMMANDS }}` → upload `results/`.
To run a different analysis, change the variable — not the workflow.

## Multi-market by design

Nothing in the workflow or the runner pattern is tied to one exchange.
Scripts take the **ticker symbol and analysis parameters as inputs** (CLI
args and/or the `COMMANDS` variable), so the same repo can analyse an EGX
stock today and a US stock (e.g. markets available through Nsave or Alpaca)
tomorrow — only the inputs change.

## Pilot: AAPL 20-day SMA crossover

`scripts/backtest.py` is a generic PyBroker backtest: ticker as a CLI
argument, with `--start`, `--end`, `--sma-period` and `--initial-cash`
options. Pilot defaults: `AAPL`, last ~2 years, 20-day SMA, $100,000.

- **Data**: PyBroker's built-in `YFinance` data source (PyBroker registers
  a `yfinance` source that downloads via yfinance internally).
- **Strategy** (rules-based): buy when the close crosses **above** its
  20-day simple moving average — the SMA is computed with PyBroker's
  indicator API (`pybroker.indicator`) — and sell all shares when the
  close crosses **below** it. Executed with `Strategy(...).backtest()`.
- **Output**: `results/metrics.json` with exactly the metrics PyBroker
  returned (metric names as-is, e.g. total return, final portfolio
  value, Sharpe, max drawdown, trade count where present) plus the data
  range used. If the Yahoo download fails from the runner, the exact
  error is recorded in `metrics.json` instead of invented numbers.

Pilot `COMMANDS` value:

```sh
mkdir -p results
python scripts/backtest.py 2>&1 | tee results/run.log
```
