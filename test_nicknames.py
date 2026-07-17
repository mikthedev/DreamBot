from nicknames import build_nickname


def test_build_nickname_basic():
    assert build_nickname("MikeGTC", "Миша") == "MikeGTC (Миша)"


def test_build_nickname_truncates_base():
    long_name = "A" * 40
    nick = build_nickname(long_name, "Миша")
    assert len(nick) <= 32
    assert nick.endswith(" (Миша)")
