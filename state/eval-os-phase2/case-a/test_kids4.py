"""Tests for kids iteration 4 (English immersion).

Coverage, by verdict condition:
  - immersion boundary (cond 4): Chinese input -> zero Chinese output;
    silence / wrong English handled in English only; no correction ever.
  - duration (cond 5): one session is 5 minutes; daily 20-minute soft
    cap; soft cap is a prompt, not a hard lock, never a failure.
  - counting (cond 3): English utterances / words / short sentences;
    spontaneous vs prompted boundary.
  - ASR mock path (cond 8): MockASR + local playback, no real voice.
  - privacy defaults (cond 2): no upload / store / train; no cloud
    endpoint without parent consent; delete entry wipes records.
  - degradation / ending (cond 4): silence and frustration ladders.
  - safety (cond 7): no network, no ads / purchases / links in code.
"""

import os

import pytest

from immersion_engine import (
    DAILY_SOFT_CAP_SECONDS,
    FORBIDDEN_CORRECTION_PHRASES,
    SESSION_SECONDS,
    AudioPrivacy,
    ImmersionEngine,
    ParentControls,
    PrivacyNotDecided,
    classify_child_turn,
    contains_chinese,
    is_short_sentence,
)
from ladder import LEVELS, level_count, prompt_for
from simulator import DEMO_SCRIPT, MockASR, PlaybackASR, run_session

HERE = os.path.dirname(__file__)
SOURCE_FILES = ["immersion_engine.py", "ladder.py", "simulator.py"]


def _all_replies_english(report):
    bad = [r.text for r in report.replies if contains_chinese(r.text)]
    assert bad == [], "engine reply contained Chinese: %r" % (bad,)


# --------------------------------------------------------------------------
# Immersion boundary
# --------------------------------------------------------------------------

def test_chinese_input_zero_chinese_output():
    engine = ImmersionEngine()
    engine.start()
    reply = engine.respond("红苹果", dt_seconds=10)
    assert not contains_chinese(reply.text)
    assert not contains_chinese(reply.visual)
    assert not contains_chinese(reply.gesture)
    assert reply.action == "simplify"


def test_silence_gets_english_prompt():
    engine = ImmersionEngine()
    engine.start()
    for silent in ("", "   ", None):
        reply = engine.respond(silent, dt_seconds=10)
        assert not contains_chinese(reply.text)
        assert reply.action in ("simplify", "degrade")
        assert not reply.end


def test_wrong_english_never_corrected():
    engine = ImmersionEngine()
    engine.start()
    reply = engine.respond("red appel", dt_seconds=10)
    lowered = reply.text.lower()
    for phrase in FORBIDDEN_CORRECTION_PHRASES:
        assert phrase not in lowered
    assert engine.counts["utterances"] == 1  # accepted, not corrected


def test_full_session_zero_chinese():
    report = run_session(DEMO_SCRIPT)
    _all_replies_english(report)
    for reply in report.replies:
        assert not contains_chinese(reply.visual)
        assert not contains_chinese(reply.gesture)


def test_no_correction_phrases_anywhere():
    script = [
        (10, "红苹果"),
        (10, "red appel"),
        (10, "blu bal"),
        (10, "I is happy"),
        (10, None),
        (10, "cat"),
    ]
    report = run_session(script)
    for reply in report.replies:
        lowered = reply.text.lower()
        for phrase in FORBIDDEN_CORRECTION_PHRASES:
            assert phrase not in lowered


def test_classify_child_turn():
    assert classify_child_turn(None) == "silence"
    assert classify_child_turn("   ") == "silence"
    assert classify_child_turn("你好") == "chinese"
    assert classify_child_turn("red apple") == "english"
    assert classify_child_turn("red appel") == "english"
    assert classify_child_turn("?!") == "unclear"


# --------------------------------------------------------------------------
# Duration: 5 minutes a session, 20 minutes a day (soft cap)
# --------------------------------------------------------------------------

def test_session_is_five_minutes():
    script = [(10, "red") for _ in range(30)]  # 30 x 10s = 300s
    report = run_session(script)
    assert report.duration_seconds == SESSION_SECONDS == 300
    assert report.ended_reason == "time_up"
    assert report.failed is False


def test_session_never_runs_past_five_minutes():
    script = [(10, "red") for _ in range(60)]  # 600s of script
    report = run_session(script)
    assert report.duration_seconds == 300
    assert len([r for r in report.replies if r.end]) >= 1


def test_daily_soft_cap_blocks_fifth_session():
    report = run_session(
        [(10, "red")], daily_seconds_used=DAILY_SOFT_CAP_SECONDS
    )
    assert report.replies[0].action == "soft_cap"
    assert report.ended_reason == "soft_cap"
    assert report.failed is False


def test_soft_cap_is_not_a_hard_lock():
    engine = ImmersionEngine(daily_seconds_used=DAILY_SOFT_CAP_SECONDS)
    reply = engine.start(parent_override=True)
    assert reply.action == "prompt"
    assert not engine.ended


def test_cross_session_accumulation():
    daily = 0
    sessions = 0
    for _ in range(4):
        report = run_session(
            [(10, "red") for _ in range(30)],
            daily_seconds_used=daily, sessions_today=sessions,
        )
        assert report.ended_reason == "time_up"
        daily = report.daily_seconds_used_after
        sessions = report.sessions_today_after
    assert daily == DAILY_SOFT_CAP_SECONDS  # 4 x 300s = 1200s
    fifth = run_session([(10, "red")], daily_seconds_used=daily)
    assert fifth.ended_reason == "soft_cap"
    assert fifth.failed is False


# --------------------------------------------------------------------------
# Counting: utterances / words / short sentences / spontaneous vs prompted
# --------------------------------------------------------------------------

def test_counts_english_words_and_utterances():
    engine = ImmersionEngine()
    engine.start()
    engine.respond("red apple", dt_seconds=10)
    assert engine.counts["utterances"] == 1
    assert engine.counts["words"] == 2
    assert engine.counts["short_sentences"] == 0


def test_counts_short_sentence():
    assert is_short_sentence("I like red apples")
    assert is_short_sentence("The ball is big")
    assert not is_short_sentence("red apple")
    assert not is_short_sentence("cat")
    engine = ImmersionEngine()
    engine.start()
    engine.respond("I like red apples", dt_seconds=10)
    assert engine.counts["short_sentences"] == 1


def test_spontaneous_vs_prompted_boundary():
    engine = ImmersionEngine()
    engine.start()
    engine.respond("red", dt_seconds=10)          # after normal prompt
    assert engine.counts["spontaneous"] == 1
    assert engine.counts["prompted"] == 0
    engine.respond("红苹果", dt_seconds=10)       # rescue (simplify)
    engine.respond("apple", dt_seconds=10)        # right after rescue
    assert engine.counts["prompted"] == 1


def test_chinese_and_silence_not_counted_as_english():
    engine = ImmersionEngine()
    engine.start()
    engine.respond("你好", dt_seconds=10)
    engine.respond(None, dt_seconds=10)
    engine.respond("?! ", dt_seconds=10)
    assert engine.counts["utterances"] == 0
    assert engine.counts["words"] == 0


def test_level_advances_by_speaking_not_correctness():
    engine = ImmersionEngine()
    engine.start()
    assert engine.level == 0
    engine.respond("red appel", dt_seconds=10)  # wrong, still counts
    engine.respond("blu", dt_seconds=10)
    assert engine.level == 1  # two counted turns -> one step up


# --------------------------------------------------------------------------
# ASR mock path (no real child voice anywhere)
# --------------------------------------------------------------------------

def test_mock_asr_drives_engine():
    asr = MockASR({"rec-1": "red apple", "rec-2": "红苹果"})
    engine = ImmersionEngine()
    engine.start()
    engine.respond(asr.transcribe("rec-1"), dt_seconds=10)
    assert engine.counts["utterances"] == 1
    reply = engine.respond(asr.transcribe("rec-2"), dt_seconds=10)
    assert reply.action == "simplify"
    assert not contains_chinese(reply.text)


def test_mock_asr_unknown_id_is_silence():
    asr = MockASR({"rec-1": "red"})
    assert asr.transcribe("no-such-rec") == ""
    assert classify_child_turn(asr.transcribe("no-such-rec")) == "silence"


def test_playback_asr_sequence_drives_full_session():
    asr = PlaybackASR(["red", "big ball", "I like red apples", None, "cat"])
    script = [(10, asr.transcribe_next()) for _ in range(5)]
    report = run_session(script)
    assert report.counts["utterances"] == 4
    assert report.counts["short_sentences"] == 1
    assert report.failed is False
    _all_replies_english(report)


def test_playback_asr_runs_out_to_silence():
    asr = PlaybackASR(["red"])
    assert asr.transcribe_next() == "red"
    assert asr.transcribe_next() == ""


# --------------------------------------------------------------------------
# Privacy defaults
# --------------------------------------------------------------------------

def test_privacy_defaults_are_all_off():
    privacy = AudioPrivacy()
    assert privacy.store_audio is False
    assert privacy.upload_audio is False
    assert privacy.use_for_training is False
    assert privacy.cloud_asr_endpoint is None
    assert privacy.cloud_tts_endpoint is None
    assert privacy.parent_consent is False


def test_cloud_endpoint_refused_without_parent_consent():
    privacy = AudioPrivacy()
    with pytest.raises(PrivacyNotDecided):
        privacy.configure_cloud(asr="some-endpoint")


def test_cloud_endpoint_allowed_after_parent_consent():
    privacy = AudioPrivacy()
    privacy.give_parent_consent()
    privacy.configure_cloud(asr="parent-supplied-endpoint")
    assert privacy.cloud_asr_endpoint == "parent-supplied-endpoint"


def test_parent_delete_entry_wipes_records():
    engine = ImmersionEngine()
    engine.start()
    engine.respond("red apple", dt_seconds=10)
    assert engine.counts["utterances"] == 1
    controls = ParentControls(engine)
    summary = controls.usage_summary()
    assert summary["english_utterances"] == 1
    assert "红" not in str(summary)
    controls.delete_all_data()
    assert engine.counts["utterances"] == 0
    assert engine.privacy.cloud_asr_endpoint is None


def test_engine_stores_no_child_text():
    engine = ImmersionEngine()
    engine.start()
    engine.respond("红苹果很好吃", dt_seconds=10)
    engine.respond("我喜欢红色", dt_seconds=10)
    blob = str(vars(engine))
    assert "红苹果" not in blob
    assert "我喜欢" not in blob


# --------------------------------------------------------------------------
# Degradation and warm ending
# --------------------------------------------------------------------------

def test_three_silences_degrade_one_level():
    engine = ImmersionEngine()
    engine.start()
    engine.respond("red", dt_seconds=10)
    engine.respond("ball", dt_seconds=10)
    assert engine.level == 1  # levelled up by speaking
    engine.respond(None, dt_seconds=10)
    engine.respond(None, dt_seconds=10)
    reply = engine.respond(None, dt_seconds=10)  # third silence
    assert reply.action == "degrade"
    assert engine.level == 0


def test_five_silences_warm_end_not_failure():
    engine = ImmersionEngine()
    engine.start()
    reply = None
    for _ in range(5):
        reply = engine.respond(None, dt_seconds=10)
    assert engine.ended
    assert engine.end_reason == "quiet_end"
    assert reply.end
    assert not contains_chinese(reply.text)
    assert "See you next time!" in reply.text


def test_frustration_warm_end():
    engine = ImmersionEngine()
    engine.start()
    reply = None
    for _ in range(3):
        reply = engine.respond("听不懂", dt_seconds=10)
    assert engine.ended
    assert engine.end_reason == "gentle_end"
    assert reply.end
    assert not contains_chinese(reply.text)


# --------------------------------------------------------------------------
# Ladder content: i+1, not a lesson, not a grammar class
# --------------------------------------------------------------------------

def test_ladder_starts_with_colours_sizes_shapes():
    words = [item["text"] for item in LEVELS[0]["items"]]
    joined = " ".join(words)
    for token in ("Red", "Blue", "Big", "Small", "Circle", "Square"):
        assert token in joined
    assert level_count() == 4


def test_ladder_phrases_are_two_words():
    for item in LEVELS[1]["items"]:
        assert len(item["text"].rstrip("!").split()) == 2


def test_ladder_sentences_are_short():
    for item in LEVELS[2]["items"]:
        assert len(item["text"].split()) <= 5


def test_ladder_has_no_chinese_and_no_lesson_words():
    banned = ("grammar", "lesson", "test", "exam", "homework", "repeat after me")
    for level in LEVELS:
        for item in level["items"]:
            assert not contains_chinese(item["text"])
            lowered = item["text"].lower()
            for word in banned:
                assert word not in lowered


def test_prompt_for_cycles():
    first = prompt_for(0, 0)
    again = prompt_for(0, len(LEVELS[0]["items"]))
    assert first == again


# --------------------------------------------------------------------------
# Safety: no network, no ads / purchases / links in code
# --------------------------------------------------------------------------

def test_source_files_have_no_network_or_commerce():
    banned = (
        "import socket", "import urllib", "import requests",
        "http://", "https://", "advert", "purchase", "buy now",
    )
    for name in SOURCE_FILES:
        path = os.path.join(HERE, name)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for token in banned:
            assert token not in text, "%s contains %r" % (name, token)


def test_demo_session_report_shape():
    report = run_session(DEMO_SCRIPT)
    assert report.failed is False
    assert report.ended_reason == "script_done"
    assert report.counts["utterances"] >= 1
    assert report.daily_seconds_used_after == 100
