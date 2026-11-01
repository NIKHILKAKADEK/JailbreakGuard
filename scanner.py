"""Local, deterministic rules for spotting prompt attack indicators."""

import re
import unicodedata


# A small set of visually similar characters and invisible format controls often
# used to hide otherwise ordinary directive words. This is intentionally not a
# general-purpose decoder: encoded payloads still need human or specialized review.
_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y",
    "х": "x", "і": "i", "ј": "j", "к": "k", "м": "m", "т": "t",
    "в": "b", "н": "h", "г": "r", "л": "l", "п": "n", "д": "d",
    "б": "b", "ь": "b", "ѕ": "s", "ԁ": "d", "ӏ": "l", "й": "i",
    "и": "u", "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
    "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y",
    "Х": "X", "І": "I", "Ј": "J",
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ο": "o",
    "ρ": "p", "τ": "t", "χ": "x", "υ": "y", "ν": "v",
    "Α": "A", "Β": "B", "Ε": "E",
    "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P",
    "Τ": "T", "Χ": "X", "Υ": "Y",
})


def _normalize_for_matching(text: str) -> str:
    """Normalize common compatibility, invisible, and lookalike characters."""
    normalized = unicodedata.normalize("NFKC", text)
    latin_letters = sum(char.isascii() and char.isalpha() for char in normalized)
    mapped_lookalikes = sum(char in _CONFUSABLES for char in normalized)
    # Avoid transliterating ordinary non-English text. Mixed English with a
    # limited number of ASCII-lookalike characters is normalized for matching.
    if latin_letters >= max(2, mapped_lookalikes):
        normalized = normalized.translate(_CONFUSABLES)
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Cf")
    return re.sub(r"\s+", " ", normalized).strip()


def _matching_views(text: str) -> tuple[str, ...]:
    """Return canonical text plus a conservative punctuation-separated view."""
    normalized = _normalize_for_matching(text)
    punctuation_separated = re.sub(r"(?<=[A-Za-z])[._-]+(?=[A-Za-z])", " ", normalized)
    return (normalized,) if punctuation_separated == normalized else (normalized, punctuation_separated)


# Rules focus on imperative or directive language, so academic discussion of the
# topic (for example, "What is prompt injection?") is not flagged by itself.
RULES = (
    (
        "instruction_override", "Instruction override",
        re.compile(
            r"\b(?:set aside|disregard|replace|ignore|override|forget)\b"
            r".{0,55}\b(?:instructions?|directions?|rules?|guidance)\b"
            r".{0,50}\b(?:you\s+(?:received|were given|were told)|provided to you)\b"
            r".{0,25}\b(?:earlier|before|previously)\b.{0,80}\b"
            r"(?:follow|obey|use|honor)\b.{0,25}\b(?:this|the|my|new|current)\s+"
            r"(?:request|prompt|instructions?|directions?)\b"
            r"|\b(?:ignore|disregard|forget|override|bypass|set aside|"
            r"stop following|do not follow)\b\s+(?:(?:all|any|the)\s+)?"
            r"(?:of\s+)?(?:your\s+|the\s+)?(?:previous|prior|above|earlier|"
            r"preceding|original|initial|system|developer|given earlier)\s+"
            r"(?:instructions?|rules?|directions?|guidance|constraints?)\b"
            r"|\b(?:ignore|disregard|forget|override)\b\s+(?:all\s+)?"
            r"(?:previous|prior|above|earlier)\s+(?:instructions?|rules?)\b"
            r"|\b(?:ignore|disregard|forget)\s+(?:all\s+)?"
            r"(?:previous|prior|above)\b",
            re.I | re.S,
        ),
        "Directs the assistant to ignore earlier instructions.",
    ),
    (
        "hidden_prompt_request", "Hidden prompt disclosure",
        re.compile(
            r"\b(?:reveal|show|print|disclose|repeat|output|tell me|quote|"
            r"recite|reproduce|copy|dump|summari[sz]e|provide|list|describe|"
            r"read back|transcribe)\b.{0,90}\b(?:"
            r"system\s+(?:prompt|message|instructions?)|hidden\s+instructions?|"
            r"developer\s+(?:message|instructions?)|confidential\s+instructions?|"
            r"internal\s+configuration|secret\s+instructions?|"
            r"(?:initial|original|starting)\s+prompt|"
            r"instructions?\s+(?:you were given|above|before this))\b"
            r"|\bwhat\s+(?:are|is|were)\s+(?:your|the)\s+(?:system\s+prompt|"
            r"hidden\s+instructions?|initial\s+instructions?)\b"
            r"|\b(?:repeat|quote|recite|copy)\b.{0,45}\b(?:everything|"
            r"all the text|the message)\b.{0,35}\b(?:above|before)\b",
            re.I | re.S,
        ),
        "Requests hidden or confidential instructions or internal configuration.",
    ),
    (
        "role_manipulation", "Role or identity manipulation",
        re.compile(
            r"\b(?:you are now|from now on,? you are|pretend to be|pretend you are|"
            r"act as|roleplay as|role-play as|become|switch to|adopt the persona of)"
            r"\b.{0,90}\b(?:unrestricted|unfiltered|uncensored|unbound|"
            r"different ai|another assistant|no restrictions|no rules|"
            r"without (?:any )?rules|without limitations|never refuse|"
            r"dan(?:\s+mode)?|developer mode|evil twin)\b"
            r"|\b(?:you are now|from now on,? you are)\s+(?:dan|a\s+"
            r"(?:different|new|second)\s+(?:ai|assistant))\b",
            re.I | re.S,
        ),
        "Attempts to assign an unrestricted or alternate assistant identity.",
    ),
    (
        "safety_evasion", "Safety safeguard evasion",
        re.compile(
            r"\b(?:bypass|disable|turn off|remove|ignore|evade|work around)"
            r"\b.{0,60}\b(?:safety\s+(?:policy|policies|filters?|rules?|checks?)|"
            r"safeguards?|content\s+restrictions?|guardrails?|refusal\s+rules?)\b"
            r"|\b(?:do anything now|dan(?:\s+mode)?|developer mode|"
            r"god mode|no-refusal mode)\b"
            r"|\b(?:without|free of)\s+(?:your\s+)?(?:safety rules|"
            r"restrictions|guardrails|limitations)\b"
            r"|\bnever refuse\b",
            re.I | re.S,
        ),
        "Asks to bypass or disable safety policies or safeguards.",
    ),
    (
        "untrusted_content_injection", "Embedded prompt injection",
        re.compile(
            r"\b(?:quoted text|(?:the\s+)?(?:following\s+)?(?:quote|document|"
            r"webpage|web page|email|message|file|tool output|search result|text|content))"
            r"\b.{0,140}\b(?:ignore|disregard|override|bypass|forget|do not follow)"
            r"\b.{0,90}\b(?:instructions?|system prompt|rules?|safeguards?)\b"
            r"|\b(?:ignore|disregard|override|bypass)\s+(?:all\s+)?"
            r"(?:previous\s+)?instructions?\b.{0,90}\b(?:quoted|untrusted|"
            r"document|webpage|web page|email|message|file|tool output|search result)\b"
            r"|\b(?:follow|obey|execute)\b.{0,45}\b(?:instructions?|commands?)"
            r"\b.{0,40}\b(?:in|from)\s+(?:the\s+)?(?:webpage|web page|"
            r"document|email|file|tool output|search result)\b",
            re.I | re.S,
        ),
        "Contains a directive in quoted or untrusted content that targets assistant instructions.",
    ),
    (
        "priority_escalation", "Untrusted instruction priority escalation",
        re.compile(
            r"\b(?:treat|consider|follow|obey|use|prioriti[sz]e)\b.{0,85}\b"
            r"(?:my|this|the following|user[ -]provided|attacker[ -]provided|"
            r"webpage|document|tool output)\s+(?:text|prompt|instructions?|message|content)"
            r"\b.{0,85}\b(?:higher priority|higher-priority|"
            r"above (?:all|the)|override|authoritative|instead of your instructions)\b"
            r"|\b(?:as|like)\s+(?:the\s+)?system instructions?\b"
            r"|\b(?:make|set|treat)\b.{0,55}\b(?:this|my|the following)\s+"
            r"(?:text|prompt|instructions?|message|content)\b.{0,55}\b"
            r"(?:system level|highest priority|higher priority|override your rules)\b"
            r"|\b(?:obey|follow)\s+(?:the\s+)?(?:webpage|document|tool output)"
            r"\s+(?:instead of|over)\s+(?:your\s+)?(?:system|developer)\s+instructions\b",
            re.I | re.S,
        ),
        "Attempts to give user supplied text higher priority than its normal role.",
    ),
    (
        "obfuscated_directive", "Obfuscated instruction indicator",
        re.compile(
            r"\bi[\W_]*g[\W_]*n[\W_]*o[\W_]*r[\W_]*e\b.{0,55}\b"
            r"(?:previous|prior|earlier|system|developer)\s+(?:instructions?|rules?)\b"
            r"|\bd[\W_]*i[\W_]*s[\W_]*r[\W_]*e[\W_]*g[\W_]*a[\W_]*r[\W_]*d\b"
            r".{0,55}\b(?:previous|prior|system|developer)\s+(?:instructions?|rules?)\b",
            re.I | re.S,
        ),
        "Contains a common instruction override with separators or lookalike characters.",
    ),
    (
        "encoded_instruction_request", "Encoded instruction handling",
        re.compile(
            r"\b(?:decode|decrypt|translate)\b.{0,80}\b(?:base64|rot.?13|"
            r"hex(?:adecimal)?|encoded payload|encoded text)\b.{0,80}\b"
            r"(?:then|and then|after that)\s+(?:follow|obey|execute|apply)\b"
            r".{0,55}\b(?:instructions?|commands?|prompt|decoded text)\b",
            re.I | re.S,
        ),
        "Asks to decode an encoded payload and then follow or execute its instructions.",
    ),
    (
        "token_wrapped_instruction", "Token-wrapped instruction indicator",
        re.compile(
            r"(?:(?:<\|[^|]{1,40}\|>|\[/?INST\]|<<\/?SYS>>|###\s*(?:system|assistant|user))"
            r".{0,160}\b(?:ignore|disregard|override|bypass|disable|reveal|follow|obey)"
            r"\b.{0,70}\b(?:instructions?|system prompt|safeguards?|rules?)\b"
            r"|\b(?:ignore|disregard|override|bypass|disable|reveal|follow|obey)"
            r"\b.{0,70}\b(?:instructions?|system prompt|safeguards?|rules?)\b"
            r".{0,160}(?:<\|[^|]{1,40}\|>|\[/?INST\]|<<\/?SYS>>|###\s*(?:system|assistant|user)))",
            re.I | re.S,
        ),
        "Combines token-like control markers with a directive targeting assistant instructions.",
    ),
)


def scan_text(text: str) -> dict:
    """Return distinct rule indicators without storing or logging submitted text."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Enter a prompt before scanning.")

    match_texts = _matching_views(text)
    # One finding per rule even if the same indicator appears several times.
    matches = []
    for rule, category, pattern, explanation in RULES:
        if any(pattern.search(match_text) for match_text in match_texts):
            matches.append({"rule": rule, "category": category, "evidence": explanation})

    if matches:
        return {
            "status": "Potential indicators found",
            "level": "warning",
            "explanation": "This text contains patterns that may indicate an attempt to influence an assistant. Review the matches; they are indicators, not proof of an attack.",
            "evidence": matches,
        }

    return {
        "status": "No obvious indicators found",
        "level": "clear",
        "explanation": "No configured rule matched this text. This is not proof that the text is safe.",
        "evidence": [],
    }
