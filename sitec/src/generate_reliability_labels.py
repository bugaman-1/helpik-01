"""Attach an imbalanced synthetic response-validity label to base responses."""
from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from reliability_features import add_reliability_features, suspiciousness_score


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path,
                        default=ROOT / "data/raw/synthetic_reliability_base.csv")
    parser.add_argument("--model", type=Path,
                        default=ROOT / "models/synthetic_baseline.joblib")
    parser.add_argument("--out", type=Path,
                        default=ROOT / "data/raw/synthetic_reliability_labeled.csv")
    parser.add_argument("--unreliable-rate", type=float, default=0.05,
                        help="target fraction labeled unreliable; default 5%%")
    parser.add_argument("--seed", type=int, default=2048)
    args = parser.parse_args()
    if not 0 < args.unreliable_rate < 1:
        parser.error("--unreliable-rate must be between 0 and 1")

    data = pd.read_csv(args.base)
    bundle = joblib.load(args.model)
    model_x = data[bundle["feature_columns"]]
    probabilities = bundle["model"].predict_proba(model_x)[:, 1]
    speed_alert = (data["avg_sec_per_answer"] < 1.5) | (data["avg_sec_per_answer"] > 20)
    data["previous_attention_class"] = (
        (probabilities >= bundle["threshold"]) | speed_alert
    ).astype(int)

    featured = add_reliability_features(data)
    score = suspiciousness_score(featured)
    rng = np.random.default_rng(args.seed)
    # Tiny jitter avoids deterministic tie ordering while preserving ranking.
    score = score + rng.uniform(0, 1e-8, size=len(data))
    total_positive = round(len(data) * args.unreliable_rate)
    from_previous_positive = round(total_positive * 0.98)
    from_previous_negative = total_positive - from_previous_positive
    groups = {
        1: from_previous_positive,
        0: from_previous_negative,
    }
    selected = []
    for previous_class, count in groups.items():
        candidates = np.flatnonzero(data["previous_attention_class"].to_numpy() == previous_class)
        if len(candidates) < count:
            raise ValueError(
                f"Need {count} records from previous class {previous_class}, found {len(candidates)}"
            )
        chosen = candidates[np.argsort(score[candidates])[-count:]] if count else np.array([], dtype=int)
        selected.extend(chosen.tolist())

    data["is_unreliable"] = 0
    data.loc[selected, "is_unreliable"] = 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(args.out, index=False, encoding="utf-8-sig")
    positives = data[data["is_unreliable"] == 1]
    overlap = positives["previous_attention_class"].mean()
    print(f"Wrote {len(data)} synthetic rows -> {args.out}")
    print(f"is_unreliable=1: {len(positives)} ({len(positives)/len(data):.2%})")
    print(f"previous class 1 among is_unreliable=1: {overlap:.2%}")


if __name__ == "__main__":
    main()
