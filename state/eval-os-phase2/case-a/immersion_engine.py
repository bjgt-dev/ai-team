"""Iteration 4: English-immersion dialogue engine.

Pure Python standard library. No network, no audio, no cloud endpoints.
The engine only ever receives *text* (a transcript string produced by an
ASR adapter). It never sees, stores, uploads or trains on audio.

Hard principles (requirements spec, section 5):
  1. Pure English immersion, zero Chinese in any device output.
  2. No correction, no testing: everything the child says is accepted.
  3. Speaking pays off at once: every child utterance gets a response.
  4. Short: one session is 5 minutes, confirmed.
  5. The child is the teacher, not the student.

Privacy defaults (verdict condition 2): raw audio is never uploaded, never
written to disk, never used for training. Cloud ASR/TTS endpoints are
always None until a parent gives explicit consent, and even then the
endpoint value is supplied by the parent at configuration time, never
hard-coded here.
"""

import re
from dataclasses import dataclass, field

# --- Duration (locked: one session is 5 minutes, confirmed) --------------
SESSION_MINUTES = 5
SESSION_SECONDS = SESSION_MINUTES * 60
DAILY_SOFT_CAP_MINUTES = 20
DAILY_SOFT_CAP_SECONDS = DAILY_SOFT_CAP_MINUTES * 60

# --- Degradation / ending thresholds --------------------------------------
SILENCE_DEGRADE_AFTER = 3   # consecutive silent turns -> drop one level
SILENCE_END_AFTER = 5       # consecutive silent turns -> warm ending
FRUSTRATION_END_AFTER = 3   # consecutive non-English turns at level 0 -> end

# --- Text helpers ----------------------------------------------------------
_CJK_RE = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff\u3400-\u4dbf]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]+")

# Phrases the engine must never emit (no correction, no testing).
FORBIDDEN_CORRECTION_PHRASES = (
    "no, ",
    "wrong",
    "not right",
    "say it like",
    "try again",
    "incorrect",
    "that is not",
    "listen and repeat",
)

# Small verb set used only to recognise a short-sentence attempt.
# This is a counting rule, not a grammar lesson: the child is never
# told about verbs and never corrected.
VERBS = {
    "like", "love", "want", "see", "have", "is", "are", "am",
    "go", "play", "eat", "look", "say",
}


def contains_chinese(text):
    """True when *text* holds any CJK character."""
    if not text:
        return False
    return bool(_CJK_RE.search(text))


def english_words(text):
    """ASCII words in *text* (used for counting only)."""
    if not text:
        return []
    return _ASCII_WORD_RE.findall(text)


def is_short_sentence(text):
    """Counting rule for a short sentence: >= 3 English words with a verb.

    This only decides the counter. It never judges the child: a one-word
    answer is just as good, it simply counts in a different bucket.
    """
    words = [w.lower() for w in english_words(text)]
    return len(words) >= 3 and any(w in VERBS for w in words)


def classify_child_turn(text):
    """Classify one child turn: silence | chinese | english | unclear.

    - silence: empty / whitespace only / None.
    - chinese: holds any CJK character (the child answered in Chinese).
    - english: holds ASCII letters and no CJK (any English attempt,
      correct or not, counts as English -- the engine never judges).
    - unclear: no letters at all (noise, babble marks, punctuation).
    """
    if text is None or not str(text).strip():
        return "silence"
    if contains_chinese(text):
        return "chinese"
    if _ASCII_WORD_RE.search(text):
        return "english"
    return "unclear"


class PrivacyNotDecided(Exception):
    """Raised when cloud capability is configured before a parent decides."""


@dataclass
class AudioPrivacy:
    """Privacy defaults for child voice. Everything is off by default.

    The engine works fully offline with these defaults. Cloud capability
    is only possible after explicit parent consent, and the endpoint is
    supplied at configuration time -- it is never hard-coded here.
    """

    store_audio: bool = False
    upload_audio: bool = False
    use_for_training: bool = False
    cloud_asr_endpoint: object = None
    cloud_tts_endpoint: object = None
    parent_consent: bool = False

    def give_parent_consent(self):
        """Parent switch: consent to cloud capability. Still off until
        an endpoint is configured."""
        self.parent_consent = True

    def configure_cloud(self, *, asr=None, tts=None):
        """Set cloud endpoints. Refuses until the parent has consented,
        and refuses to store or upload audio even then unless the parent
        also turns those switches on explicitly (not part of this API:
        there is no call that enables upload -- see delete_all_records)."""
        if not self.parent_consent:
            raise PrivacyNotDecided(
                "Parent privacy decision is pending: cloud ASR/TTS "
                "endpoint must not be configured."
            )
        if asr is not None:
            self.cloud_asr_endpoint = asr
        if tts is not None:
            self.cloud_tts_endpoint = tts

    def delete_all_records(self, engine):
        """Parent delete entry: wipe every counter the engine holds.
        The engine keeps no transcripts and no audio, so counters are
        the only records that exist."""
        engine.reset_counts()
        self.cloud_asr_endpoint = None
        self.cloud_tts_endpoint = None


@dataclass
class ParentControls:
    """Parent-visible controls. Usage records are counts only: no child
    speech, no transcripts, no audio."""

    engine: object
    daily_cap_minutes: int = DAILY_SOFT_CAP_MINUTES

    def set_daily_cap_minutes(self, minutes):
        """Parent setting override for the daily soft cap."""
        if minutes <= 0:
            raise ValueError("daily cap must be positive")
        self.daily_cap_minutes = minutes

    def usage_summary(self):
        """What a parent can see: counts and durations only."""
        return {
            "sessions_today": self.engine.sessions_today,
            "seconds_today": self.engine.daily_seconds_used,
            "english_utterances": self.engine.counts["utterances"],
            "english_words": self.engine.counts["words"],
            "short_sentences": self.engine.counts["short_sentences"],
            "spontaneous": self.engine.counts["spontaneous"],
            "prompted": self.engine.counts["prompted"],
        }

    def delete_all_data(self):
        """Parent delete entry: wipe counters and cloud endpoints."""
        self.engine.privacy.delete_all_records(self.engine)
        self.engine.daily_seconds_used = 0
        self.engine.sessions_today = 0


@dataclass
class EngineReply:
    """One device turn. Every field is English-only, always."""

    text: str
    visual: str       # picture cue, e.g. "picture: red apple"
    gesture: str      # gesture cue, e.g. "point to the apple"
    action: str       # prompt | encourage | simplify | degrade | end | soft_cap
    end: bool = False


# Minimal hardware interface contract (verdict condition 6).
# Software runs on an existing tablet/phone/dev board; hardware only
# adapts to this interface. No firmware, no casing, no 4G in scope.
HARDWARE_CONTRACT = {
    "platforms": ["tablet", "phone", "dev-board"],
    "mic": "single microphone, push-to-talk or always-listening with "
           "local voice activity detection; audio never leaves device",
    "speaker": "single speaker for device voice",
    "display": "status only: listening / thinking / speaking, plus a "
               "simple picture cue; no game, no video, no complex touch",
    "button": "one big button: start / stop the 5-minute session",
    "led": "one LED mirroring display status",
    "connectivity": "offline-first; cloud ASR/TTS only if the parent "
                    "has consented and configured an endpoint",
}


class ImmersionEngine:
    """English-immersion dialogue engine for one 5-minute session.

    The engine keeps counts only. It never stores child speech text,
    transcripts or audio: each turn is processed and discarded.
    """

    def __init__(self, privacy=None, daily_seconds_used=0, sessions_today=0):
        self.privacy = privacy if privacy is not None else AudioPrivacy()
        self.level = 0
        self.counts = {
            "utterances": 0,       # English turns spoken by the child
            "words": 0,            # English words spoken
            "short_sentences": 0,  # short-sentence attempts (see counter)
            "spontaneous": 0,      # English turn without a rescue prompt
            "prompted": 0,         # English turn right after a rescue
        }
        self.elapsed_seconds = 0
        self.daily_seconds_used = daily_seconds_used
        self.sessions_today = sessions_today
        self.consecutive_silence = 0
        self.consecutive_non_english = 0
        self.successes_at_level = 0
        self.ended = False
        self.end_reason = None
        self._last_action_was_simplify = False
        self._daily_counted = False
        self._ladder_index = 0

    # -- public ------------------------------------------------------------

    def reset_counts(self):
        for key in self.counts:
            self.counts[key] = 0
        self.successes_at_level = 0
        self.consecutive_silence = 0
        self.consecutive_non_english = 0

    def start(self, parent_override=False):
        """Begin a session. Returns the opening reply.

        Daily soft cap: at 20 minutes the device says so warmly and does
        not start. The cap is soft, not a lock: a parent override starts
        a session anyway, and hitting the cap is never a failure.
        """
        if self.ended:
            return self._closing_reply()
        cap = DAILY_SOFT_CAP_SECONDS
        if self.daily_seconds_used >= cap and not parent_override:
            self.ended = True
            self.end_reason = "soft_cap"
            return EngineReply(
                text="Wow, we played a lot today! See you tomorrow!",
                visual="picture: moon and stars",
                gesture="wave goodbye",
                action="soft_cap",
                end=True,
            )
        self.sessions_today += 1
        return self._prompt_reply(first=True)

    def respond(self, child_text, dt_seconds=10):
        """One child turn. *dt_seconds* is the virtual time the turn took."""
        if self.ended:
            return self._closing_reply()
        self.elapsed_seconds += dt_seconds
        if self.elapsed_seconds >= SESSION_SECONDS:
            return self.end("time_up")

        kind = classify_child_turn(child_text)
        if kind in ("silence", "unclear"):
            return self._on_silence()
        if kind == "chinese":
            return self._on_chinese()
        return self._on_english(child_text)

    def end(self, reason):
        """Warm ending. Ending early is never a failure."""
        if self.ended:
            return self._closing_reply()
        self.ended = True
        self.end_reason = reason
        if not self._daily_counted:
            self.daily_seconds_used += self.elapsed_seconds
            self._daily_counted = True
        return self._closing_reply()

    # -- turn handlers -----------------------------------------------------

    def _on_silence(self):
        self.consecutive_silence += 1
        self.consecutive_non_english += 1
        if self.consecutive_silence >= SILENCE_END_AFTER:
            return self.end("quiet_end")
        if self.consecutive_silence >= SILENCE_DEGRADE_AFTER:
            self.level = max(0, self.level - 1)
            self._last_action_was_simplify = True
            item = self._current_item()
            return EngineReply(
                text="Look! " + item["text"],
                visual=item["visual"],
                gesture=item["gesture"],
                action="degrade",
            )
        self._last_action_was_simplify = True
        item = self._current_item()
        return EngineReply(
            text="Look! " + item["text"],
            visual=item["visual"],
            gesture=item["gesture"],
            action="simplify",
        )

    def _on_chinese(self):
        # The child answered in Chinese: never switch to Chinese, never
        # correct. Restate in simpler English with picture + gesture.
        self.consecutive_silence = 0
        self.consecutive_non_english += 1
        if self.level == 0 and self.consecutive_non_english >= FRUSTRATION_END_AFTER:
            return self.end("gentle_end")
        self._last_action_was_simplify = True
        item = self._current_item()
        return EngineReply(
            text="Look! " + item["text"],
            visual=item["visual"],
            gesture=item["gesture"],
            action="simplify",
        )

    def _on_english(self, child_text):
        # Everything the child says is accepted. Count it, praise it,
        # keep the chat going with the next i+1 prompt.
        self.consecutive_silence = 0
        self.consecutive_non_english = 0
        words = english_words(child_text)
        self.counts["utterances"] += 1
        self.counts["words"] += len(words)
        if is_short_sentence(child_text):
            self.counts["short_sentences"] += 1
        if self._last_action_was_simplify:
            self.counts["prompted"] += 1
        else:
            self.counts["spontaneous"] += 1
        self._last_action_was_simplify = False
        self.successes_at_level += 1
        self._maybe_level_up()
        item = self._current_item()
        return EngineReply(
            text="Yes! " + item["text"],
            visual=item["visual"],
            gesture=item["gesture"],
            action="encourage",
        )

    # -- internals ---------------------------------------------------------

    def _maybe_level_up(self):
        # Two counted English turns at a level moves one step up the
        # ladder. Advancement is by speaking, never by correctness.
        from ladder import LEVELS

        if self.successes_at_level >= 2 and self.level < len(LEVELS) - 1:
            self.level += 1
            self.successes_at_level = 0

    def _current_item(self):
        from ladder import prompt_for

        item = prompt_for(self.level, self._ladder_index)
        self._ladder_index += 1
        return item

    def _prompt_reply(self, first=False):
        item = self._current_item()
        text = ("Hi! I am Max! " if first else "") + "Look! " + item["text"]
        return EngineReply(
            text=text,
            visual=item["visual"],
            gesture=item["gesture"],
            action="prompt",
        )

    def _closing_reply(self):
        said = self.counts["utterances"]
        if said > 0:
            text = "You did great! You said so much English! See you next time!"
        else:
            text = "That was fun! See you next time!"
        return EngineReply(
            text=text,
            visual="picture: smiling sun",
            gesture="wave goodbye",
            action="end",
            end=True,
        )
