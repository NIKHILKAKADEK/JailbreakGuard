"""Prompt-free error and overlap analysis for core train/validation only."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys
import unicodedata

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.evaluate_dataset import EvaluationError, _pyarrow_modules, dataset_path
from scanner import RULES, _CONFUSABLES as _SCANNER_CONFUSABLES, _normalize_for_matching, scan_text


_SIGNALS = {
    "override_language": re.compile(r"\b(ignore|disregard|forget|override|replace|set aside|bypass|follow instead)\b", re.I),
    "prior_instruction_reference": re.compile(r"\b(previous|prior|earlier|above|original|initial|system|developer)\b.{0,35}\b(instructions?|rules?|directions?|prompt)\b|\b(instructions?|directions?)\b.{0,35}\b(received|given|earlier|before)\b", re.I | re.S),
    "instruction_noun": re.compile(r"\b(instructions?|rules?|directions?|guidance|system prompt|developer message)\b", re.I),
    "follow_new_request": re.compile(r"\b(follow|obey|use|honor|comply with)\b.{0,35}\b(this|new|current|following)\s+(request|prompt|instructions?|directions?)\b", re.I | re.S),
    "persona_language": re.compile(r"\b(act as|pretend|role.?play|persona|you are now|developer mode|dan mode|unrestricted|uncensored|unfiltered)\b", re.I),
    "prompt_extraction_language": re.compile(r"\b(reveal|repeat|quote|print|show|copy|disclose|recite|dump|verbatim)\b.{0,70}\b(system|developer|hidden|confidential|prompt|instructions?|message|everything above)\b|\b(system prompt|developer message|hidden instructions?)\b", re.I | re.S),
    "untrusted_source_language": re.compile(r"\b(webpage|web page|email|document|search result|tool output|retrieved|quoted|untrusted|attached file)\b", re.I),
    "encoded_payload_marker": re.compile(r"\b(base64|rot.?13|hex(?:adecimal)?|unicode escape|encoded|decode|decrypt)\b", re.I),
    "base64_like_run": re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/])"),
    "token_marker": re.compile(r"(?:<\|[^|]{1,40}\|>|\[/?INST\]|###\s*(?:system|assistant|user))", re.I),
    "punctuation_split_word": re.compile(r"\b[A-Za-z](?:[._-][A-Za-z]){3,}\b"),
}
_CONFUSABLES = set(_SCANNER_CONFUSABLES)
_RULE_IDS = tuple(rule_id for rule_id, *_ in RULES)


class AnalysisError(ValueError):
    """Raised when a core split cannot be safely analyzed."""


def _normalized_overlap_text(text):
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Cf")
    return " ".join(re.findall(r"[\w]+", normalized, flags=re.UNICODE))


def _shingles(normalized, size=3):
    words = normalized.split()
    if len(words) >= size:
        return {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}
    return {normalized} if normalized else set()


def overlapping_train_indices(train_rows, validation_rows, near_threshold=0.85):
    """Return core/train row indices with a normalized exact or near validation match."""
    validation_norm = [_normalized_overlap_text(row.get("text") or "") for row in validation_rows]
    validation_exact = set(validation_norm)
    validation_shingles = [_shingles(text) for text in validation_norm]
    inverted = defaultdict(list)
    for index, shingles in enumerate(validation_shingles):
        for shingle in shingles:
            inverted[shingle].append(index)
    excluded = set()
    for train_index, row in enumerate(train_rows):
        text = _normalized_overlap_text(row.get("text") or "")
        if not text:
            continue
        if text in validation_exact:
            excluded.add(train_index)
            continue
        shingles = _shingles(text)
        candidates = set()
        for shingle in shingles:
            candidates.update(inverted.get(shingle, ()))
        for validation_index in candidates:
            other = validation_shingles[validation_index]
            union = len(shingles | other)
            similarity = len(shingles & other) / union if union else 1.0
            if similarity >= near_threshold:
                excluded.add(train_index)
                break
    return excluded


def overlap_summary(train_rows, validation_rows, near_threshold=0.85):
    """Compare core partitions; return counts only, never examples or hashes."""
    train_texts = [row.get("text") or "" for row in train_rows]
    validation_texts = [row.get("text") or "" for row in validation_rows]
    train_norm = [_normalized_overlap_text(text) for text in train_texts]
    validation_norm = [_normalized_overlap_text(text) for text in validation_texts]
    validation_exact = Counter(validation_norm)
    train_exact = Counter(train_norm)
    exact_pairs = sum(count * validation_exact.get(value, 0) for value, count in train_exact.items())
    shared_exact_texts = sum(1 for value in train_exact if validation_exact.get(value, 0))
    train_labels = defaultdict(set)
    validation_labels = defaultdict(set)
    for row, normalized in zip(train_rows, train_norm):
        train_labels[normalized].add(row.get("label"))
    for row, normalized in zip(validation_rows, validation_norm):
        validation_labels[normalized].add(row.get("label"))
    conflicting_exact_texts = sum(
        1 for value in train_labels.keys() & validation_labels.keys()
        if train_labels[value] != validation_labels[value]
    )
    validation_groups = {str(row["group_id"]) for row in validation_rows if row.get("group_id") not in (None, "")}
    train_groups = {str(row["group_id"]) for row in train_rows if row.get("group_id") not in (None, "")}

    inverted = defaultdict(list)
    validation_shingles = []
    for index, text in enumerate(validation_norm):
        shingles = _shingles(text)
        validation_shingles.append(shingles)
        for shingle in shingles:
            inverted[shingle].append(index)

    near_pairs = set()
    train_with_near = set()
    max_similarity = 0.0
    for train_index, text in enumerate(train_norm):
        shingles = _shingles(text)
        if not shingles:
            continue
        candidates = set()
        for shingle in shingles:
            candidates.update(inverted.get(shingle, ()))
        for validation_index in candidates:
            other = validation_shingles[validation_index]
            union = len(shingles | other)
            similarity = len(shingles & other) / union if union else 1.0
            if similarity >= near_threshold and text != validation_norm[validation_index]:
                near_pairs.add((train_index, validation_index))
                train_with_near.add(train_index)
                max_similarity = max(max_similarity, similarity)

    return {
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "normalized_exact_match_pairs": exact_pairs,
        "shared_normalized_exact_texts": shared_exact_texts,
        "shared_exact_texts_with_label_disagreement": conflicting_exact_texts,
        "shared_group_ids": len(train_groups & validation_groups),
        "near_duplicate_threshold_jaccard": near_threshold,
        "near_duplicate_pairs": len(near_pairs),
        "train_rows_with_near_duplicate": len(train_with_near),
        "maximum_near_duplicate_jaccard": max_similarity,
        "method": "NFKC, case-fold, remove format controls, tokenize alphanumeric words; compare unique 3-word shingle Jaccard candidates.",
    }


def _normalization_features(text):
    normalized_nfkc = unicodedata.normalize("NFKC", text)
    return {
        "nfkc_changes": normalized_nfkc != text,
        "invisible_format_characters": any(unicodedata.category(ch) == "Cf" for ch in text),
        "c0_control_characters": any(unicodedata.category(ch) == "Cc" and ch not in "\t\r\n" for ch in text),
        "common_lookalike_characters": any(ch in _CONFUSABLES for ch in text),
        "unusual_whitespace": any(ch in "\u00a0\u2000\u2001\u2002\u2003\u2009\u202f" for ch in text) or "\t" in text or "\n" in text,
    }


def _transformation_character_names(text):
    """Return small transformation-class counts, never characters or excerpts."""
    counts = Counter()
    for char in text:
        name = unicodedata.name(char, "")
        if unicodedata.category(char) == "Cf":
            counts["invisible_format_character"] += 1
        elif char in _CONFUSABLES:
            counts["mapped_lookalike_character"] += 1
        elif char in "\u00a0\u2000\u2001\u2002\u2003\u2009\u202f":
            counts["nonstandard_space"] += 1
        elif "FULLWIDTH" in name:
            counts["fullwidth_character"] += 1
        elif any(marker in name for marker in ("MATHEMATICAL", "CIRCLED LATIN", "MODIFIER LETTER", "SUPERSCRIPT LATIN", "SUBSCRIPT LATIN", "NEGATIVE SQUARED LATIN")):
            counts["compatibility_alphanumeric"] += 1
        elif unicodedata.category(char).startswith("L") and ord(char) > 127:
            for script in ("CYRILLIC", "GREEK", "ARMENIAN", "CHEROKEE"):
                if script in name:
                    counts[f"letter_script:{script.casefold()}"] += 1
                    counts[f"script_character_name:{name}"] += 1
                    break
    return counts


def analyze_attacks(rows, scanner=scan_text):
    """Summarize attack outcomes, rule signals and obfuscation cues by category."""
    categories = defaultdict(lambda: {
        "attacks": 0, "detected": 0, "missed": 0,
        "matched_rule_counts": Counter(), "missed_signal_counts": Counter(),
        "missed_normalization_counts": Counter(),
        "missed_character_name_counts": Counter(),
    })
    overall_misses = Counter()
    overall_detected_signals = Counter()
    for row in rows:
        if row.get("label") != 1:
            continue
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        category = str(row.get("category") or "uncategorized")
        stats = categories[category]
        stats["attacks"] += 1
        result = scanner(text)
        if result.get("level") == "warning":
            stats["detected"] += 1
            for finding in result.get("evidence", []):
                rule_id = finding.get("rule")
                if rule_id in _RULE_IDS:
                    stats["matched_rule_counts"][rule_id] += 1
                    overall_detected_signals[rule_id] += 1
            continue
        stats["missed"] += 1
        normalized_text = _normalize_for_matching(text)
        present_signals = {name: bool(pattern.search(normalized_text)) for name, pattern in _SIGNALS.items()}
        for name, present in present_signals.items():
            if present:
                stats["missed_signal_counts"][name] += 1
                overall_misses[name] += 1
        for combination, required in {
            "override_plus_prior_reference": ("override_language", "prior_instruction_reference"),
            "override_plus_instruction_noun": ("override_language", "instruction_noun"),
            "override_plus_new_request": ("override_language", "follow_new_request"),
            "encoded_marker_plus_instruction_noun": ("encoded_payload_marker", "instruction_noun"),
            "token_marker_plus_override": ("token_marker", "override_language"),
            "encoded_marker_plus_new_request": ("encoded_payload_marker", "follow_new_request"),
        }.items():
            if all(present_signals.get(name, False) for name in required):
                stats["missed_signal_counts"][combination] += 1
                overall_misses[combination] += 1
        for name, present in _normalization_features(text).items():
            if present:
                stats["missed_normalization_counts"][name] += 1
                overall_misses[f"normalization:{name}"] += 1
        stats["missed_character_name_counts"].update(_transformation_character_names(text))

    output = {}
    for category, stats in sorted(categories.items()):
        missed_cues = stats["missed_signal_counts"]
        normalization = stats["missed_normalization_counts"]
        suggestions = []
        if missed_cues["override_language"] or missed_cues["prior_instruction_reference"]:
            suggestions.append("Test contextual combinations of an override verb, prior-instruction reference, and a new-request directive; preserve benign explanation cases.")
        if normalization["unusual_whitespace"] or normalization["nfkc_changes"] or normalization["common_lookalike_characters"]:
            suggestions.append("Normalize whitespace and common compatibility/confusable characters before matching; test mixed-script benign text.")
        if missed_cues["encoded_payload_marker"] or missed_cues["base64_like_run"]:
            suggestions.append("Flag explicit decode-then-follow requests and encoded payload markers in context; do not decode or execute payloads.")
        if missed_cues["token_marker"]:
            suggestions.append("Combine token-like control markers with nearby instruction override or extraction language.")
        if missed_cues["persona_language"]:
            suggestions.append("Broaden only bypass-oriented role changes; ordinary task personas should remain clear.")
        if missed_cues["prompt_extraction_language"]:
            suggestions.append("Expand high-confidence extraction verbs and targets such as original/initial prompts and prior messages.")
        if missed_cues["untrusted_source_language"]:
            suggestions.append("Pair untrusted-source references with commands to follow that source over system/developer guidance.")
        if not suggestions:
            suggestions.append("No narrow lexical signal recurred enough to justify a new rule; collect additional validation evidence first.")
        output[category] = {
            "attacks": stats["attacks"],
            "detected": stats["detected"],
            "missed": stats["missed"],
            "matched_rule_counts": dict(sorted(stats["matched_rule_counts"].items())),
            "missed_signal_counts": dict(sorted(stats["missed_signal_counts"].items())),
            "missed_normalization_counts": dict(sorted(stats["missed_normalization_counts"].items())),
            "missed_character_name_counts": dict(sorted(stats["missed_character_name_counts"].items())),
            "suggested_improvements": suggestions,
        }
    return {
        "categories": output,
        "overall_missed_signal_counts": dict(sorted(overall_misses.items())),
        "overall_detected_rule_counts": dict(sorted(overall_detected_signals.items())),
        "privacy": "Counts only; prompt text and excerpts are not included.",
    }


def analyze_false_positives(rows, scanner=scan_text):
    """Count benign predictions that triggered rules, without retaining text."""
    false_positives = 0
    rules = Counter()
    categories = Counter()
    for row in rows:
        if row.get("label") != 0:
            continue
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        result = scanner(text)
        if result.get("level") != "warning":
            continue
        false_positives += 1
        categories[str(row.get("category") or "uncategorized")] += 1
        rules.update(item.get("rule", "unknown") for item in result.get("evidence", []))
    return {
        "false_positive_examples": false_positives,
        "by_category": dict(sorted(categories.items())),
        "matched_rule_counts": dict(sorted(rules.items())),
    }


def load_core_split(split):
    if split not in {"train", "validation"}:
        raise AnalysisError("This analysis only reads core/train and core/validation.")
    path = dataset_path(PROJECT_ROOT / "test_samples" / "text", "core", split)
    try:
        parquet = _pyarrow_modules()
        table = parquet.read_table(path, columns=["text", "label", "category", "group_id"])
    except Exception as error:
        raise AnalysisError(f"Unable to read core/{split} Parquet split: {type(error).__name__}") from error
    return table.to_pylist()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "reports" / "core_train_error_analysis.json")
    args = parser.parse_args(argv)
    try:
        train = load_core_split("train")
        validation = load_core_split("validation")
        report = {
            "dataset": "core",
            "split_analyzed_for_false_negatives": "train",
            "false_negative_analysis": analyze_attacks(train),
            "validation_attack_analysis": analyze_attacks(validation),
            "validation_false_positive_analysis": analyze_false_positives(validation),
            "train_validation_overlap": overlap_summary(train, validation),
            "limitations": "Category labels are descriptive; numeric label is the binary ground truth. Analysis identifies patterns for validation, not proof of protection.",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"report_path": str(args.output), **report}, indent=2, ensure_ascii=False))
        return 0
    except (AnalysisError, EvaluationError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
