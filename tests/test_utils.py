from bot.utils import md_to_html


def test_escape_less_than():
    assert md_to_html("a < b") == "a &lt; b"


def test_escape_ampersand():
    assert md_to_html("a & b") == "a &amp; b"


def test_no_escape_double_quotes():
    # html.escape is called with quote=False — double quotes are NOT escaped
    assert md_to_html('say "hi"') == 'say "hi"'


def test_bold_double_asterisk():
    assert md_to_html("**text**") == "<b>text</b>"


def test_bold_double_underscore():
    assert md_to_html("__text__") == "<b>text</b>"


def test_italic_asterisk():
    assert md_to_html("*text*") == "<i>text</i>"


def test_italic_underscore():
    assert md_to_html("_text_") == "<i>text</i>"


def test_strikethrough():
    assert md_to_html("~~text~~") == "<s>text</s>"


def test_inline_code():
    assert md_to_html("`code`") == "<code>code</code>"


def test_code_block():
    # The regex captures the content including the trailing newline before ```
    result = md_to_html("```\ncode\n```")
    assert result == "<pre>code\n</pre>"


def test_link():
    assert md_to_html("[text](https://example.com)") == '<a href="https://example.com">text</a>'


def test_heading():
    assert md_to_html("### H") == "<b>H</b>"


def test_combo_bold_with_html_chars():
    # HTML chars are escaped first, then bold markers are processed
    assert md_to_html("**a & <tag>**") == "<b>a &amp; &lt;tag&gt;</b>"


def test_empty_string():
    assert md_to_html("") == ""
