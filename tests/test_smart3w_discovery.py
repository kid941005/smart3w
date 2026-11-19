"""smart3w_discovery 单元测试：链接发现、路径过滤与结构化抽取（无网络）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smart3w_discovery import (  # noqa: E402
    extract_fields,
    extract_links,
    parse_robots_sitemaps,
    path_allowed,
    same_site,
)


def test_same_site_allows_subdomains_by_default():
    assert same_site("https://a.example.com/x", "https://example.com/") is True
    assert same_site("https://other.com/x", "https://example.com/") is False
    assert same_site("https://a.example.com/x", "https://example.com/", include_subdomains=False) is False


def test_path_allowed_supports_prefix_and_glob():
    assert path_allowed("/docs/a", "/docs") is True
    assert path_allowed("/docs/a", "/docs/*") is True
    assert path_allowed("/blog/a", "/docs") is False
    assert path_allowed("/private/a", "", "/private/*") is False


def test_extract_links_filters_and_resolves_relative_urls():
    html = """
    <a href="/a">A</a>
    <a href="https://example.com/b#section">B</a>
    <a href="https://other.com/c">C</a>
    <a href="javascript:void(0)">bad</a>
    """
    links = extract_links(html, "https://example.com/", path_exclude="/b")
    assert [item["url"] for item in links] == ["https://example.com/a"]


def test_parse_robots_sitemaps_dedupes():
    text = "User-agent: *\nSitemap: /sitemap.xml\nsitemap: https://example.com/sitemap.xml\n"
    assert parse_robots_sitemaps(text, "https://example.com/") == [
        "https://example.com/sitemap.xml"
    ]


def test_extract_fields_css_attr_and_all():
    html = '<h1>Hello</h1><a href="/x">X</a><a href="/y">Y</a>'
    result = extract_fields(
        html,
        {
            "title": "h1",
            "links": {"selector": "a", "attr": "href", "all": True},
            "missing": "p.none",
        },
    )
    assert result["data"]["title"] == "Hello"
    assert result["data"]["links"] == ["/x", "/y"]
    assert result["data"]["missing"] is None
    assert result["missing"] == ["missing"]


def test_extract_fields_xpath_and_scalar():
    html = "<div><h2>Title</h2><span>3</span></div>"
    result = extract_fields(
        html,
        {
            "title": {"xpath": "//h2/text()"},
            "count": {"xpath": "count(//span)"},
        },
        mode="xpath",
    )
    assert result["data"]["title"] == "Title"
    assert result["data"]["count"] == "1.0"
