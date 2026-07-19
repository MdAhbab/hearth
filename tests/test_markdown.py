"""Tests for the dependency-free markdown -> HTML converter used by chat bubbles."""

from __future__ import annotations

from hearth.ui.markdown import markdown_to_html

# ---------------------------------------------------------------------------
# Security: escaping
# ---------------------------------------------------------------------------


def test_script_tag_is_escaped():
    html_out = markdown_to_html("<script>alert(1)</script>")
    assert "<script>" not in html_out
    assert "alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_ampersand_is_escaped():
    html_out = markdown_to_html("Tom & Jerry")
    assert "Tom &amp; Jerry" in html_out


def test_raw_angle_brackets_never_leak_through():
    html_out = markdown_to_html("if a < b and b > c: <img src=x onerror=alert(1)>")
    assert "<img" not in html_out
    assert "&lt;img" in html_out


# ---------------------------------------------------------------------------
# Inline formatting
# ---------------------------------------------------------------------------


def test_bold():
    assert "<b>bold</b>" in markdown_to_html("**bold**")


def test_italic_asterisk():
    assert "<i>italic</i>" in markdown_to_html("*italic*")


def test_italic_underscore():
    assert "<i>italic</i>" in markdown_to_html("_italic_")


def test_inline_code():
    html_out = markdown_to_html("`code`")
    assert "<code" in html_out
    assert ">code</code>" in html_out


def test_link_http_renders_anchor():
    html_out = markdown_to_html("[Hearth](http://example.com)")
    assert '<a href="http://example.com">Hearth</a>' in html_out


def test_link_https_renders_anchor():
    html_out = markdown_to_html("[Hearth](https://example.com)")
    assert '<a href="https://example.com">Hearth</a>' in html_out


def test_link_javascript_scheme_renders_as_plain_text_not_anchor():
    html_out = markdown_to_html("[click me](javascript:alert(1))")
    assert "<a" not in html_out
    assert "click me" in html_out


def test_inline_code_span_protects_markdown_inside():
    html_out = markdown_to_html("`**not bold**`")
    assert "<b>" not in html_out
    assert "**not bold**" in html_out


# ---------------------------------------------------------------------------
# Fenced code blocks
# ---------------------------------------------------------------------------


def test_fenced_code_block_is_escaped_and_unformatted():
    html_out = markdown_to_html("```\n**x** & <y>\n```")
    assert "<pre" in html_out
    assert "**x** &amp; &lt;y&gt;" in html_out
    assert "<b>" not in html_out


def test_fenced_code_block_ignores_language_tag():
    html_out = markdown_to_html("```python\nprint('hi')\n```")
    assert "<pre" in html_out
    assert "print('hi')" in html_out
    assert "python" not in html_out


def test_fenced_code_block_preserves_internal_newlines():
    html_out = markdown_to_html("```\nline one\nline two\n```")
    pre_start = html_out.index("<pre")
    pre_content = html_out[pre_start:]
    assert "line one\nline two" in pre_content


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------


def test_heading_level_1():
    html_out = markdown_to_html("# Title")
    assert "<b" in html_out
    assert "Title" in html_out
    assert "font-size:15px" in html_out


def test_heading_level_3():
    html_out = markdown_to_html("### Sub")
    assert "<b" in html_out
    assert "Sub" in html_out


def test_heading_deeper_than_3_still_renders_as_heading():
    html_out = markdown_to_html("#### Deep")
    assert "<b" in html_out
    assert "Deep" in html_out


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def test_unordered_list_groups_consecutive_items():
    html_out = markdown_to_html("- one\n- two\n- three")
    assert html_out.count("<ul") == 1
    assert html_out.count("<li>") == 3
    assert "<li>one</li>" in html_out
    assert "<li>two</li>" in html_out
    assert "<li>three</li>" in html_out


def test_unordered_list_accepts_star_prefix():
    html_out = markdown_to_html("* a\n* b")
    assert html_out.count("<ul") == 1
    assert html_out.count("<li>") == 2


def test_ordered_list_groups_consecutive_items():
    html_out = markdown_to_html("1. first\n2. second\n3. third")
    assert html_out.count("<ol") == 1
    assert html_out.count("<li>") == 3
    assert "<li>first</li>" in html_out


def test_separate_lists_not_merged_across_blank_line():
    html_out = markdown_to_html("- a\n- b\n\n- c\n- d")
    assert html_out.count("<ul") == 2


def test_switching_list_type_starts_a_new_list():
    html_out = markdown_to_html("- a\n1. b")
    assert html_out.count("<ul") == 1
    assert html_out.count("<ol") == 1


# ---------------------------------------------------------------------------
# Horizontal rules
# ---------------------------------------------------------------------------


def test_horizontal_rule_dashes():
    assert "<hr/>" in markdown_to_html("---")


def test_horizontal_rule_asterisks():
    assert "<hr/>" in markdown_to_html("***")


# ---------------------------------------------------------------------------
# Paragraphs / line breaks
# ---------------------------------------------------------------------------


def test_blank_line_separates_paragraphs():
    html_out = markdown_to_html("first paragraph\n\nsecond paragraph")
    assert html_out.count("<p>") == 2
    assert "first paragraph" in html_out
    assert "second paragraph" in html_out


def test_single_newline_becomes_br():
    html_out = markdown_to_html("line one\nline two")
    assert "<br/>" in html_out
    assert html_out.count("<p>") == 1


# ---------------------------------------------------------------------------
# Plain text / edge cases
# ---------------------------------------------------------------------------


def test_plain_text_round_trips_readable():
    html_out = markdown_to_html("Hello, world! This is plain text.")
    assert "Hello, world! This is plain text." in html_out


def test_plain_text_consistent_shape_is_paragraph_wrapped():
    html_out = markdown_to_html("just some words")
    assert html_out == "<p>just some words</p>"


def test_empty_string_returns_empty_ish_output_no_crash():
    html_out = markdown_to_html("")
    assert html_out == ""


def test_whitespace_only_does_not_crash():
    html_out = markdown_to_html("   \n\n   ")
    assert isinstance(html_out, str)
