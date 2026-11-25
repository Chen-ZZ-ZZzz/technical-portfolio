"""
The docs render what their source says (user, 2026-11-25).

viewer/SPEC.md rendered wrong on GitHub in three ways: a bare <...> outside code was read as an
HTML tag and dropped; an underscore beside such a tag became emphasis (rule 1 rendered as
'have s "transient_.json"'); a backslash before punctuation was read as an escape and not shown
(rule 5's "/ or \\," as "/ or ,"). The values now sit in code spans, and this guard keeps it so:
for every doc in DOCS (tests/test_readme_quotes.py), nothing outside code may be lost on the way
to the page. It fails on raw HTML (inline, block, comments), a backslash escape or an entity the
renderer consumes, a line break made with a backslash, and emphasis made with _ or __. In these
docs an underscore is nearly always part of an identifier, so a _ pair is far likelier an
identifier left outside code than emphasis meant; emphasis is made with *.

How it reads a doc: markdown-it-py, CommonMark plus GitHub's tables. The table rule takes \\| in
a cell before inline parsing, so a pipe written in a cell is no finding (the pin: "escaped pipe
in a table cell"). The parser's text_join step merges a consumed escape or entity into plain
text; it is turned off, so each stays a text_special token. Every token an inline rule makes is
stamped with where its source begins (_stamped): that gives each finding its line, past a code
span that runs over a line break, and tells a backslash line break from a two-space one, which
make the same token.

Known limits: GitHub's strikethrough (~x~, ~~x~~) is not modelled; no tilde pair stands outside
code in the six docs (2026-11-25). An escape or entity inside a link's destination or title is
resolved by the link rule without a token of its own, so it is not seen.
"""

from collections.abc import Callable
from typing import NamedTuple

import pytest
from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token

from tests.test_readme_quotes import DOCS

# Token.meta key: where a token's source begins in the text its inline rule read.
SOURCE_START = "source_start"


class Lost(NamedTuple):
    """Source text the page does not show as written: its line, what it is, the text, what shows."""
    line: int
    what: str
    source: str
    shown: str = ""


def _stamped(rule: Callable[[StateInline, bool], bool]) -> Callable[[StateInline, bool], bool]:
    """The rule, also stamping each token it makes with the position where it was called."""
    def stamped(state: StateInline, silent: bool) -> bool:
        start, made = state.pos, len(state.tokens)
        ok = rule(state, silent)
        if ok and not silent:
            for token in state.tokens[made:]:
                # a link's own text was tokenized inside the link rule, and stamped by its rules
                token.meta.setdefault(SOURCE_START, start)
        return ok
    return stamped


def parser() -> MarkdownIt:
    md = MarkdownIt("commonmark").enable("table").disable("text_join")
    for rule in md.inline.ruler.__rules__:
        md.inline.ruler.at(rule.name, _stamped(rule.fn), {"alt": rule.alt})
    return md


MD = parser()


def lost(text: str) -> list[Lost]:
    """Everything outside code in a Markdown text that its rendered page would not show."""
    found = []
    for block in MD.parse(text):
        if block.type == "html_block":
            found.append(Lost(block.map[0] + 1, "raw HTML", block.content.splitlines()[0]))
        elif block.type == "inline":
            found += _lost_inline(block.children, block.content, block.map[0] + 1, 0)
    return found


def _lost_inline(tokens: list[Token], content: str, first_line: int, offset: int) -> list[Lost]:
    """The findings among a block's inline tokens; content is the text they were parsed from,
    starting at first_line, and offset where the tokens' own text starts in it."""
    def where(token: Token) -> int:
        return offset + token.meta[SOURCE_START]

    def line(token: Token) -> int:
        return first_line + content.count("\n", 0, where(token))

    found = []
    for i, token in enumerate(tokens):
        if token.type == "html_inline":
            found.append(Lost(line(token), "raw HTML", token.content))
        elif token.type == "text_special":  # info: "escape" or "entity"
            found.append(Lost(line(token), token.info, token.markup, token.content))
        elif token.type == "hardbreak" and content[where(token)] == "\\":
            found.append(Lost(line(token), "line break", "\\"))
        elif token.type in ("em_open", "strong_open") and token.markup[0] == "_":
            inner = _inner_text(tokens, i)
            found.append(Lost(line(token), "emphasis", f"{token.markup}{inner}{token.markup}", inner))
        elif token.type == "image":  # its alt text is parsed apart, from just after the "!["
            found += _lost_inline(token.children, content, first_line, where(token) + 2)
    return found


def _inner_text(tokens: list[Token], i: int) -> str:
    """The text between an opening token and its close, roughly as written: for messages."""
    close = tokens[i].type.replace("_open", "_close")
    inner = []
    for token in tokens[i + 1:]:
        if token.type == close and token.level == tokens[i].level:
            break
        inner.append(" " if token.type in ("softbreak", "hardbreak")
                     else token.markup if token.type == "text_special" else token.content or token.markup)
    return "".join(inner)


def message(doc: str, item: Lost) -> str:
    """A finding as the failure shows it, its texts quoted as written (a repr would double a backslash)."""
    at = f"{doc}:{item.line}:"
    if item.what == "raw HTML":
        return f"{at} raw HTML '{item.source}': GitHub drops it; put the value in a code span"
    if item.what in ("escape", "entity"):
        return f"{at} {item.what} '{item.source}' renders as '{item.shown}'; put the value in a code span"
    if item.what == "line break":
        return f"{at} the backslash ending the line renders as a line break, not shown; put the value in a code span"
    delimiter = item.source[:(len(item.source) - len(item.shown)) // 2]
    return f"{at} emphasis made with '{delimiter}' ('{item.source}'); write it with '{'*' * len(delimiter)}'"


@pytest.mark.parametrize("doc", DOCS)
def test_nothing_outside_code_is_lost(doc):
    found = lost(DOCS[doc])
    assert not found, "the page would not show what the source says:\n" + "\n".join(
        message(doc, item) for item in found)


@pytest.mark.parametrize("text,expected", [
    pytest.param("A value <x> here.\n", [Lost(1, "raw HTML", "<x>")], id="inline tag"),
    pytest.param("Text.\n\n<div>\nx\n</div>\n", [Lost(3, "raw HTML", "<div>")], id="HTML block"),
    pytest.param("a <!-- note --> b\n", [Lost(1, "raw HTML", "<!-- note -->")], id="HTML comment"),
    pytest.param("Text.\n<!-- note -->\nMore.\n", [Lost(2, "raw HTML", "<!-- note -->")],
                 id="HTML comment on its own line"),
    pytest.param("one\ncontaining / or \\., the\n", [Lost(2, "escape", "\\.", ".")], id="backslash escape"),
    pytest.param("a &amp; b\n", [Lost(1, "entity", "&amp;", "&")], id="entity"),
    pytest.param("a\\\nb\n", [Lost(1, "line break", "\\")], id="backslash line break"),
    pytest.param("an _em_ word\n", [Lost(1, "emphasis", "_em_", "em")], id="underscore emphasis"),
    pytest.param("a __strong__ word\n", [Lost(1, "emphasis", "__strong__", "strong")],
                 id="double underscore strong"),
    pytest.param('have _<a>_ "x"\n', [Lost(1, "emphasis", "_<a>_", "<a>"), Lost(1, "raw HTML", "<a>")],
                 id="underscore pair around a tag"),
    pytest.param("p \\|x\\| q\n", [Lost(1, "escape", "\\|", "|"), Lost(1, "escape", "\\|", "|")],
                 id="escaped pipe outside a table"),
    pytest.param("| h |\n|---|\n| \\. <y> |\n", [Lost(3, "escape", "\\.", "."), Lost(3, "raw HTML", "<y>")],
                 id="escape and tag in a table cell"),
    pytest.param("`code\nspan` then\nmore <x>\n", [Lost(3, "raw HTML", "<x>")],
                 id="the line past a code span over a line break"),
    pytest.param("- item\n\n  > a\n  > quote <x>\n", [Lost(4, "raw HTML", "<x>")], id="inside a list and a quote"),
    pytest.param("an image\n![alt\n<x>](i.png)\n", [Lost(3, "raw HTML", "<x>")], id="image alt text"),
])
def test_the_guard_sees(text, expected):
    assert lost(text) == expected


@pytest.mark.parametrize("text", [
    pytest.param("A value `<x>`, `\\` and `&amp;`.\n", id="tag, backslash and entity in code spans"),
    pytest.param("logs `ERROR: ... stopped at the deadline\n... partial report written: <path>` and exits 3\n",
                 id="code span over a line break"),
    pytest.param("a `C:\\\nx` path\n", id="backslash ending a line inside a code span"),
    pytest.param("```\n<x> \\. &amp; _e_\n```\n", id="fenced block"),
    pytest.param("an *em* and **strong** word\n", id="star emphasis"),
    pytest.param("state/transient_monitor_ecdfs.json and lsst_box_census\n", id="underscores inside words"),
    pytest.param("| h |\n|---|\n| \\|measured mag\\| |\n", id="escaped pipe in a table cell"),
    pytest.param("a  \nb\n", id="two-space line break"),
])
def test_the_guard_passes(text):
    assert lost(text) == []
