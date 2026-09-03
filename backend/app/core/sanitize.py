"""Treat every byte of external text as hostile.

Two jobs:
  1. `sanitize_external_text` neutralises prompt-injection markers and returns the
     cleaned text plus a list of what was neutralised (never silently dropped).
  2. `wrap_untrusted` puts text inside a nonce-tagged envelope so that injected
     closing delimiters cannot escape it.

The raw original is still stored in `raw_records.content_raw` for audit; only the
sanitised copy is ever shown to a model.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from dataclasses import dataclass, field

# Patterns that have no legitimate reason to appear in collected content and that
# are commonly used to hijack a model. Case-insensitive, whitespace-tolerant.
_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "instruction_override",
        re.compile(r"ignore\s+(all\s+)?(the\s+)?(previous|prior|above)\s+instructions?", re.I),
    ),
    ("instruction_override", re.compile(r"disregard\s+(all\s+)?(previous|prior|above)", re.I)),
    ("role_spoof", re.compile(r"^\s*(system|assistant|developer)\s*:", re.I | re.M)),
    ("tag_spoof", re.compile(r"</?\s*(system|assistant|user|instructions?)\s*>", re.I)),
    ("tool_spoof", re.compile(r"<\s*/?\s*(function_calls|tool_use|invoke|antml)[^>]*>", re.I)),
    ("prompt_leak", re.compile(r"(reveal|print|repeat|output)\s+(your\s+)?(system\s+)?prompt", re.I)),
    ("policy_override", re.compile(r"you\s+are\s+now\s+(a|an|in)\s+", re.I)),
    ("exfiltration", re.compile(r"!\[[^\]]*\]\(\s*https?://[^)]*\{[^)]*\)", re.I)),
    ("secret_request", re.compile(r"\b(api[_ -]?key|secret[_ -]?key|password|token)\b\s*[:=]", re.I)),
    ("long_base64", re.compile(r"[A-Za-z0-9+/]{400,}={0,2}")),
]

_ZERO_WIDTH = dict.fromkeys([0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2028, 0x2029, 0x2060, 0xFEFF])

MAX_TEXT_CHARS = 20_000


@dataclass(slots=True)
class SanitizedText:
    text: str
    neutralised: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def is_suspicious(self) -> bool:
        return bool(self.neutralised)


def sanitize_external_text(raw: str | None, max_chars: int = MAX_TEXT_CHARS) -> SanitizedText:
    if not raw:
        return SanitizedText(text="")

    text = unicodedata.normalize("NFKC", raw).translate(_ZERO_WIDTH)
    text = text.replace("\x00", "")

    neutralised: list[str] = []
    for label, pattern in _INJECTION_PATTERNS:
        text, count = pattern.subn(f"[neutralised:{label}]", text)
        if count:
            neutralised.extend([label] * count)

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars] + "\n[truncated]"

    return SanitizedText(text=text.strip(), neutralised=neutralised, truncated=truncated)


def wrap_untrusted(text: str, label: str = "SOURCE_CONTENT") -> str:
    """Wrap sanitised external text in a nonce envelope for LLM consumption."""
    nonce = secrets.token_hex(8)
    return f"<<<UNTRUSTED_{label} {nonce}>>>\n{text}\n<<<END_UNTRUSTED_{label} {nonce}>>>"


_TAG_RE = re.compile(r"<[^>]+>")
_ENTITIES = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&apos;": "'",
    "&nbsp;": " ",
    "&mdash;": "-",
    "&ndash;": "-",
}


def strip_html(raw: str | None) -> str:
    """Very small HTML-to-text reducer for RSS summaries.

    Deliberately not a parser: feed content is stored as a short excerpt, never a
    full reproduction of a copyrighted article, so a lossy strip is the right tool.
    """
    if not raw:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>|</p>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))) if int(m.group(1)) < 0x110000 else " ", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()
