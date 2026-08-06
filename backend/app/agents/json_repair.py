"""Shared best-effort JSON repair for weak LLM outputs.

Extracted from PlannerAgent so the same recovery pipeline (smart quotes,
control chars, trailing commas, single-quoted strings, unquoted keys, bare
None/True/False) is available to every agent that parses model JSON —
currently the planner and the coding agent (whose live failures surfaced the
same "Expecting property name enclosed in double quotes" class of bug).

Every function here is pure and raises only on programmer error; the parse
helpers never raise on bad input, they degrade to the documented fallback.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple


def fix_single_quotes(text: str) -> str:
    """Convert single-quoted strings to double-quoted, ignoring double-quoted
    regions (so apostrophes inside real JSON strings are never touched).

    A single quote followed by a word character is treated as an apostrophe
    (English contractions like ``don't`` / ``it's``), not a closing quote.
    Control characters in the converted value are JSON-escaped so code
    snippets containing literal newlines survive json.loads.
    """
    out: List[str] = []
    i, n = 0, len(text)
    in_double = False
    while i < n:
        c = text[i]
        if in_double:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_double = False
            i += 1
            continue
        if c == '"':
            in_double = True
            out.append(c)
            i += 1
            continue
        if c == "'":
            j = i + 1
            buf: List[str] = []
            while j < n:
                ch = text[j]
                if ch == "'":
                    nxt = text[j + 1] if j + 1 < n else ""
                    if nxt and (nxt.isalnum() or nxt == "'"):
                        buf.append(ch)
                        j += 1
                        continue
                    break
                buf.append(ch)
                j += 1
            if j < n:
                content = "".join(buf)
                content = content.replace("\\", "\\\\").replace('"', '\\"')
                content = (
                    content.replace("\r", "\\r")
                    .replace("\n", "\\n")
                    .replace("\t", "\\t")
                )
                out.append('"')
                out.append(content)
                out.append('"')
                i = j + 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def mask_string_contents(text: str) -> Tuple[str, List[str]]:
    """Replace double-quoted string bodies with placeholders so regex repairs
    never touch string values; returns (masked_text, tokens)."""
    tokens: List[str] = []
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            body: List[str] = []
            while j < n:
                c = text[j]
                if c == "\\":
                    body.append(c)
                    if j + 1 < n:
                        body.append(text[j + 1])
                    j += 2
                    continue
                if c == '"':
                    break
                body.append(c)
                j += 1
            if j < n:
                token = f"\x00STR{len(tokens)}\x00"
                tokens.append("".join(body))
                out.append('"')
                out.append(token)
                out.append('"')
                i = j + 1
                continue
        out.append(ch)
        i += 1
    return "".join(out), tokens


def unmask_string_contents(masked: str, tokens: List[str]) -> str:
    for idx, token in enumerate(tokens):
        masked = masked.replace(f"\x00STR{idx}\x00", token)
    return masked


def repair_json_text(text: str) -> Optional[str]:
    """Best-effort repair of common malformed JSON emitted by weaker LLMs.

    Applies cumulative fixes (unicode punctuation, control chars, trailing
    commas, single-quoted strings, unquoted keys, bare None/True/False) and
    returns the repaired text only if it finally parses as JSON.
    """
    t = text
    for src, dst in (
        ("\u201c", '"'), ("\u201d", '"'),
        ("\u2018", "'"), ("\u2019", "'"),
        ("\u2014", "-"), ("\u2013", "-"),
        ("\u2026", "..."), ("\u00a0", " "),
        ("\u200b", ""), ("\ufeff", ""),
    ):
        t = t.replace(src, dst)

    t = "".join(ch for ch in t if ch in "\t\n\r" or ord(ch) >= 32)

    t = fix_single_quotes(t)
    masked, tokens = mask_string_contents(t)

    masked = re.sub(r",\s*([}\]])", r"\1", masked)
    masked = re.sub(
        r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)", r'\1"\2"\3', masked
    )
    masked = re.sub(r"\bNone\b", "null", masked)
    masked = re.sub(r"\bTrue\b", "true", masked)
    masked = re.sub(r"\bFalse\b", "false", masked)

    repaired = unmask_string_contents(masked, tokens)

    try:
        json.loads(repaired)
    except json.JSONDecodeError:
        return None
    return repaired


def extract_json_object(text: str) -> str:
    """Extract the first balanced-brace JSON object (best effort)."""
    start = text.find("{")
    if start == -1:
        return ""

    depth = 0
    in_string = False
    escape = False
    end = start

    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

    if depth != 0:
        return ""
    return text[start:end]


def parse_llm_json(text: str) -> Dict[str, Any]:
    """Parse JSON from an LLM response, handling markdown fences, balanced
    braces, and the common malformed-JSON repair fallback.

    Never raises: returns an empty dict when nothing parses.
    """
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?\s*```\s*$", "", cleaned)
    cleaned = cleaned.strip()

    if not cleaned:
        return {}

    candidates = [cleaned]
    extracted = extract_json_object(cleaned)
    if extracted and extracted != cleaned:
        candidates.append(extracted)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        repaired = repair_json_text(candidate)
        if repaired is not None:
            try:
                parsed = json.loads(repaired)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

    return {}
