"""Train a synthetic response-validity prototype, separate from attention model."""
from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, classification_report,
                             confusion_matrix, precision_score, recall_score)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from reliability_features import (MODEL_NUMERIC, QUESTION_COLUMNS,
                                  add_reliability_features)


ROOT = Path(__file__).resolve().parents[1]
TARGET = "is_unreliable"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path,
                        default=ROOT / "data/raw/synthetic_reliability_labeled.csv")
    parser.add_argument("--model-out", type=Path,
                        default=ROOT / "models/synthetic_reliability.joblib")
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    data = add_reliability_features(pd.read_csv(args.data))
    feature_columns = QUESTION_COLUMNS + MODEL_NUMERIC
    required = feature_columns + [TARGET, "previous_attention_class"]
    missing = sorted(set(required) - set(data.columns))
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    if data[TARGET].nunique() != 2:
        raise ValueError("Need examples from both reliability classes")
    overlap = data.loc[data[TARGET] == 1, "previous_attention_class"].mean()
    print(f"P(previous attention class=1 | is_unreliable=1): {overlap:.2%}")

    preprocessing = ColumnTransformer([
        ("answers", Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]), QUESTION_COLUMNS),
        ("numeric", Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]), MODEL_NUMERIC),
    ])
    model = Pipeline([
        ("features", preprocessing),
        ("classifier", LogisticRegression(class_weight="balanced", max_iter=2000)),
    ])
    train, test = train_test_split(data, test_size=0.25, random_state=42,
                                   stratify=data[TARGET])
    model.fit(train[feature_columns], train[TARGET].astype(int))
    probabilities = model.predict_proba(test[feature_columns])[:, 1]
    predictions = (probabilities >= args.threshold).astype(int)
    y = test[TARGET].astype(int)
    print(classification_report(y, predictions, zero_division=0))
    print("Confusion matrix [ [TN, FP], [FN, TP] ]:\n", confusion_matrix(y, predictions))
    print(f"Recall unreliable: {recall_score(y, predictions):.3f}")
    print(f"Accuracy: {accuracy_score(y, predictions):.3f}")
    print(f"Precision unreliable: {precision_score(y, predictions, zero_division=0):.3f}")

    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "threshold": args.threshold,
                 "feature_columns": feature_columns,
                 "target": TARGET,
                 "meaning": "synthetic response-pattern anomaly label; not clinical truth"},
                args.model_out)
    print(f"Saved reliability model: {args.model_out}")


if __name__ == "__main__":
    main()
