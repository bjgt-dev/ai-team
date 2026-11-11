"""Kids iteration 5 boundary tests (EVAL-001, Eval OS second phase Case A).

12 boundary tests on top of iteration 4 (test_kids4.py, 35 tests).
Each test pins a boundary value of the real implementation:
  - classify / english_words / is_short_sentence edges
  - ladder wrap-around and level names
  - exact session-length (300 s) and daily-cap (1200 s) boundaries
  - silence / frustration ladders: 2 vs 3 turns
  - parent daily-cap validation
No source file is modified; this file only adds tests.
"""

import pytest

from immersion_engine import (
    DAILY_SOFT_CAP_SECONDS,
    SESSION_SECONDS,
    ImmersionEngine,
    ParentControls,
    classify_child_turn,
    english_words,
    is_short_sentence,
)
from ladder import LEVELS, level_count, level_name, prompt_for


def test_classify_punctuation_or_digits_only_is_unclear():
    assert classify_child_turn("!!!") == "unclear"
    assert classify_child_turn("123") == "unclear"
    assert classify_child_turn("?!...") == "unclear"


def test_classify_mixed_chinese_and_english_is_chinese():
    assert classify_child_turn("red 苹果") == "chinese"
    assert classify_child_turn("苹果 red") == "chinese"
    assert classify_child_turn("你好") == "chinese"


def test_classify_empty_or_whitespace_only_is_silence():
    assert classify_child_turn("") == "silence"
    assert classify_child_turn("   ") == "silence"
    assert classify_child_turn(None) == "silence"
    assert classify_child_turn("\t\n") == "silence"


def test_english_words_ignores_numbers_and_punctuation():
    assert english_words("I have 3 red balls!") == ["I", "have", "red", "balls"]
    assert english_words("") == []
    assert english_words(None) == []


def test_is_short_sentence_three_word_boundary():
    assert is_short_sentence("I like apples") is True
    assert is_short_sentence("I see a blue car") is True
    assert is_short_sentence("I like") is False
    assert is_short_sentence("red big ball") is False


def test_prompt_for_wraps_at_level_end():
    assert prompt_for(0, 13) is LEVELS[0]["items"][0]
    assert prompt_for(0, 14) is LEVELS[0]["items"][1]
    assert prompt_for(1, 8) is LEVELS[1]["items"][0]
    assert prompt_for(2, 6) is LEVELS[2]["items"][0]
    assert prompt_for(3, 5) is LEVELS[3]["items"][0]


def test_level_names_and_count():
    assert level_count() == 4
    assert [level_name(i) for i in range(4)] == [
        "words", "phrases", "sentences", "chat",
    ]


def test_session_ends_exactly_at_300_seconds():
    assert SESSION_SECONDS == 300
    engine = ImmersionEngine()
    engine.start()
    first = engine.respond("hello", dt_seconds=290)
    assert first.end is False
    assert engine.ended is False
    second = engine.respond("hello", dt_seconds=10)
    assert second.end is True
    assert engine.ended is True
    assert engine.end_reason == "time_up"
    assert engine.elapsed_seconds == 300


def test_daily_cap_exact_boundary_1199_starts_1200_blocks():
    assert DAILY_SOFT_CAP_SECONDS == 1200
    ok = ImmersionEngine(daily_seconds_used=1199)
    opening = ok.start()
    assert opening.end is False
    assert ok.ended is False
    capped = ImmersionEngine(daily_seconds_used=1200)
    reply = capped.start()
    assert reply.end is True
    assert capped.ended is True
    assert capped.end_reason == "soft_cap"


def test_two_silences_simplify_third_degrades():
    engine = ImmersionEngine()
    engine.start()
    first = engine.respond(None, dt_seconds=10)
    second = engine.respond(None, dt_seconds=10)
    assert first.action == "simplify"
    assert second.action == "simplify"
    assert engine.level == 0
    third = engine.respond(None, dt_seconds=10)
    assert third.action == "degrade"
    assert engine.level == 0


def test_two_chinese_simplify_third_gentle_ends():
    engine = ImmersionEngine()
    engine.start()
    first = engine.respond("红苹果", dt_seconds=10)
    second = engine.respond("红苹果", dt_seconds=10)
    assert first.action == "simplify"
    assert second.action == "simplify"
    assert engine.ended is False
    third = engine.respond("红苹果", dt_seconds=10)
    assert third.end is True
    assert engine.ended is True
    assert engine.end_reason == "gentle_end"


def test_parent_daily_cap_must_be_positive():
    controls = ParentControls(engine=ImmersionEngine())
    with pytest.raises(ValueError):
        controls.set_daily_cap_minutes(0)
    with pytest.raises(ValueError):
        controls.set_daily_cap_minutes(-5)
    controls.set_daily_cap_minutes(1)
    assert controls.daily_cap_minutes == 1
