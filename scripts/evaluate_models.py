"""Local rules, TF-IDF/logistic-regression, and hybrid validation experiment.

Only core/train is used to fit models and core/validation is used for selection.
Reports contain aggregate counts and metrics only; no vectorizer or model is saved.
"""

import argparse
from pathlib import Path
import json
import sys

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion
from sklearn.linear_model import LogisticRegression

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyze_rules import load_core_split, overlapping_train_indices
from scripts.evaluate_dataset import calculate_metrics
from scanner import scan_text


def _category_metrics(rows, predictions):
    categories = {}
    for category in sorted({str(row.get("category") or "uncategorized") for row in rows}):
        selected = [index for index, row in enumerate(rows) if str(row.get("category") or "uncategorized") == category]
        categories[category] = calculate_metrics(
            [rows[index]["label"] for index in selected],
            [predictions[index] for index in selected],
        )
    return categories


def summarize(rows, predictions):
    return {
        **calculate_metrics([row["label"] for row in rows], predictions),
        "per_category": _category_metrics(rows, predictions),
    }


def make_vectorizer():
    return FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=60000, sublinear_tf=True)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, max_features=100000, sublinear_tf=True)),
    ])


def fit_and_score(train_rows, validation_rows, c=1.0, class_weight=None):
    """Fit one local model; return validation scores and the unsaved settings."""
    train_texts = [row["text"] for row in train_rows]
    validation_texts = [row["text"] for row in validation_rows]
    y_train = [row["label"] for row in train_rows]
    vectorizer = make_vectorizer()
    x_train = vectorizer.fit_transform(train_texts)
    x_validation = vectorizer.transform(validation_texts)
    model = LogisticRegression(C=c, class_weight=class_weight, max_iter=1000, solver="liblinear", random_state=0)
    model.fit(x_train, y_train)
    scores = model.predict_proba(x_validation)[:, 1].tolist()
    return scores


def select_candidate(candidates, min_precision=0.90, max_fpr=0.05):
    """Choose by F1, then recall/precision, preferring low FPR; enforce constraints when possible."""
    eligible = [candidate for candidate in candidates
                if candidate["metrics"]["precision"] >= min_precision
                and candidate["metrics"]["false_positive_rate"] <= max_fpr]
    pool = eligible or candidates
    if not pool:
        raise ValueError("No validation candidates were produced.")
    selected = max(pool, key=lambda candidate: (
        candidate["metrics"]["f1"], candidate["metrics"]["recall"],
        candidate["metrics"]["precision"], -candidate["metrics"]["false_positive_rate"],
        candidate.get("threshold", 1.0),
    ))
    return {**selected, "selection_constraints_met": bool(eligible)}


def _candidate_rows(validation_rows, scores, rule_predictions, approach, model_config):
    candidates = []
    for threshold_step in range(5, 96, 5):
        threshold = threshold_step / 100
        ml_predictions = [int(score >= threshold) for score in scores]
        predictions = ml_predictions if approach == "ml_only" else [
            int(rule_prediction or ml_prediction)
            for rule_prediction, ml_prediction in zip(rule_predictions, ml_predictions)
        ]
        candidates.append({
            "approach": approach,
            "model": model_config,
            "threshold": threshold,
            "metrics": summarize(validation_rows, predictions),
        })
    return candidates


def run_experiment(train_rows, validation_rows):
    if not train_rows or not validation_rows:
        raise ValueError("Training and validation data must both contain rows.")
    y_train = {row.get("label") for row in train_rows}
    y_validation = {row.get("label") for row in validation_rows}
    if y_train != {0, 1} or not y_validation.issubset({0, 1}):
        raise ValueError("Expected binary labels 0/1 in core data.")

    overlapping_indices = overlapping_train_indices(train_rows, validation_rows)
    train_rows = [row for index, row in enumerate(train_rows) if index not in overlapping_indices]
    if {row.get("label") for row in train_rows} != {0, 1}:
        raise ValueError("Both binary classes must remain after removing normalized train/validation overlaps.")

    rule_predictions = [int(scan_text(row["text"])["level"] == "warning") for row in validation_rows]
    rule_metrics = summarize(validation_rows, rule_predictions)
    best_ml = None
    best_hybrid = None
    tested_configurations = []
    for c in (0.1, 1.0, 10.0):
        for class_weight in (None, "balanced"):
            config = {"features": "word(1,2)+char_wb(3,5) TF-IDF", "C": c, "class_weight": class_weight}
            scores = fit_and_score(train_rows, validation_rows, c=c, class_weight=class_weight)
            tested_configurations.append(config)
            for approach in ("ml_only", "hybrid_or_rules"):
                candidates = _candidate_rows(validation_rows, scores, rule_predictions, approach, config)
                selected = select_candidate(candidates)
                current = best_ml if approach == "ml_only" else best_hybrid
                if current is None or (
                    selected["metrics"]["f1"], selected["metrics"]["recall"],
                    selected["metrics"]["precision"], -selected["metrics"]["false_positive_rate"]
                ) > (
                    current["metrics"]["f1"], current["metrics"]["recall"],
                    current["metrics"]["precision"], -current["metrics"]["false_positive_rate"]
                ):
                    if approach == "ml_only":
                        best_ml = selected
                    else:
                        best_hybrid = selected

    return {
        "dataset": "core",
        "fit_split": "train",
        "selection_split": "validation",
        "training_examples_before_overlap_filter": len(train_rows) + len(overlapping_indices),
        "training_examples_after_overlap_filter": len(train_rows),
        "training_examples_excluded_for_validation_overlap": len(overlapping_indices),
        "validation_examples": len(validation_rows),
        "selection_policy": {
            "thresholds": "0.05 through 0.95 in 0.05 increments",
            "preferred_constraints": {"precision_at_least": 0.90, "false_positive_rate_at_most": 0.05},
            "ranking": "F1, recall, precision, then lower false-positive rate; if no candidate meets constraints, rank all candidates and mark constraints unmet.",
        },
        "model_configurations_tested": tested_configurations,
        "validation": {
            "rules_only": {"threshold": None, "metrics": rule_metrics},
            "ml_only_selected": best_ml,
            "hybrid_or_rules_selected": best_hybrid,
        },
        "privacy": "Prompts remain in memory; report contains no prompt text, excerpts, vectorizer vocabulary, or fitted model artifact.",
        "limitations": "Validation metrics guide model selection and may be optimistic; this dataset does not establish real-world protection.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "reports" / "core_validation_models.json")
    args = parser.parse_args(argv)
    try:
        train_rows = load_core_split("train")
        validation_rows = load_core_split("validation")
        report = run_experiment(train_rows, validation_rows)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"report_path": str(args.output), **report}, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        parser.error(f"Experiment failed ({type(error).__name__}).")


if __name__ == "__main__":
    raise SystemExit(main())
