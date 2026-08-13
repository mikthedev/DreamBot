from game_search import (
    GameHit,
    is_video_game_page,
    merge_game_hits,
    store_link_label,
    store_link_markdown,
)


def test_is_video_game_page():
    assert is_video_game_page(
        "2011 video game",
        "Minecraft is a sandbox game developed by Mojang.",
        "Minecraft",
    )
    assert not is_video_game_page(
        "2025 film directed by Jared Hess",
        "A Minecraft Movie is a film based on the video game.",
        "A Minecraft Movie",
    )
    assert not is_video_game_page(
        "Media franchise",
        "Minecraft is a video game franchise.",
        "Minecraft (franchise)",
    )


def test_merge_prefers_exact_wiki_over_related_steam():
    steam = [
        GameHit("Minecraft Dungeons", "https://store.steampowered.com/app/1", "steam"),
        GameHit("Minecraft Legends", "https://store.steampowered.com/app/2", "steam"),
    ]
    wiki = [
        GameHit(
            "Minecraft",
            "https://en.wikipedia.org/wiki/Minecraft",
            "wikipedia",
            snippet="2011 video game",
        )
    ]
    merged = merge_game_hits(steam, wiki, "Minecraft")
    assert merged[0].name == "Minecraft"
    assert merged[0].source == "wikipedia"
    names = [h.name for h in merged]
    assert "Minecraft Dungeons" in names


def test_merge_steam_wins_same_title():
    steam = [GameHit("Among Us", "https://store.steampowered.com/app/945360", "steam")]
    wiki = [GameHit("Among Us", "https://en.wikipedia.org/wiki/Among_Us", "wikipedia")]
    merged = merge_game_hits(steam, wiki, "Among Us")
    assert len(merged) == 1
    assert merged[0].source == "steam"


def test_drop_steam_addons_keeps_sequels():
    from game_search import _drop_steam_addons

    hits = _drop_steam_addons(
        [
            GameHit("Minecraft Dungeons", "u", "steam"),
            GameHit("Minecraft Dungeons II", "u", "steam"),
            GameHit("Minecraft Dungeons Echoing Void", "u", "steam"),
            GameHit("Minecraft Dungeons Jungle Awakens", "u", "steam"),
        ]
    )
    names = [h.name for h in hits]
    assert names == ["Minecraft Dungeons", "Minecraft Dungeons II"]


def test_store_link_label():
    assert store_link_label("https://store.steampowered.com/app/730") == "Steam"
    assert store_link_label("https://en.wikipedia.org/wiki/Minecraft") == "About"
    assert store_link_markdown(None) == ""
    assert store_link_markdown("") == ""
    assert "Steam" in store_link_markdown("https://store.steampowered.com/app/730")


if __name__ == "__main__":
    test_is_video_game_page()
    test_merge_prefers_exact_wiki_over_related_steam()
    test_merge_steam_wins_same_title()
    test_drop_steam_addons_keeps_sequels()
    test_store_link_label()
    print("ok")
