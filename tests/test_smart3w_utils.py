"""Smart3W utils 单元测试（无真实网络依赖）。"""

import json
import ssl
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import smart3w_utils as su  # noqa: E402

import smart3w_core as core  # noqa: E402


def _soup(html):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")


def test_img_markdown_protocol_relative():
    soup = _soup('<img src="//example.com/a.png">')
    assert core._img_markdown(soup.img) == "![](https://example.com/a.png)"


def test_img_markdown_skips_data_uri():
    soup = _soup('<img src="data:image/png;base64,AAAA">')
    assert core._img_markdown(soup.img) is None


def test_img_markdown_wechat_attrs_priority():
    soup = _soup('<img src="placeholder.png" data-src="real.jpg">')
    assert core._img_markdown(soup.img, core.WECHAT_IMAGE_ATTRS) == "![](real.jpg)"


def test_dedupe_parts_removes_duplicate_images_and_consecutive_text():
    parts = ["a", "a", "![](x)", "![](x)", "b"]
    assert core._dedupe_parts(parts) == "a\n\n![](x)\n\nb"


def test_compress_html_preserves_links_lists_table_code(tmp_path):
    html = """<html><head><title>T</title></head><body>
      <article>
        <p>Visit <a href="https://example.com">Example</a> now.</p>
        <ul><li>one</li><li>two</li></ul>
        <table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>
        <pre><code>print("hi")</code></pre>
        <p><img src="https://example.com/i.png"></p>
      </article>
    </body></html>"""
    src = tmp_path / "in.html"
    src.write_text(html, encoding="utf-8")
    out = tmp_path / "out.md"

    assert su.compress_html(str(src), str(out)) is True
    text = out.read_text(encoding="utf-8")
    assert "[Example](https://example.com)" in text
    assert "- one" in text and "- two" in text
    assert "| A | B |" in text
    assert "```" in text and 'print("hi")' in text
    assert "![](https://example.com/i.png)" in text


def test_compress_html_dedupes_duplicate_images(tmp_path):
    html = """<html><body><article>
      <p><img src="https://example.com/a.png"></p>
      <p><img src="https://example.com/a.png"></p>
    </article></body></html>"""
    src = tmp_path / "in.html"
    src.write_text(html, encoding="utf-8")
    out = tmp_path / "out.md"

    assert su.compress_html(str(src), str(out)) is True
    assert out.read_text(encoding="utf-8").count("![](https://example.com/a.png)") == 1


def test_compress_wechat_no_duplicate_paragraphs(tmp_path):
    html = """<html><body><div id="js_content">
      <section><p>第一段内容内容内容</p><p>第二段内容内容内容</p>
      <img data-src="//mmbiz.qpic.cn/x.jpg"></section>
    </div></body></html>"""
    src = tmp_path / "in.html"
    src.write_text(html, encoding="utf-8")
    out = tmp_path / "out.md"

    assert su.compress_wechat(str(src), str(out)) is True
    text = out.read_text(encoding="utf-8")
    assert text.count("第一段内容内容") == 1
    assert text.count("第二段内容内容") == 1
    assert "![](https://mmbiz.qpic.cn/x.jpg)" in text


def test_search_outputs_json(capsys, monkeypatch):
    class FakeResp:
        def read(self):
            return b'{"results": [{"title": "t", "url": "u", "content": "c"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        core.urllib.request,
        "urlopen",
        lambda req, context=None, timeout=None: FakeResp(),
    )
    su.cmd_search("q", 5, "https://searxng.example.com")
    data = json.loads(capsys.readouterr().out)
    assert data["success"] is True
    assert data["result_count"] == 1
    assert data["results"][0]["snippet"] == "c"


def test_ssl_context_verifies_by_default(monkeypatch):
    monkeypatch.delenv("SMART3W_SSL_VERIFY", raising=False)
    ctx = core._ssl_context()
    assert ctx.verify_mode != ssl.CERT_NONE
    assert ctx.check_hostname is True


def test_ssl_context_can_be_disabled(monkeypatch):
    monkeypatch.setenv("SMART3W_SSL_VERIFY", "0")
    ctx = core._ssl_context()
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


class _FakeResp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_search_passes_params_and_enriches_results(capsys, monkeypatch):
    captured = {}

    def fake_urlopen(req, context=None, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp(b'{"results": [{"title": "t", "url": "https://example.com/a?utm_source=x", '
                         b'"content": "c", "score": 0.9, "engines": ["google"], "positions": [1]}]}')

    monkeypatch.setattr(core.urllib.request, "urlopen", fake_urlopen)
    su.cmd_search("q", 5, "https://a.example", language="en-US", time_range="week",
                  categories="news", safesearch=2, engines="google,bing")
    data = json.loads(capsys.readouterr().out)

    assert data["success"] is True
    assert "language=en-US" in captured["url"]
    assert "time_range=week" in captured["url"]
    assert "categories=news" in captured["url"]
    assert "safesearch=2" in captured["url"]
    assert "engines=google%2Cbing" in captured["url"]
    assert data["instance"] == "https://a.example"
    assert data["results"][0]["score"] == 0.9
    assert data["results"][0]["engines"] == ["google"]
    assert data["results"][0]["positions"] == [1]


def test_search_passes_through_metadata(capsys, monkeypatch):
    body = {
        "results": [{"title": "t", "url": "https://example.com/a", "content": "c"}],
        "answers": ["答案"],
        "corrections": ["corrected"],
        "suggestions": ["s1", "s2"],
        "infoboxes": [{"infobox": "box", "content": "content"}],
    }

    def fake_urlopen(req, context=None, timeout=None):
        return _FakeResp(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(core.urllib.request, "urlopen", fake_urlopen)
    su.cmd_search("q", 5, "https://a.example")
    data = json.loads(capsys.readouterr().out)

    assert data["success"] is True
    assert data["answers"] == ["答案"]
    assert data["corrections"] == ["corrected"]
    assert data["suggestions"] == ["s1", "s2"]
    assert data["infoboxes"][0]["infobox"] == "box"


def test_search_omits_empty_metadata(capsys, monkeypatch):
    def fake_urlopen(req, context=None, timeout=None):
        return _FakeResp(b'{"results": [{"title": "t", "url": "https://example.com/a", "content": "c"}]}')

    monkeypatch.setattr(core.urllib.request, "urlopen", fake_urlopen)
    su.cmd_search("q", 5, "https://a.example")
    data = json.loads(capsys.readouterr().out)

    assert data["success"] is True
    for key in ("answers", "corrections", "suggestions", "infoboxes"):
        assert key not in data


def test_cmd_search_returns_false_on_validation_error(capsys):
    assert su.cmd_search("", 5, "https://a.example") is False


def test_merge_results_handles_mixed_engine_types():
    merged = {}
    core._merge_results(merged, [
        {"url": "https://example.com/a", "engines": ["google"]},
        {"url": "https://example.com/a", "engines": [1]},
    ])
    assert merged["https://example.com/a"]["engines"] == ["1", "google"]


def test_search_web_rejects_bool_safesearch():
    result = core.search_web("q", 5, "https://a.example", safesearch=True)
    assert result["success"] is False
    assert "safesearch" in result["error"]


def test_search_web_handles_non_string_instance(monkeypatch):
    monkeypatch.setenv("SEARXNG_INSTANCE", "not-a-url")
    result = core.search_web("q", 5, 123)
    assert result["success"] is False


def test_parse_sitemap_rejects_non_integer_max_urls():
    with pytest.raises(core.SitemapError):
        core.parse_sitemap("https://example.com/sitemap.xml", "5")


def test_search_paginates_until_count(capsys, monkeypatch):
    calls = []

    def fake_urlopen(req, context=None, timeout=None):
        url = req.full_url
        page = 2 if "pageno=2" in url else 1
        calls.append(page)
        start = 0 if page == 1 else 10
        size = 10 if page == 1 else 5
        results = [
            {
                "title": f"t{index}",
                "url": f"https://example.com/{index}",
                "content": "c",
            }
            for index in range(start, start + size)
        ]
        return _FakeResp(json.dumps({"results": results}).encode("utf-8"))

    monkeypatch.setattr(core.urllib.request, "urlopen", fake_urlopen)
    su.cmd_search("q", 15, "https://a.example")
    data = json.loads(capsys.readouterr().out)

    assert data["result_count"] == 15
    assert calls == [1, 2]


def test_sitemap_recursive_expands_index(capsys, monkeypatch):
    index = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://example.com/child.xml</loc></sitemap></sitemapindex>"
    )
    child = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>https://example.com/a</loc></url></urlset>"
    )

    def fake_urlopen(req, *args, **kwargs):
        url = req.full_url
        body = index if url.endswith("sitemap.xml") else child
        return _SitemapResp(body, url=url)

    monkeypatch.setattr(core.urllib.request, "urlopen", fake_urlopen)
    su.cmd_sitemap("https://example.com/sitemap.xml", 10, recursive=True)
    out = capsys.readouterr().out

    assert "https://example.com/a" in out
    assert "📋" not in out


def test_sitemap_skips_failed_child(capsys, monkeypatch):
    index = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://example.com/child.xml</loc></sitemap></sitemapindex>"
    )

    def fake_urlopen(req, *args, **kwargs):
        url = req.full_url
        if url.endswith("child.xml"):
            raise OSError("child down")
        return _SitemapResp(index, url=url)

    monkeypatch.setattr(core.urllib.request, "urlopen", fake_urlopen)
    su.cmd_sitemap("https://example.com/sitemap.xml", 10, recursive=True)
    out = capsys.readouterr().out

    assert "已跳过" in out


def test_sitemap_blocks_private_target(capsys):
    with pytest.raises(SystemExit) as excinfo:
        su.cmd_sitemap("http://127.0.0.1/sitemap.xml", 10)

    assert excinfo.value.code == 1
    assert "拦截" in capsys.readouterr().err


def test_sitemap_blocks_redirect_to_private(capsys, monkeypatch):
    class FakeSitemapResp:
        def __init__(self):
            self._body = (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
            )

        def read(self):
            return self._body

        def geturl(self):
            return "http://127.0.0.1/sitemap.xml"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(core.urllib.request, "urlopen", lambda *a, **k: FakeSitemapResp())
    with pytest.raises(SystemExit) as excinfo:
        su.cmd_sitemap("http://example.com/sitemap.xml", 10)

    assert excinfo.value.code == 1
    assert "拦截" in capsys.readouterr().err


def test_search_falls_back_to_backup_instance(capsys, monkeypatch):
    calls = []

    def fake_urlopen(req, context=None, timeout=None):
        calls.append(req.full_url)
        if req.full_url.startswith("https://a.example"):
            raise OSError("primary instance down")
        return _FakeResp(b'{"results": [{"title": "t", "url": "https://example.com/x", "content": "c"}]}')

    monkeypatch.setenv("SEARXNG_INSTANCES", "https://b.example, https://a.example")
    monkeypatch.setattr(core.urllib.request, "urlopen", fake_urlopen)
    su.cmd_search("q", 5, "https://a.example")
    data = json.loads(capsys.readouterr().out)

    assert data["success"] is True
    assert data["instance"] == "https://b.example"
    assert len(calls) == 2


def test_search_dedupes_results_by_normalized_url(capsys, monkeypatch):
    body = b'{"results": [{"title": "t1", "url": "https://example.com/a?utm_source=x", "content": "c1", "engines": ["google"]}, {"title": "t2", "url": "https://example.com/a", "content": "c2", "engines": ["bing"], "score": 0.8}]}'
    monkeypatch.setattr(
        core.urllib.request, "urlopen",
        lambda req, context=None, timeout=None: _FakeResp(body),
    )
    su.cmd_search("q", 5, "https://a.example")
    data = json.loads(capsys.readouterr().out)

    assert data["result_count"] == 1
    assert data["results"][0]["title"] == "t1"
    assert sorted(data["results"][0]["engines"]) == ["bing", "google"]


def test_search_validates_safesearch(capsys, monkeypatch):
    su.cmd_search("q", 5, "https://a.example", safesearch=3)
    data = json.loads(capsys.readouterr().out)
    assert data["success"] is False
    assert "safesearch" in data["error"]


def test_compress_html_does_not_duplicate_list_paragraphs(tmp_path):
    html = """<html><body><article>
      <ul><li><p>one</p></li><li><p>two</p></li></ul>
    </article></body></html>"""
    src = tmp_path / "in.html"
    src.write_text(html, encoding="utf-8")
    out = tmp_path / "out.md"

    assert su.compress_html(str(src), str(out)) is True
    text = out.read_text(encoding="utf-8")
    assert text.count("one") == 1
    assert text.count("two") == 1
    assert "- one" in text


def test_compress_html_does_not_duplicate_table_cells(tmp_path):
    html = """<html><body><article>
      <p>intro</p>
      <table><tr><th><p>A</p></th><th><p>B</p></th></tr>
      <tr><td><p>1</p></td><td><p>2</p></td></tr></table>
    </article></body></html>"""
    src = tmp_path / "in.html"
    src.write_text(html, encoding="utf-8")
    out = tmp_path / "out.md"

    assert su.compress_html(str(src), str(out)) is True
    text = out.read_text(encoding="utf-8")
    assert text.count("A") == 1
    assert text.count("1") == 1
    assert "| A | B |" in text


def test_extract_blocks_nested_list_indentation():
    soup = _soup("<article><ul><li>parent<ul><li>child</li></ul></li></ul></article>")
    assert core._extract_blocks(soup) == ["- parent", "  - child"]


class _SitemapResp:
    def __init__(self, body, url="https://example.com/sitemap.xml"):
        self._body = body
        self._url = url

    def read(self, n=-1):
        return self._body if n < 0 else self._body[:n]

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_sitemap_index_marks_child_sitemaps(capsys, monkeypatch):
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://example.com/sm1.xml</loc></sitemap></sitemapindex>"
    )
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda *a, **k: _SitemapResp(xml))
    su.cmd_sitemap("https://example.com/sitemap.xml", 10)

    out = capsys.readouterr().out
    assert "📋" in out
    assert "[index]" not in out
    assert "https://example.com/sm1.xml" in out


def test_sitemap_without_namespace(capsys, monkeypatch):
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b"<urlset><url><loc>https://example.com/a</loc></url></urlset>"
    )
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda *a, **k: _SitemapResp(xml))
    su.cmd_sitemap("https://example.com/sitemap.xml", 10)

    assert "https://example.com/a" in capsys.readouterr().out


def test_search_normalizes_engine_string_fields(capsys, monkeypatch):
    body = (
        b'{"results": [{"title": "t", "url": "https://example.com/a", "content": "c", '
        b'"engines": "google", "positions": 1}]}'
    )
    monkeypatch.setattr(
        core.urllib.request, "urlopen", lambda *a, **k: _FakeResp(body)
    )
    su.cmd_search("q", 5, "https://a.example")
    data = json.loads(capsys.readouterr().out)

    assert data["results"][0]["engines"] == ["google"]
    assert data["results"][0]["positions"] == [1]


def test_search_ignores_non_dict_results(capsys, monkeypatch):
    body = b'{"results": [null, {"title": "t", "url": "https://example.com/a", "content": "c"}]}'
    monkeypatch.setattr(
        core.urllib.request, "urlopen", lambda *a, **k: _FakeResp(body)
    )
    su.cmd_search("q", 5, "https://a.example")
    data = json.loads(capsys.readouterr().out)

    assert data["success"] is True
    assert data["result_count"] == 1
