"""Offline evaluation of the existing JailbreakGuard scanner on local Parquet data.

Only aggregate counts and metrics are written to reports. Prompt text remains in
memory and is never included in output files.
"""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scanner import scan_text


DATA_FILES = {
    "core": {"train", "validation", "test"},
    "full": {"train", "validation", "test"},
    "data": {"train"},
}


class EvaluationError(ValueError):
    """Raised for invalid datasets, labels, or scanner results."""


def map_label(label):
    """Map verified binary dataset labels to attack positive / benign negative."""
    if isinstance(label, bool):
        return int(label)
    if isinstance(label, int) and label in (0, 1):
        return label
    if isinstance(label, float) and label in (0.0, 1.0):
        return int(label)
    if isinstance(label, str):
        value = label.strip().casefold()
        if value in {"0", "benign", "safe", "normal", "non-malicious", "non_malicious"}:
            return 0
        if value in {"1", "attack", "malicious", "jailbreak", "prompt-injection", "prompt_injection"}:
            return 1
    raise EvaluationError(f"Unrecognized label {label!r}; expected a verified benign/attack label or 0/1.")


def map_prediction(result):
    """Map the production scanner's documented level field to a binary label."""
    if not isinstance(result, dict) or result.get("level") not in {"clear", "warning"}:
        raise EvaluationError("scan_text() returned an unexpected result schema; expected level 'clear' or 'warning'.")
    return int(result["level"] == "warning")


def calculate_metrics(y_true, y_pred):
    """Calculate attack-positive binary metrics, returning zero on zero denominators."""
    if len(y_true) != len(y_pred):
        raise EvaluationError("Truth and prediction arrays must have the same length.")
    tn = fp = fn = tp = 0
    for actual, predicted in zip(y_true, y_pred):
        actual = map_label(actual)
        if predicted not in (0, 1):
            raise EvaluationError("Predictions must be binary integers 0 or 1.")
        if actual == 0 and predicted == 0:
            tn += 1
        elif actual == 0 and predicted == 1:
            fp += 1
        elif actual == 1 and predicted == 0:
            fn += 1
        else:
            tp += 1

    def ratio(numerator, denominator):
        return numerator / denominator if denominator else 0.0

    total = tn + fp + fn + tp
    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)
    return {
        "examples": total,
        "accuracy": ratio(tp + tn, total),
        "precision": precision,
        "recall": recall,
        "f1": ratio(2 * precision * recall, precision + recall),
        "false_positive_rate": ratio(fp, fp + tn),
        "false_negative_rate": ratio(fn, fn + tp),
        "confusion_matrix": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
    }


def deterministic_indices(row_count, max_samples, seed):
    if not isinstance(row_count, int) or row_count < 0:
        raise EvaluationError("row_count must be a non-negative integer.")
    if max_samples is not None and (not isinstance(max_samples, int) or max_samples < 1):
        raise EvaluationError("max_samples must be a positive integer.")
    if not isinstance(seed, int):
        raise EvaluationError("seed must be an integer.")
    count = min(row_count, max_samples) if max_samples is not None else row_count
    # Hash ranking is stable across Python versions and never depends on prompt text.
    return sorted(range(row_count), key=lambda index: sha256(f"{seed}:{index}".encode()).digest())[:count]


def _row_text(row):
    if row.get("text") is not None:
        return row.get("text")
    return row.get("prompt")


def evaluate_rows(rows, max_samples=None, seed=0, scanner=scan_text):
    """Evaluate in-memory row dictionaries without retaining text in the summary."""
    rows = list(rows)
    indices = deterministic_indices(len(rows), max_samples, seed)
    y_true = []
    y_pred = []
    categories = []
    raw_labels = Counter()
    mapped_labels = Counter()
    skipped = 0

    for index in indices:
        row = rows[index]
        text = _row_text(row)
        label = row.get("label")
        if not isinstance(text, str) or not text.strip() or label is None:
            skipped += 1
            continue
        actual = map_label(label)
        raw_labels[str(label)] += 1
        mapped_labels[actual] += 1
        prediction = map_prediction(scanner(text))
        category = row.get("category", row.get("attack_type"))
        categories.append(str(category) if category not in (None, "") else "uncategorized")
        y_true.append(actual)
        y_pred.append(prediction)

    per_category = {}
    for category in sorted(set(categories)):
        selected = [i for i, value in enumerate(categories) if value == category]
        per_category[category] = calculate_metrics(
            [y_true[i] for i in selected], [y_pred[i] for i in selected]
        )

    return {
        **calculate_metrics(y_true, y_pred),
        "rows_selected": len(indices),
        "skipped_examples": skipped,
        "label_distribution": {"benign": mapped_labels[0], "attack": mapped_labels[1]},
        "raw_label_counts": dict(sorted(raw_labels.items())),
        "per_category": per_category,
    }


def _pyarrow_modules():
    try:
        import pyarrow.parquet as parquet
    except ImportError as error:
        raise EvaluationError("Parquet evaluation requires PyArrow. Install requirements.txt first.") from error
    return parquet


def dataset_path(data_root, dataset, split):
    if dataset not in DATA_FILES:
        raise EvaluationError(f"Unknown dataset {dataset!r}; choose core, full, or data.")
    if split not in DATA_FILES[dataset]:
        raise EvaluationError(f"Dataset {dataset!r} does not define a {split!r} split.")
    path = Path(data_root) / dataset / f"{split}-00000-of-00001.parquet"
    if not path.is_file():
        raise EvaluationError(f"Dataset file not found: {path}")
    return path


def load_rows(path):
    parquet = _pyarrow_modules()
    try:
        table = parquet.read_table(path)
    except Exception as error:
        raise EvaluationError(f"Could not read local Parquet file {path}: {error}") from error
    rows = table.to_pylist()
    if not rows:
        raise EvaluationError(f"Parquet dataset has no rows: {path}")
    fields = set(table.column_names)
    text_field = "text" if "text" in fields else "prompt" if "prompt" in fields else None
    if text_field is None or "label" not in fields:
        raise EvaluationError(f"Expected a text/prompt field and label field; found columns {sorted(fields)}.")
    return rows, table.column_names


def inspect_local_dataset(data_root):
    """Inspect all local Parquet parts for actual labels and overlap, without text output."""
    parquet = _pyarrow_modules()
    root = Path(data_root)
    files = sorted(root.glob("*/*.parquet"))
    if not files:
        raise EvaluationError(f"No Parquet files found under {root}.")
    partitions = {}
    file_hash_groups = {}
    for path in files:
        try:
            table = parquet.read_table(path)
        except Exception as error:
            raise EvaluationError(f"Could not inspect {path}: {error}") from error
        rows = table.to_pylist()
        fields = set(table.column_names)
        text_field = "text" if "text" in fields else "prompt" if "prompt" in fields else None
        labels = Counter(str(row.get("label")) for row in rows)
        categories_field = "category" if "category" in fields else "attack_type" if "attack_type" in fields else None
        categories = Counter(str(row.get(categories_field)) for row in rows) if categories_field else Counter()
        text_hashes = set()
        group_ids = set()
        if text_field:
            for row in rows:
                text = row.get(text_field)
                if isinstance(text, str):
                    text_hashes.add(sha256(text.encode("utf-8")).hexdigest())
                if row.get("group_id") not in (None, ""):
                    group_ids.add(str(row["group_id"]))
        partition = f"{path.parent.name}/{path.stem.split('-')[0]}"
        partitions[partition] = {
            "path": str(path), "rows": len(rows), "columns": table.column_names,
            "labels": dict(sorted(labels.items())),
            "categories": dict(sorted(categories.items())),
            "unique_text_hashes": len(text_hashes), "unique_group_ids": len(group_ids),
            "_text_hashes": text_hashes, "_group_ids": group_ids,
        }
        file_hash_groups.setdefault(sha256(path.read_bytes()).hexdigest(), []).append(partition)

    overlap = []
    names = sorted(partitions)
    for i, left_name in enumerate(names):
        for right_name in names[i + 1:]:
            left, right = partitions[left_name], partitions[right_name]
            text_overlap = len(left["_text_hashes"] & right["_text_hashes"])
            group_overlap = len(left["_group_ids"] & right["_group_ids"])
            if text_overlap or group_overlap:
                overlap.append({"left": left_name, "right": right_name,
                                "shared_unique_text_hashes": text_overlap,
                                "shared_group_ids": group_overlap})
    for partition in partitions.values():
        partition.pop("_text_hashes")
        partition.pop("_group_ids")
    return {
        "files": list(partitions.values()),
        "exactly_identical_parquet_partitions": [group for group in file_hash_groups.values() if len(group) > 1],
        "row_level_overlap": overlap,
    }


def _write_json(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=sorted(DATA_FILES), default="core")
    parser.add_argument("--split", choices=("train", "validation", "test"), default="test")
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "test_samples" / "text")
    parser.add_argument("--max-samples", type=int, help="deterministically evaluate at most this many rows")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, help="JSON metrics report path (defaults to reports/)")
    parser.add_argument("--inspect", action="store_true", help="inspect local label distributions and partition overlap; do not scan prompts")
    args = parser.parse_args(argv)
    try:
        if args.inspect:
            print(json.dumps(inspect_local_dataset(args.data_root), indent=2, ensure_ascii=False))
            return 0
        path = dataset_path(args.data_root, args.dataset, args.split)
        rows, columns = load_rows(path)
        counts = evaluate_rows(rows, args.max_samples, args.seed)
        report = {
            "dataset": args.dataset, "split": args.split, "source_file": str(path),
            "source_rows": len(rows), "columns": columns, "seed": args.seed,
            "sample_limit": args.max_samples, "metrics": counts,
            "limitations": "Rule-based benchmark results are indicators of performance on this dataset, not proof of protection against all prompt injections or jailbreaks.",
        }
        out = args.output or PROJECT_ROOT / "reports" / f"{args.dataset}_{args.split}_metrics.json"
        _write_json(out, report)
        print(json.dumps({"report_path": str(out), **report}, indent=2, ensure_ascii=False))
        return 0
    except EvaluationError as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
