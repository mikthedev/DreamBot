"""Unit tests for Spotify→YouTube/SoundCloud match scoring."""

from __future__ import annotations

import music


def test_rejects_cover_unless_requested() -> None:
    want = dict(title="Blinding Lights", artist="The Weeknd", duration=200)
    cover = {
        "title": "Blinding Lights (Cover)",
        "uploader": "Some Singer",
        "duration": 200,
    }
    assert music._bridge_candidate_score(cover, **want) < 0

    asked = dict(title="Blinding Lights (Cover)", artist="Some Singer", duration=200)
    assert music._bridge_candidate_score(cover, **asked) >= 0


def test_rejects_remix_unless_requested() -> None:
    want = dict(title="Starboy", artist="The Weeknd", duration=230)
    remix = {
        "title": "The Weeknd - Starboy (Kygo Remix)",
        "uploader": "Kygo",
        "duration": 230,
    }
    assert music._bridge_candidate_score(remix, **want) < 0

    asked = dict(title="Starboy (Kygo Remix)", artist="The Weeknd", duration=230)
    assert music._bridge_candidate_score(remix, **asked) >= 0


def test_rejects_live_and_karaoke() -> None:
    want = dict(title="Blinding Lights", artist="The Weeknd", duration=200)
    live = {
        "title": "The Weeknd - Blinding Lights (Live On The 2020 MTV VMAs)",
        "uploader": "The Weeknd",
        "duration": 199,
    }
    karaoke = {
        "title": "Blinding Lights Karaoke",
        "uploader": "Karaoke Version",
        "duration": 200,
    }
    assert music._bridge_candidate_score(live, **want) < 0
    assert music._bridge_candidate_score(karaoke, **want) < 0


def test_rejects_30s_preview() -> None:
    want = dict(title="Some Hit", artist="Artist", duration=210)
    preview = {"title": "Some Hit", "uploader": "Artist", "duration": 30}
    assert music._bridge_candidate_score(preview, **want) < 0
    assert music._is_preview_clip(30, 210) is True
    assert music._is_preview_clip(30, 28) is False


def test_rejects_far_duration() -> None:
    want = dict(title="Blinding Lights", artist="The Weeknd", duration=200)
    long_video = {
        "title": "The Weeknd - Blinding Lights (Official Video)",
        "uploader": "The Weeknd",
        "duration": 263,
    }
    assert music._bridge_candidate_score(long_video, **want) < 0


def test_prefers_official_audio() -> None:
    want = dict(title="Blinding Lights", artist="The Weeknd", duration=200)
    official = {
        "title": "The Weeknd - Blinding Lights (Official Audio)",
        "uploader": "The Weeknd",
        "duration": 204,
    }
    topic = {
        "title": "Blinding Lights",
        "uploader": "The Weeknd - Topic",
        "duration": 200,
    }
    assert music._bridge_candidate_score(official, **want) >= music._BRIDGE_MIN_ACCEPT_SCORE
    assert music._bridge_candidate_score(topic, **want) >= music._BRIDGE_MIN_ACCEPT_SCORE


def test_recovery_not_flagged_as_cover() -> None:
    # Regression: naive 'cover' substring used to hit 'recovery'
    want = dict(title="Recovery", artist="Eminem", duration=284)
    ok = {
        "title": "Eminem - Recovery (Official Audio)",
        "uploader": "EminemMusic",
        "duration": 284,
    }
    assert music._bridge_candidate_score(ok, **want) >= music._BRIDGE_MIN_ACCEPT_SCORE


def test_rejects_unrequested_pianoforte_parenthetical() -> None:
    want = dict(title="Never Gonna Give You Up", artist="Rick Astley", duration=213)
    piano = {
        "title": "Rick Astley - Never Gonna Give You Up (Pianoforte) (Official Audio)",
        "uploader": "Rick Astley",
        "duration": 209,
    }
    assert music._bridge_candidate_score(piano, **want) < 0


def test_keeps_requested_piano_man() -> None:
    want = dict(title="Piano Man", artist="Billy Joel", duration=339)
    ok = {
        "title": "Billy Joel - Piano Man (Official HD Video)",
        "uploader": "Billy Joel",
        "duration": 339,
    }
    # HD video may fail duration elsewhere; here durations match.
    assert music._bridge_candidate_score(ok, **want) >= music._BRIDGE_MIN_ACCEPT_SCORE


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all passed")
