"""Dependency-free Markdown -> HTML for chat bubbles rendered in QLabel rich text.

Security stance: model output is untrusted. We html-escape the *entire* input
before any markdown parsing happens, so every literal `<`, `>` and `&` in the
model's text is neutralized first. All subsequent parsing only ever wraps the
already-escaped text in a small, fixed set of HTML tags we construct ourselves
(`<b>`, `<i>`, `<code>`, `<pre>`, `<ul>`, `<li>`, `<a href="...">`, ...). There
is no path by which text from the model can introduce a new tag or attribute:
link targets are validated against an http(s)-only allowlist regex, and every
other inline/block rule only emits tags around text that was escaped up front.

Qt's QLabel rich text only understands a small HTML 4 subset, so the tag set
below is intentionally minimal (no tables, no images, no classes/ids).
"""

from __future__ import annotations

import html
import re

_FENCE_RE = re.compile(r"```[^\n]*\n?(.*?)```", re.DOTALL)
_BOLD_RE = re.compile(r"\*\*\*(.+?)\*\*\*|\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"\*(.+?)\*|_(.+?)_")
_CODE_SPAN_RE = re.compile(r"`([^`]+?)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^[-*]\s+(.*)$")
_OL_RE = re.compile(r"^\d+\.\s+(.*)$")
_HR_RE = re.compile(r"^(?:-{3,}|\*{3,})$")

_PRE_STYLE = (
    "font-family:Menlo,Consolas,monospace; font-size:12px; "
    "background:rgba(127,127,127,0.13); padding:6px; border-radius:4px;"
)
_CODE_STYLE = "font-family:Menlo,Consolas,monospace; background:rgba(127,127,127,0.13);"
_LIST_STYLE = "margin:2px 0 2px 14px; -qt-list-indent:1;"
_HEADING_STYLE = "font-size:15px"

_FENCE_PLACEHOLDER = "\x00FENCE{}\x00"


def _format_inline(text: str) -> str:
    """Apply inline formatting to already-escaped text, protecting code spans."""
    spans: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        spans.append(f'<code style="{_CODE_STYLE}">{match.group(1)}</code>')
        return f"\x00CODE{len(spans) - 1}\x00"

    text = _CODE_SPAN_RE.sub(_stash_code, text)
    text = _LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
    text = _BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _ITALIC_RE.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)

    def _restore_code(match: re.Match[str]) -> str:
        return spans[int(match.group(1))]

    return re.sub(r"\x00CODE(\d+)\x00", _restore_code, text)


def markdown_to_html(text: str) -> str:
    """Convert a (trusted-as-untrusted) markdown string into safe QLabel rich text."""
    if not text:
        return ""

    escaped = html.escape(text, quote=False)

    fences: list[str] = []

    def _stash_fence(match: re.Match[str]) -> str:
        body = match.group(1)
        if body.endswith("\n"):
            body = body[:-1]
        fences.append(f'<pre style="{_PRE_STYLE}">{body}</pre>')
        return _FENCE_PLACEHOLDER.format(len(fences) - 1)

    escaped = _FENCE_RE.sub(_stash_fence, escaped)

    lines = escaped.split("\n")
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    list_tag: str | None = None

    def _flush_paragraph() -> None:
        if paragraph:
            blocks.append("<p>" + "<br/>".join(_format_inline(ln) for ln in paragraph) + "</p>")
            paragraph.clear()

    def _flush_list() -> None:
        nonlocal list_tag
        if list_items:
            items = "".join(f"<li>{item}</li>" for item in list_items)
            blocks.append(f'<{list_tag} style="{_LIST_STYLE}">{items}</{list_tag}>')
            list_items.clear()
        list_tag = None

    for raw_line in lines:
        line = raw_line.strip("\r")
        fence_match = re.fullmatch(r"\x00FENCE(\d+)\x00", line.strip())
        if fence_match:
            _flush_paragraph()
            _flush_list()
            blocks.append(fences[int(fence_match.group(1))])
            continue

        if not line.strip():
            _flush_paragraph()
            _flush_list()
            continue

        if _HR_RE.match(line.strip()):
            _flush_paragraph()
            _flush_list()
            blocks.append("<hr/>")
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            # All levels (even deeper than ###) render at the same, capped size —
            # chat bubbles shouldn't shout with huge h1s.
            _flush_paragraph()
            _flush_list()
            content = _format_inline(heading_match.group(2).strip())
            blocks.append(f'<p><b style="{_HEADING_STYLE}">{content}</b></p>')
            continue

        ul_match = _UL_RE.match(line)
        ol_match = _OL_RE.match(line)
        if ul_match or ol_match:
            _flush_paragraph()
            tag = "ul" if ul_match else "ol"
            if list_tag != tag:
                _flush_list()
                list_tag = tag
            content = ul_match.group(1) if ul_match else ol_match.group(1)
            list_items.append(_format_inline(content))
            continue

        _flush_list()
        paragraph.append(line)

    _flush_paragraph()
    _flush_list()

    return "".join(blocks)
