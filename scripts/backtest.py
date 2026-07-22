"""Walk-forward backtest of baselines, net of costs, vs base rate."""
import json

from sgt.backtest.harness import run_all
from sgt.features.store import get_training_frame

if __name__ == "__main__":
    df = get_training_frame()
    print(f"Training frame: {len(df)} rows")
    report = run_all(df, candidate_factory=None, n_splits=5)
    print(json.dumps(report, indent=2, default=str))
