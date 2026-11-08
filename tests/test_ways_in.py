"""Ways in: the public rooms, repos and pages that do not need an email address.

2026-11-08: OpenHands had zero open postings, so the careers page was a dead end and the only
doors left were their Slack and their good-first-issues. That is the case this stage exists for.
"""

from __future__ import annotations

from jobhunter import ways_in


def test_a_badge_image_is_not_a_slack_invite():
    """A README's Slack button is two links: the shields.io image and the invite. Only one is a
    way in, and storing the image would give him a link that opens a PNG."""
    md = ("[![Slack](https://img.shields.io/badge/Slack-Join-611f69?logo=slack)]"
          "(https://go.openhands.dev/slack)")
    found = {k: u for k, u, _ in ways_in.links_in(md, source="readme")}
    assert found["slack"] == "https://go.openhands.dev/slack"
    assert "shields.io" not in str(found)


def test_the_links_worth_keeping_are_recognised():
    text = """
    https://discord.gg/abc123   https://github.com/Acme/acme/discussions
    https://docs.acme.dev/start https://x.com/acmehq
    https://www.linkedin.com/company/acme   https://discuss.acme.dev/c/jobs
    """
    kinds = {k for k, _u, _s in ways_in.links_in(text, source="site")}
    assert kinds == {"discord", "discussions", "docs", "x", "linkedin", "forum"}


def test_the_vendors_own_pages_are_not_ways_in():
    """slack.com/downloads is Slack's marketing, not a room belonging to the company."""
    text = ("https://slack.com/downloads/mac https://discord.com/developers/docs "
            "https://x.com/intent/tweet?text=hi https://linkedin.com/company/linkedin")
    assert ways_in.links_in(text, source="site") == []


def test_a_docs_deep_link_is_stored_as_the_docs_site():
    """A README citing ten doc pages should leave one row, not ten: the destination is the same."""
    assert ways_in.canonical("docs", "https://docs.acme.dev/a/b/c#x") == "https://docs.acme.dev"
    assert ways_in.canonical("forum", "https://discuss.acme.dev/t/1") == "https://discuss.acme.dev"
    # a repo is NOT folded — each one is a different place
    assert ways_in.canonical("repo", "https://github.com/Acme/two") == "https://github.com/Acme/two"


def test_slack_outranks_a_social_link():
    """The order is the judgement: a room where maintainers answer beats a marketing feed."""
    assert ways_in.KINDS["slack"][1] > ways_in.KINDS["x"][1]
    assert ways_in.KINDS["good_first_issue"][1] > ways_in.KINDS["docs"][1]


def test_the_module_is_not_the_channel_health_one():
    """Two things called "channel" in one package is how a Slack link ends up logged as an
    outage. `jobhunter.channels` records research-channel health and must keep `record`."""
    from jobhunter import channels

    assert hasattr(channels, "record")
    assert not hasattr(channels, "collect")
    assert hasattr(ways_in, "collect")
