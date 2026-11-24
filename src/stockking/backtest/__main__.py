"""백테스트 실행 CLI (예제 전략).

python -m stockking.backtest --csv bars.csv --commission 0.00015 --sell-tax 0.002 --slippage-bps 5 --source "COST_MODEL.md vX"
"""

import argparse
import json

from ..data import load_bars_csv
from ..strategy import SmaCrossStrategy
from .costs import CostModel
from .engine import BacktestConfig, BacktestEngine


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--cash", type=int, default=100_000)
    ap.add_argument("--commission", type=float, required=True)
    ap.add_argument("--sell-tax", type=float, required=True)
    ap.add_argument("--slippage-bps", type=float, required=True)
    ap.add_argument("--source", default="UNCONFIRMED")
    ap.add_argument("--fast", type=int, default=5)
    ap.add_argument("--slow", type=int, default=20)
    a = ap.parse_args()
    cost = CostModel(a.commission, a.sell_tax, a.slippage_bps, source=a.source)
    res = BacktestEngine(BacktestConfig(a.cash, cost)).run(SmaCrossStrategy(a.fast, a.slow), load_bars_csv(a.csv))
    print(json.dumps(res.summary(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
