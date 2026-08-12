# The dashboard on a phone

**What changed:** the Tasks panel and the deal panels now work at phone width.
Nothing moved on a laptop — every change is inside the narrow-screen rules.

## What was wrong, and what it does now

**Typing a note zoomed the whole page.** On an iPhone, tapping any text box
smaller than 16px makes Safari zoom in and never zoom back out, so adding a
note meant pinching the page back to size afterwards. The ask box and the
settings screen were already exempt; the note field, the add-a-task line, the
chat search and the rename box were not. They are now.

**The Move buttons were too small to hit.** Today / Soon / Back list — and
Stage on a deal panel — were 21 pixels tall with 5 pixels between them. On a
phone these are the *only* way to move a row: dragging needs the little grip
handle, which is hidden on a phone, and a finger cannot start a drag anyway.
They are now 36 pixels tall with the label on its own line above them, so
Delete and Archive are nowhere near the button you meant to press.

**The tick box was hard to tap.** It still looks the same size; the invisible
target around it is now 44 pixels, which is the size Apple says a thumb hits
reliably.

**Opening a row wasted a third of the screen.** The expanded panel started
under the title instead of at the left edge, so a note field and six buttons
shared 320 of your 390 pixels. It now uses the full width.

**Long email subjects pushed everything else off screen.** A subject plus a
snippet could run four lines. It stops at two now, with "…" — you keep the
density you chose this layout for.

**The menu button sat on the first heading.** The button that opens the left
panel overlapped the top of TODAY's collapse arrow by four pixels and stole
the tap. Fixed.

**"Waiting on you −1".** When fewer than five people were waiting, that
heading counted backwards and offered a "+ −3 more" button. It was a
subtraction with no floor. Now it says 2 when two people are waiting.

**"Drag an item here" was a lie on a phone.** The empty attention list told
you to do something touch cannot do. On a phone it now says "Tap a row, then
press ☆ Needs attention now."

Two smaller things: the rail's resize handle is hidden on a phone (it resized
nothing there and swallowed swipes), and the "Add to Today…" prompt is now
visible rather than drawn in the hairline colour — on a laptop it appears on
hover, and a phone has no hover.

## The evidence

I rendered the real dashboard in a browser at 390 pixels wide — an iPhone's
width — against made-up data (Northwind, Acme, Morgan, Pat), and checked the
task list, an opened row, an opened deal, and the waiting-on-you group. The
screenshots are what the fixes above are based on; the "−1" heading was found
that way, not by reading code.

There are also six new automated tests that read the shipped stylesheet and
fail if any of these slips back: no typeable field under 16px, no thumb
control under 36px, the tick target at 44px, the panel at full width, the
detail capped at two lines, and the waiting count clamped. All six fail on the
old file and pass on the new one. The whole suite — 497 tests — passes.

## What I did not fix

**"Auto-improving application" took 176 seconds to answer.** I left this
alone. That question is a genuinely strategic one, and answering it is a full
assistant run reading your mail and notes; there is no bug to point at, and I
cannot reproduce the timing offline. Making it faster is a product decision —
answer briefly first and fill in detail after, or route strategy questions to
a shorter path — not something to change quietly inside a layout fix.
