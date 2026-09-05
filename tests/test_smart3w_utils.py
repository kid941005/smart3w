"""Smart3W utils 单元测试（无真实网络依赖）。"""

import json
import ssl
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import smart3w_utils as su  # noqa: E402


def _soup(html):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")


def test_img_markdown_protocol_relative():
    soup = _soup('<img src="//example.com/a.png">')
    assert su._img_markdown(soup.img) == "![](https://example.com/a.png)"


def test_img_markdown_wechat_attrs_priority():
    soup = _soup('<img src="placeholder.png" data-src="real.jpg">')
    assert su._img_markdown(soup.img, su.WECHAT_IMAGE_ATTRS) == "![](real.jpg)"


def test_dedupe_parts_removes_duplicate_images_and_consecutive_text():
    parts = ["a", "a", "![](x)", "![](x)", "b"]
    assert su._dedupe_parts(parts) == "a\n\n![](x)\n\nb"


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
        su.urllib.request,
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
    ctx = su._ssl_context()
    assert ctx.verify_mode != ssl.CERT_NONE
    assert ctx.check_hostname is True


def test_ssl_context_can_be_disabled(monkeypatch):
    monkeypatch.setenv("SMART3W_SSL_VERIFY", "0")
    ctx = su._ssl_context()
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False
