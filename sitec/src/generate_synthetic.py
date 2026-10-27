"""Generate explicitly synthetic, rule-labeled data for pipeline prototyping."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
BDI_COLUMNS = [f"BDI{i:02d}" for i in range(1, 6)]
STAI_COLUMNS = [f"STAI{i:02d}" for i in range(1, 21)]
QUESTION_COLUMNS = BDI_COLUMNS + STAI_COLUMNS
SPEED_LOW = 1.5
SPEED_HIGH = 20.0
QUESTIONS_PER_RESPONSE = 26  # includes the CHECK item

# In STAI, higher option indices express MORE of the statement. Items whose
# statement describes calm/security/contentment are therefore reverse keyed.
# This list is for the questionnaire version in the supplied workbook.
STAI_REVERSE = {1, 2, 8, 9, 11, 15, 16, 20}


def draw_answer(rng: np.random.Generator, severity: float, discrimination: float,
                reverse: bool = False) -> int:
    """Ordinal response 0..3, with increasing severity shifting toward 3."""
    direction = -1 if reverse else 1
    latent = severity * direction + rng.normal(0, 0.95)
    # Four ordered response bins. The noise and overlapping bins avoid a
    # deterministic question-to-label mapping.
    return int(np.clip(np.floor(1.5 + discrimination * latent), 0, 3))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=10_000, help="number of synthetic respondents")
    parser.add_argument("--positive-rate", type=float, default=0.20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=ROOT / "data/raw/synthetic_responses.csv")
    args = parser.parse_args()
    if args.n < 10 or not 0 < args.positive_rate < 1:
        parser.error("--n must be >= 10 and --positive-rate must be between 0 and 1")

    rng = np.random.default_rng(args.seed)
    n_positive = round(args.n * args.positive_rate)
    labels = np.r_[np.ones(n_positive, dtype=int), np.zeros(args.n - n_positive, dtype=int)]
    rng.shuffle(labels)
    rows = []

    for i, label in enumerate(labels):
        # Two synthetic reasons for class 1: out-of-range response speed or
        # elevated symptom profile despite an in-range response speed.
        speed_trigger = bool(label and rng.random() < 0.60)
        if speed_trigger:
            if rng.random() < 0.25:
                sec_per_answer = rng.uniform(0.35, SPEED_LOW - 0.02)
            else:
                sec_per_answer = rng.uniform(SPEED_HIGH + 0.5, 35.0)
        else:
            sec_per_answer = rng.uniform(SPEED_LOW + 0.05, SPEED_HIGH - 0.5)

        if label:
            severity = rng.beta(4.0, 2.2)
            # Keep some in-range positive records moderately symptomatic,
            # while speed-triggered cases have overlapping symptom scores.
            if not speed_trigger:
                severity = rng.uniform(0.48, 1.0)
        else:
            severity = rng.beta(1.5, 5.0)

        answers = {}
        for col in BDI_COLUMNS:
            answers[col] = draw_answer(rng, severity, rng.uniform(0.78, 1.10))
        for item, col in enumerate(STAI_COLUMNS, start=1):
            answers[col] = draw_answer(
                rng, severity, rng.uniform(0.72, 1.08), reverse=item in STAI_REVERSE
            )

        # A small response-style component creates within-person dependence.
        # CHECK01 is QC-only and never included by the training pipeline.
        answers["CHECK01"] = int(np.clip(round(1.5 + rng.normal(0, 0.9)), 0, 3))
        back_nav = int(rng.poisson(1.5 + 2.0 * label + 1.0 * severity))
        repeated = sum(1 for j in range(1, len(QUESTION_COLUMNS))
                       if answers[QUESTION_COLUMNS[j]] == answers[QUESTION_COLUMNS[j - 1]])
        rows.append({
            "respondent_id": f"SYN-{i + 1:06d}",
            **answers,
            "avg_sec_per_answer": round(float(sec_per_answer), 3),
            "duration_sec": round(float(sec_per_answer * QUESTIONS_PER_RESPONSE), 2),
            "back_navigation_count": back_nav,
            # Proxy definition for the prototype: adjacent equal option indices.
            # Replace when the product's definition of "matching answers" is known.
            "consistent_answers_count": repeated,
            "needs_attention": int(label or sec_per_answer < SPEED_LOW or sec_per_answer > SPEED_HIGH),
            "synthetic": 1,
        })

    frame = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"Wrote {len(frame)} SYNTHETIC rows to {args.out}")
    print(f"Class 1: {frame.needs_attention.mean():.1%} ({frame.needs_attention.sum()}/{len(frame)})")
    print("Speed rule violations:", int(((frame.avg_sec_per_answer < SPEED_LOW) |
                                        (frame.avg_sec_per_answer > SPEED_HIGH)).sum()))


if __name__ == "__main__":
    main()
