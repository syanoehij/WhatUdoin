"""핀포인트 단위 테스트 - HTML 표를 Markdown 표로 내보낼 때 셀 내부 목록 보존.

실행:
    "D:\\Program Files\\Python\\Python312\\python.exe" -m pytest tests/test_html_table_to_gfm.py -v
"""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import _convert_html_tables_to_gfm, _html_table_to_gfm, _normalize_markdown_for_export  # noqa: E402


def _gfm_data_cell(gfm: str, row_index: int = 0, cell_index: int = 0) -> str:
    """GFM 표에서 데이터 행/셀 내용 반환."""
    lines = gfm.splitlines()
    assert len(lines) >= 3
    row = lines[2 + row_index].strip().strip("|")
    cells = []
    buf = []
    escaped = False
    for ch in row:
        if ch == "|" and not escaped:
            cells.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
        escaped = ch == "\\" and not escaped
        if ch != "\\":
            escaped = False
    cells.append("".join(buf).strip())
    return cells[cell_index]


def test_basic_ul_converts_to_gfm_cell_markers():
    html = (
        "<table>"
        "<tr><th>H</th></tr>"
        "<tr><td><ul><li><p>A</p></li><li><p>B</p></li></ul></td></tr>"
        "</table>"
    )
    out = _html_table_to_gfm(html)
    assert out is not None
    assert "<table" not in out
    assert _gfm_data_cell(out) == "- A<br>- B"


def test_basic_ol_converts_to_gfm_cell_markers():
    html = (
        "<table>"
        "<tr><th>H</th></tr>"
        "<tr><td><ol><li>A</li><li>B</li></ol></td></tr>"
        "</table>"
    )
    out = _html_table_to_gfm(html)
    assert out is not None
    assert _gfm_data_cell(out) == "1. A<br>2. B"


def test_task_list_converts_to_gfm_checkboxes():
    html = (
        "<table>"
        "<tr><th>H</th></tr>"
        "<tr><td>"
        '<ul data-type="taskList">'
        '<li data-type="taskItem" data-checked="true"><p>Done</p></li>'
        '<li data-type="taskItem" data-checked="false"><p>Todo</p></li>'
        "</ul>"
        "</td></tr>"
        "</table>"
    )
    out = _html_table_to_gfm(html)
    assert out is not None
    assert _gfm_data_cell(out) == "- [x] Done<br>- [ ] Todo"


def test_convert_html_tables_to_gfm_no_longer_keeps_list_table_html():
    html = (
        "before\n\n"
        "<table>"
        "<tr><th>Todo</th></tr>"
        "<tr><td><ul><li>One</li><li>Two</li></ul></td></tr>"
        "</table>"
        "\n\nafter"
    )
    out = _convert_html_tables_to_gfm(html)
    assert "<table" not in out
    assert "| Todo |" in out
    assert "| - One<br>- Two |" in out


def test_plain_text_cell_converts_to_gfm():
    html = (
        "<table>"
        "<tr><th>제목</th></tr>"
        "<tr><td>안녕하세요</td></tr>"
        "</table>"
    )
    out = _html_table_to_gfm(html)
    assert out is not None
    lines = out.splitlines()
    assert lines[0] == "| 제목 |"
    assert lines[1] == "| --- |"
    assert lines[2] == "| 안녕하세요 |"


def test_image_cell_converts_to_gfm():
    html = (
        "<table>"
        "<tr><th>img</th></tr>"
        '<tr><td><img src="x.png" alt="pic"></td></tr>'
        "</table>"
    )
    out = _html_table_to_gfm(html)
    assert out is not None
    assert _gfm_data_cell(out) == "![pic](x.png)"


def test_image_cell_with_width_converts_to_gfm():
    html = (
        "<table>"
        "<tr><th>img</th></tr>"
        '<tr><td><img src="y.png" alt="ph" style="width: 240px"></td></tr>'
        "</table>"
    )
    out = _html_table_to_gfm(html)
    assert out is not None
    assert _gfm_data_cell(out) == "![ph\\|240](y.png)"


def test_pipe_in_cell_is_escaped():
    html = (
        "<table>"
        "<tr><th>H</th></tr>"
        "<tr><td>a|b</td></tr>"
        "</table>"
    )
    out = _html_table_to_gfm(html)
    assert out is not None
    assert _gfm_data_cell(out) == "a\\|b"


def test_colspan_still_falls_back_to_html():
    html = (
        "<table>"
        '<tr><th colspan="2">H</th></tr>'
        "<tr><td>A</td><td>B</td></tr>"
        "</table>"
    )
    assert _html_table_to_gfm(html) is None
    assert _convert_html_tables_to_gfm(html) == html


def test_rich_table_cell_preserves_markdown_syntax():
    html = (
        "<table><tr><th>H</th></tr><tr><td>"
        "<p><strong>Bold</strong></p>"
        "<p><em>Italic</em></p>"
        "<p><s>Gone</s></p>"
        "<p><mark>Hi</mark></p>"
        "<p><code>Inline Code</code></p>"
        '<p><span data-latex="x = 1" data-type="inline-math"></span></p>'
        "<blockquote><p>Quote</p></blockquote>"
        "<blockquote><p>[!success] Good</p><p>OK</p></blockquote>"
        '<p><a href="https://example.test/a">link</a></p>'
        "<pre><code>Code N</code></pre>"
        '<div data-latex="E = m2C" data-align="center" data-type="block-math"></div>'
        '<p><span data-type="obsidian-comment" class="wu-comment">memo</span></p>'
        "</td></tr></table>"
    )
    out = _html_table_to_gfm(html)
    assert out is not None
    cell = _gfm_data_cell(out)
    assert "**Bold**" in cell
    assert "*Italic*" in cell
    assert "~~Gone~~" in cell
    assert "==Hi==" in cell
    assert "`Inline Code`" in cell
    assert "$x = 1$" in cell
    assert "> Quote" in cell
    assert "> [!success] Good<br>> OK" in cell
    assert "[link](https://example.test/a)" in cell
    assert "```<br>Code N<br>```" in cell
    assert "$$<br>E = m2C<br>$$" in cell
    assert "%%memo%%" in cell


def test_export_cleanup_removes_empty_paragraph_html_and_unescapes_footnotes():
    md = "<p></p>\n\n각주 테스트\\[^1\\]\n\n\\[^1\\]: 각주 본문\n"
    out = _normalize_markdown_for_export(md)
    assert "<p" not in out
    assert "각주 테스트[^1]" in out
    assert "[^1]: 각주 본문" in out
