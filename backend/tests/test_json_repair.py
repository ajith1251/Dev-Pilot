"""Unit tests for the shared LLM JSON-repair pipeline.

The pipeline (extracted from PlannerAgent) is the single recovery path for
malformed model JSON across planner and coding agent. These tests pin the
pure-function behaviour directly; agent-level tests cover the wiring.
"""

from app.agents.json_repair import (
    extract_json_object,
    fix_single_quotes,
    mask_string_contents,
    parse_llm_json,
    repair_json_text,
    unmask_string_contents,
)


class TestFixSingleQuotes:
    def test_converts_single_to_double(self):
        assert fix_single_quotes("'hello'") == '"hello"'

    def test_ignores_double_quoted_regions(self):
        assert fix_single_quotes('{"a": "it\'s"}') == '{"a": "it\'s"}'

    def test_nested_quotes_escaped(self):
        assert fix_single_quotes("'he said \"hi\"'") == '"he said \\"hi\\""'

    def test_apostrophe_inside_single_quoted_value(self):
        assert fix_single_quotes("'don't'") == '"don\'t"'

    def test_unterminated_single_quote_left_alone(self):
        assert fix_single_quotes("'abc") == "'abc"


class TestMaskRoundTrip:
    def test_string_bodies_replaced_and_restored(self):
        text = '{"key": "a: b", "n": 1}'
        masked, tokens = mask_string_contents(text)
        assert tokens == ["key", "a: b", "n"]
        assert unmask_string_contents(masked, tokens) == text

    def test_regex_repair_does_not_touch_string_values(self):
        text = '{changes: [{path: "a: b, c", new_content: "def f(){ }"}]}'
        masked, tokens = mask_string_contents(text)
        # unquoted keys present after masking but values are masked tokens
        assert ": b, c" not in masked
        assert "def f(){" not in masked
        restored = unmask_string_contents(masked, tokens)
        assert restored == text


class TestRepairJsonText:
    def test_unquoted_keys(self):
        assert repair_json_text('{a: "1", b: 2}') == '{"a": "1", "b": 2}'

    def test_trailing_commas(self):
        assert repair_json_text('{"a": 1, "b": [1, 2,],}') == '{"a": 1, "b": [1, 2]}'

    def test_single_quotes(self):
        repaired = repair_json_text("{'a': 'x', 'b': [{'c': 1}]}")
        assert repaired == '{"a": "x", "b": [{"c": 1}]}'

    def test_bare_none_true_false(self):
        assert repair_json_text('{"a": None, "b": True, "c": False}') == (
            '{"a": null, "b": true, "c": false}'
        )

    def test_unicode_punctuation(self):
        repaired = repair_json_text('{\u201csummary\u201d: \u201cx\u2014y\u201d}')
        assert repaired == '{"summary": "x-y"}'

    def test_string_values_untouched_by_key_quoting(self):
        text = '{summary: "contains, b: colon", steps: [{id: "S1"}]}'
        repaired = repair_json_text(text)
        assert repaired == '{"summary": "contains, b: colon", "steps": [{"id": "S1"}]}'

    def test_bare_word_in_string_value_not_quoted(self):
        text = '{"a": "True", "b": true}'
        repaired = repair_json_text(text)
        assert repaired == '{"a": "True", "b": true}'

    def test_unrepairable_returns_none(self):
        assert repair_json_text("just prose") is None

    def test_already_valid_unchanged(self):
        assert repair_json_text('{"a": 1}') == '{"a": 1}'


class TestExtractJsonObject:
    def test_balanced_object(self):
        assert extract_json_object('{"a": {"b": 1}} tail') == '{"a": {"b": 1}}'

    def test_braces_inside_string_ignored(self):
        text = '{"a": "x { y", "b": 1}'
        assert extract_json_object(text) == text

    def test_unbalanced_returns_empty(self):
        assert extract_json_object('{"a": 1') == ""

    def test_no_brace_returns_empty(self):
        assert extract_json_object("no json") == ""


class TestParseLlmJson:
    def test_plain_valid(self):
        assert parse_llm_json('{"a": 1}') == {"a": 1}

    def test_markdown_fence(self):
        assert parse_llm_json("```json\n{\"a\": 1}\n```") == {"a": 1}

    def test_repairs_malformed(self):
        assert parse_llm_json('{a: "1", b: [1,],}') == {"a": "1", "b": [1]}

    def test_empty_returns_empty(self):
        assert parse_llm_json("") == {}

    def test_prose_returns_empty(self):
        assert parse_llm_json("no json here at all") == {}

    def test_non_dict_root_returns_empty(self):
        assert parse_llm_json("[1, 2, 3]") == {}
