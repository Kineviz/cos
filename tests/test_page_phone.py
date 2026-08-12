"""The dashboard is read on a phone, between meetings, one-handed.

There is no browser in this suite, so these tests read the stylesheet the page
ships and check the handful of rules that decide whether a 390px screen works
at all. Each one is a failure that was actually there:

* a text field under 16px makes iOS Safari zoom the whole page on focus and
  never zoom back, so adding a note cost a pinch every time;
* the Move / Stage buttons were 21px tall, and on a phone they are the *only*
  way to move a row — the drag grip is hidden and HTML5 drag does not fire
  from a finger;
* the expanded panel was indented into the title column, leaving a note field
  and six buttons sharing 320px.

Checking the CSS text is a weak test of appearance and a strong test of these
specific regressions: they are all one property being wrong.
"""

from __future__ import annotations

import re

from cos.page import PAGE

PHONE = "@media(max-width:820px)"


def _css() -> str:
    body = PAGE.split("<style>", 1)[1].split("</style>", 1)[0]
    return re.sub(r"/\*.*?\*/", "", body, flags=re.S)


def _rules(css: str) -> list[tuple[str, str]]:
    """Every `selector{body}` at this level, selectors whitespace-normalised."""
    out, i = [], 0
    while True:
        j = css.find("{", i)
        if j < 0:
            return out
        sel = " ".join(css[i:j].split())
        depth, k = 1, j + 1
        while k < len(css) and depth:
            depth += (css[k] == "{") - (css[k] == "}")
            k += 1
        out.append((sel, css[j + 1:k - 1]))
        i = k


def _scope(name: str | None) -> list[tuple[str, str]]:
    """Rules outside any @media, or those inside the phone media query."""
    top = _rules(_css())
    if name is None:
        return [(s, b) for s, b in top if not s.startswith("@")]
    inner = [b for s, b in top if s.replace(" ", "") == name]
    return [r for b in inner for r in _rules(b)]


def _prop(selector: str, prop: str) -> str | None:
    """The value a selector ends up with on a phone — its own rule if the
    phone block sets one, otherwise whatever the base stylesheet said."""
    found = None
    for rules in (_scope(None), _scope(PHONE)):
        for sel, body in rules:
            if selector not in [" ".join(s.split()) for s in sel.split(",")]:
                continue
            for decl in body.split(";"):
                k, _, v = decl.partition(":")
                if k.strip() == prop:
                    found = v.strip()
    return found


def _px(value: str | None) -> float:
    assert value is not None, "expected a length, found no declaration"
    m = re.search(r"-?\d+(?:\.\d+)?", value)
    assert m, f"not a length: {value!r}"
    return float(m.group())


# Every field you can put a cursor in without leaving the list.
TYPEABLE = [".pnl .note", ".add input", ".srchbox input", ".ses input.ren",
            ".cbox textarea", ".spanel input", ".spanel textarea"]


def test_no_field_makes_ios_zoom_the_page():
    for sel in TYPEABLE:
        assert _px(_prop(sel, "font-size")) >= 16, sel


def test_the_controls_a_thumb_uses_are_big_enough():
    # 36px is the floor; 44px is Apple's guidance and what the checkbox gets.
    assert _px(_prop(".mv button", "min-height")) >= 36
    assert _px(_prop(".ghead", "min-height")) >= 36
    assert _px(_prop(".more", "height")) >= 36
    assert _px(_prop(".add", "height")) >= 36
    # The checkbox is drawn small and hit large: ::after insets grow the box.
    drawn = _px(_prop(".tick", "width"))
    assert drawn + 2 * abs(_px(_prop(".tick::after", "inset"))) >= 44


def test_the_expanded_panel_starts_at_the_rows_edge():
    assert _prop(".pnl", "grid-column").replace(" ", "") == "1/-1"


def test_the_detail_still_shows_but_cannot_run_away():
    # It was display:none once, which turned "who is waiting on you" into six
    # names and no subjects. It must be visible, and capped at two lines.
    assert _prop(".dtl", "display") != "none"
    assert _px(_prop(".dtl", "-webkit-line-clamp")) == 2


def test_the_waiting_group_cannot_count_backwards():
    # Five are shown, so with two people waiting the overflow was -3: the
    # header read "WAITING ON YOU  -1" and offered "+ -3 more". There is no JS
    # runtime in this suite, so this checks the clamp is still on the
    # subtraction — the weakest useful test of the strongest kind of bug.
    assert "more:Math.max(0,mail.length-showMail)" in PAGE


def test_touch_copy_does_not_tell_you_to_drag():
    phone = dict(_scope(PHONE))
    assert "display:none" in phone[".deskonly"]
    assert "display:inline" in phone[".mobonly"]
    touch = re.search(r'class="mobonly">([^<]*)<', PAGE)
    assert touch and "rag" not in touch.group(1), touch
