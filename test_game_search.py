from game_search import (
    GameHit,
    is_video_game_page,
    merge_game_hits,
    prefer_official_url,
    steam_title_matches,
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
    assert store_link_label("https://www.minecraft.net/") == "Website"
    assert store_link_label("https://minecraft.net") == "Website"
    assert store_link_label("https://genshin.hoyoverse.com/") == "Website"
    assert store_link_markdown(None) == ""
    assert store_link_markdown("") == ""
    assert "Steam" in store_link_markdown("https://store.steampowered.com/app/730")


def test_format_steam_ua_price():
    from game_search import format_steam_ua_price, steam_price_markdown

    assert format_steam_ua_price({"is_free": True}) == "**Free**"
    assert format_steam_ua_price(None) == ""
    assert format_steam_ua_price({"price_overview": {"currency": "USD", "final_formatted": "$1"}}) == ""
    sale = format_steam_ua_price(
        {
            "price_overview": {
                "currency": "UAH",
                "final_formatted": "79₴",
                "initial_formatted": "159₴",
                "discount_percent": 50,
            }
        }
    )
    assert "79₴" in sale and "159₴" in sale and "50%" in sale
    assert "on Steam" not in sale
    plain = format_steam_ua_price(
        {
            "price_overview": {
                "currency": "UAH",
                "final_formatted": "449₴",
                "initial_formatted": "449₴",
                "discount_percent": 0,
            }
        }
    )
    assert plain == "**449₴**"
    linked = steam_price_markdown(
        "**194₴** on Steam (UA)",
        "https://store.steampowered.com/app/730",
    )
    assert linked == "[**194₴**](https://store.steampowered.com/app/730)"
    assert steam_price_markdown("**Free**", None) == "**Free**"


def test_steam_cdn_art():
    from game_search import steam_app_id_from_url, steam_cdn_art, steam_cdn_candidates

    assert steam_app_id_from_url("https://store.steampowered.com/app/3527290/PEAK/") == 3527290
    art = steam_cdn_art(3527290)
    assert "3527290" in (art.icon_url or "")
    assert art.image_url and "header.jpg" in art.image_url
    portraits, banners = steam_cdn_candidates(3527290)
    assert any("library_hero.jpg" in u for u in banners)
    assert all("capsule_231" not in u for u in portraits)


def test_known_official_fallback():
    from game_search import _KNOWN_OFFICIAL, _bare_key

    assert _KNOWN_OFFICIAL[_bare_key("Minecraft")].startswith("https://")
    assert "hoyoverse" in _KNOWN_OFFICIAL[_bare_key("Genshin Impact")]


def test_fill_play_template():
    from play_together import _fill_play_template

    out = _fill_play_template(
        "Hello {game}\n{link}\n{note}\nBye",
        {"game": "PEAK", "link": "", "note": ""},
    )
    assert out.startswith("Hello PEAK")
    assert out.endswith("Bye")
    assert "{link}" not in out
    assert "{note}" not in out


def test_steam_title_matches():
    assert steam_title_matches("Overwatch", "Overwatch 2")
    assert steam_title_matches("PEAK", "PEAK")
    assert steam_title_matches("Big Walk", "The Big Walk")
    assert not steam_title_matches("Minecraft", "Minecraft Dungeons")
    assert not steam_title_matches("Minecraft", "Minecraft Legends")
    assert not steam_title_matches("Genshin Impact", "Genshin Impact Soundtrack")


def test_prefer_official_url():
    assert (
        prefer_official_url(
            [
                "https://en.wikipedia.org/wiki/Minecraft",
                "https://www.minecraft.net/en-us",
                "https://www.minecraft.net/",
            ]
        )
        == "https://www.minecraft.net/"
    )


if __name__ == "__main__":
    test_is_video_game_page()
    test_merge_prefers_exact_wiki_over_related_steam()
    test_merge_steam_wins_same_title()
    test_drop_steam_addons_keeps_sequels()
    test_store_link_label()
    test_format_steam_ua_price()
    test_steam_cdn_art()
    test_known_official_fallback()
    test_steam_title_matches()
    test_prefer_official_url()
    test_fill_play_template()
    print("ok")
