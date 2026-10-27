"""Baseline binary classifier scaffold. Requires clinician-reviewed labels."""
from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, classification_report,
                             confusion_matrix, roc_auc_score)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[1]
QUESTION_COLUMNS = [f"BDI{i:02d}" for i in range(1, 6)] + [f"STAI{i:02d}" for i in range(1, 21)]
NUMERIC_COLUMNS = ["avg_sec_per_answer", "duration_sec", "back_navigation_count",
                   "consistent_answers_count"]
TARGET = "needs_attention"
ID = "respondent_id"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=ROOT / "data/raw/responses_labeled.csv")
    parser.add_argument("--model-out", type=Path, default=ROOT / "models/baseline.joblib")
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    data = pd.read_csv(args.data)
    required = [*QUESTION_COLUMNS, *NUMERIC_COLUMNS, TARGET]
    missing = sorted(set(required) - set(data.columns))
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    if data[TARGET].isna().any() or not set(data[TARGET].unique()).issubset({0, 1}):
        raise ValueError(f"{TARGET} must contain clinician-reviewed 0/1 labels only")
    if data[TARGET].nunique() != 2:
        raise ValueError("Training needs examples of both target classes")
    # A speed outside the specified interval must always be labeled positive.
    speed_alert = (data["avg_sec_per_answer"] < 1.5) | (data["avg_sec_per_answer"] > 20)
    if (speed_alert & (data[TARGET] != 1)).any():
        raise ValueError("Speed <1.5 or >20 sec/answer must have needs_attention=1")

    # The control item is deliberately not a predictive feature; use it for QC upstream.
    categorical = QUESTION_COLUMNS
    preprocessing = ColumnTransformer([
        ("answers", Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]), categorical),
        ("numeric", Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]), NUMERIC_COLUMNS),
    ])
    model = Pipeline([
        ("features", preprocessing),
        ("classifier", LogisticRegression(class_weight="balanced", max_iter=2000)),
    ])

    # If there are repeated visits, replace this row split with a patient-group split.
    train, test = train_test_split(
        data, test_size=0.25, random_state=42, stratify=data[TARGET]
    )
    x_train = train[categorical + NUMERIC_COLUMNS]
    x_test = test[categorical + NUMERIC_COLUMNS]
    model.fit(x_train, train[TARGET].astype(int))
    probabilities = model.predict_proba(x_test)[:, 1]
    speed_alert = (test["avg_sec_per_answer"] < 1.5) | (test["avg_sec_per_answer"] > 20)
    predictions = ((probabilities >= args.threshold) | speed_alert).astype(int)
    print(classification_report(test[TARGET], predictions, zero_division=0))
    print("Confusion matrix [ [TN, FP], [FN, TP] ]:\n", confusion_matrix(test[TARGET], predictions))
    if test[TARGET].nunique() == 2:
        print(f"ROC AUC: {roc_auc_score(test[TARGET], probabilities):.3f}")
        print(f"PR AUC:  {average_precision_score(test[TARGET], probabilities):.3f}")

    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "threshold": args.threshold,
                 "speed_alert": {"below_sec_per_answer": 1.5, "above_sec_per_answer": 20.0},
                 "feature_columns": categorical + NUMERIC_COLUMNS}, args.model_out)
    print(f"Saved baseline: {args.model_out}")


if __name__ == "__main__":
    main()
