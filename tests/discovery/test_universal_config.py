from textwrap import dedent

from agentic_jobs.services.discovery.universal.sites_config import load_universal_sites_config


def test_load_universal_sites_config(tmp_path, monkeypatch):
    config_path = tmp_path / "sites.yaml"
    config_path.write_text(
        dedent(
            """
            sites:
              - site_slug: apple
                display_name: Apple Careers
                crawl_interval_minutes: 90
                feeds:
                  - parser: workday
                    options:
                      host: jobs.apple.com
                      tenant: apple
                      site: en-us
              - site_slug: meta
                display_name: Meta Careers
                feeds:
                  - feed_slug: professional
                    parser: lever
                    options:
                      company: meta
              - site_slug: openai
                display_name: OpenAI Careers
                feeds:
                  - site_url: https://openai.com/careers
            """
        ),
        encoding="utf-8",
    )

    config = load_universal_sites_config(str(config_path))
    assert len(config.feeds) == 3
    apple_feed = config.get_feed("apple:default")
    assert apple_feed is not None
    assert apple_feed.parser == "workday"
    assert apple_feed.crawl_interval_minutes == 90
    meta_feed = config.get_feed("meta:professional")
    assert meta_feed is not None
    assert meta_feed.parser == "lever"
    assert meta_feed.options["company"] == "meta"
    openai_feed = config.get_feed("openai:default")
    assert openai_feed is not None
    assert openai_feed.parser is None
    assert openai_feed.site_url == "https://openai.com/careers"
    assert openai_feed.requires_detection is True
