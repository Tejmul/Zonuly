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
