"""Features and deterministic sampling score for synthetic response validity."""
from __future__ import annotations

import numpy as np
import pandas as pd

QUESTION_COLUMNS = [f"BDI{i:02d}" for i in range(1, 6)] + [f"STAI{i:02d}" for i in range(1, 21)]
BASE_NUMERIC = ["avg_sec_per_answer", "duration_sec", "back_navigation_count",
                "consistent_answers_count"]
MODEL_NUMERIC = [*BASE_NUMERIC, "option_entropy", "unique_answer_count",
                 "adjacent_repeat_count", "speed_out_of_range"]


def add_reliability_features(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy()
    answers = result[QUESTION_COLUMNS].astype(float)
    vals = answers.to_numpy()
    repeats = (vals[:, 1:] == vals[:, :-1]).sum(axis=1)
    unique = np.array([len(np.unique(row)) for row in vals])
    entropies = []
    for row in vals:
        counts = np.bincount(row.astype(int), minlength=4).astype(float)
        probs = counts[counts > 0] / len(row)
        entropies.append(float(-(probs * np.log(probs)).sum() / np.log(4)))
    result["option_entropy"] = entropies
    result["unique_answer_count"] = unique
    result["adjacent_repeat_count"] = repeats
    result["speed_out_of_range"] = (
        (result["avg_sec_per_answer"] < 1.5) |
        (result["avg_sec_per_answer"] > 20)
    ).astype(int)
    return result


def suspiciousness_score(data: pd.DataFrame) -> np.ndarray:
    # Synthetic heuristic for selecting unusual response patterns. This is not
    # a validated measure of deception or clinical test validity.
    return (4.0 * data["speed_out_of_range"].to_numpy()
            + 0.07 * data["adjacent_repeat_count"].to_numpy()
            + 0.25 * (4 - data["unique_answer_count"].to_numpy())
            + (1.0 - data["option_entropy"].to_numpy()))
