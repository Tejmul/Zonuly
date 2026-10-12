"""web._parse_jina_ddg: DuckDuckGo's results page as Jina Reader renders it."""

from __future__ import annotations

from jobhunter.research import web

PAGE = """Title: Namespace Series B at DuckDuckGo

Markdown Content:
[](https://html.duckduckgo.com/html/?q=x)

## [Developer infrastructure startup Namespace raises $42M - Axios](https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.axios.com%2Fpro%2Fnamespace%2Dscale&rut=ab)

[![Image 1](https://external-content.duckduckgo.com/ip3/www.axios.com.ico)](https://duckduckgo.com/l/?uddg=x)[www.axios.com/pro/namespace-scale](https://duckduckgo.com/l/?uddg=x) 2026-10-05T00:00:00.0000000

[**Namespace**, developer infrastructure company, raised a $42 million **Series B** led by Scale.](https://duckduckgo.com/l/?uddg=x)

## [Ad: cheap cloud](https://duckduckgo.com/y.js?ad_domain=example.com)

## [Zurich's Namespace raises $42M Series B](https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.afroeuropa.com%2Farticle%2Fnamespace&rut=cd)
"""


def test_parses_title_url_date_and_snippet_and_skips_ads():
    rows = web._parse_jina_ddg(PAGE, limit=8)
    assert [r.url for r in rows] == ["https://www.axios.com/pro/namespace-scale",
                                     "https://www.afroeuropa.com/article/namespace"]
    first = rows[0]
    assert first.title.startswith("Developer infrastructure startup Namespace")
    assert first.published == "2026-10-05"
    assert first.snippet == "Namespace, developer infrastructure company, raised a $42 million Series B led by Scale."
    assert first.source == "jina-ddg"


def test_limit_is_respected():
    assert len(web._parse_jina_ddg(PAGE, limit=1)) == 1


def test_adjacent_bold_runs_keep_their_space_and_entities_decode():
    assert web._unbold("**developer****infrastructure** &amp; cloud") == "developer infrastructure & cloud"


SP_PAGE = """Markdown Content:
Ads
[How to Build Voice AI Agents](https://bridge.pdx1.admarketplace.net/ct?x=1)
## [Sponsored thing](https://bridge.pdx1.admarketplace.net/ct?y=2)
Web results
[![Image 6: favicon](blob:http://localhost/x)](https://www.ringg.ai/blog/series-a)
[Ringg AI](https://www.ringg.ai/blog/series-a)[https://www.ringg.ai/blog/series-a https://www.ringg.ai›blog](https://www.ringg.ai/blog/series-a)
## [Ringg AI Funding: $5.5M Series A for Voice AI Agents](https://www.ringg.ai/blog/series-a)
Jul 6, 2026 **...** Today, we're announcing **Ringg AI's** $5.5M Series A **funding** round, led by Arkam Ventures.
[Visit in Anonymous View](https://us3-browse.startpage.com/av/proxy?ep=1)
## [Ringg AI Raises $5.5M from Arkam Ventures](https://www.linkedin.com/posts/aim_ringg-ai-raised)
Ringg AI has raised $5.5 million (₹48 crore) in Series A.
"""


def test_startpage_skips_ads_reads_date_and_snippet():
    rows = web._parse_jina_startpage(SP_PAGE, limit=8)
    assert [r.url for r in rows] == ["https://www.ringg.ai/blog/series-a",
                                     "https://www.linkedin.com/posts/aim_ringg-ai-raised"]
    assert rows[0].published == "2026-07-06"
    assert rows[0].snippet.startswith("Today, we're announcing Ringg AI's $5.5M Series A funding round")
    assert rows[1].published is None and rows[1].source == "jina-startpage"


def test_search_is_down_only_when_every_keyless_backend_cools(monkeypatch):
    import time as _t

    monkeypatch.setattr(web, "mcp_cooling", lambda: True)
    for st in web._kl_state.values():
        st["cool_until"] = _t.monotonic() + 60
    assert web.search_down()
    web._kl_state["jina-startpage"]["cool_until"] = 0.0
    assert not web.search_down()
    for st in web._kl_state.values():
        st["cool_until"] = 0.0
