"""Iteration 4: 5-minute session simulator with mock voice input.

Drives the immersion engine through a full session using scripted mock
input. Time is virtual: the script's delays advance the session clock,
nothing sleeps, nothing records real audio.

Test voice path (verdict condition 8): real child voice is NOT used.
Two mock paths exist instead:
  - MockASR: recording id -> canned transcript (unit-test path).
  - PlaybackASR: an ordered playlist of canned transcripts, standing in
    for playing back local test audio files (prototype voice-chain path).
    No audio file is read; the playlist *is* the test fixture.

A script is a list of (delay_seconds, text_or_None) turns. None means
the child stayed silent for that turn.
"""

from dataclasses import dataclass, field

from immersion_engine import (
    SESSION_SECONDS,
    DAILY_SOFT_CAP_SECONDS,
    ImmersionEngine,
)


class MockASR:
    """Test-only recogniser: recording id -> canned transcript.

    Unknown ids return "" (silence). No files, no network, no audio.
    """

    def __init__(self, mapping=None):
        self._mapping = dict(mapping or {})

    def transcribe(self, recording_id):
        return self._mapping.get(recording_id, "")


class PlaybackASR:
    """Local-playback stand-in: canned transcripts in playlist order.

    Models playing back local test audio files one by one. When the
    playlist runs out it returns "" (silence) instead of failing.
    """

    def __init__(self, playlist=None):
        self._playlist = list(playlist or [])
        self._pos = 0

    def transcribe_next(self):
        if self._pos >= len(self._playlist):
            return ""
        text = self._playlist[self._pos]
        self._pos += 1
        return text


@dataclass
class SessionReport:
    """What a session produced. Counts only -- no child speech stored."""

    duration_seconds: int = 0
    ended_reason: str = ""
    failed: bool = False  # a child session can never fail
    counts: dict = field(default_factory=dict)
    daily_seconds_used_after: int = 0
    sessions_today_after: int = 0
    replies: list = field(default_factory=list)  # engine replies only


def run_session(script, *, engine=None, daily_seconds_used=0,
                sessions_today=0, parent_override=False):
    """Run one scripted session. Returns a SessionReport.

    *script*: list of (delay_seconds, child_text_or_None).
    The session ends at 5 minutes, at a warm ending, or at the daily
    soft cap; it never runs past 5 minutes.
    """
    engine = engine or ImmersionEngine(
        daily_seconds_used=daily_seconds_used,
        sessions_today=sessions_today,
    )
    report = SessionReport()
    opening = engine.start(parent_override=parent_override)
    report.replies.append(opening)
    if opening.end:
        report.ended_reason = engine.end_reason or "soft_cap"
        report.daily_seconds_used_after = engine.daily_seconds_used
        report.sessions_today_after = engine.sessions_today
        report.counts = dict(engine.counts)
        return report

    for delay, child_text in script:
        if engine.ended:
            break
        reply = engine.respond(child_text, dt_seconds=delay)
        report.replies.append(reply)
        if engine.ended:
            break

    if not engine.ended:
        engine.end("script_done")
        report.replies.append(engine._closing_reply())
    report.duration_seconds = min(engine.elapsed_seconds, SESSION_SECONDS)
    report.ended_reason = engine.end_reason or "time_up"
    report.counts = dict(engine.counts)
    report.daily_seconds_used_after = engine.daily_seconds_used
    report.sessions_today_after = engine.sessions_today
    return report


# A default demo script: Chinese, silence, wrong English, good English,
# then a few more turns. Used by main() and by the tests.
DEMO_SCRIPT = [
    (10, "红苹果"),
    (10, None),
    (10, "red appel"),
    (10, "red apple"),
    (10, "big ball"),
    (10, "I like red apples"),
    (10, None),
    (10, "blue car"),
    (10, "The ball is big"),
    (10, "cat"),
]


def main():
    """Run the demo script and print a one-page summary."""
    report = run_session(DEMO_SCRIPT)
    print("session: %d seconds, ended: %s, failed: %s"
          % (report.duration_seconds, report.ended_reason, report.failed))
    print("counts: %s" % (report.counts,))
    print("daily seconds after: %d (soft cap %d)"
          % (report.daily_seconds_used_after, DAILY_SOFT_CAP_SECONDS))
    for reply in report.replies:
        print("[%s] %s" % (reply.action, reply.text))


if __name__ == "__main__":
    main()
