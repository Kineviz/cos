"""The dashboard's HTML, CSS and JS.

Lifted out of `webconfig.py` because it outgrew living beside the server, and
because the two change for different reasons: the server changes when the data
changes, this changes when the design does.

**The layout is Claude Desktop's**, at Wei's suggestion, and it earns its place
rather than being an homage: a narrow rail for navigation, one focused column
to work in, a composer pinned to the bottom, and settings behind a single
button because they are touched monthly. The version this replaces put six
equal tabs across the top, five of which were settings — the daily surface was
one tab in six.

Three specifications went into it:

* **Density.** Rows were 68px, and hovering one grew it 23px, which pushed
  every row below it down — the list ran away from the cursor. Rows are now
  28px and hover changes only the background. A day's work fits one screen
  instead of four.
* **Visual.** Cool slate rather than warm paper, one cold accent, and **no
  green anywhere**: a passing check is rendered in plain grey. Colour is spent
  entirely on what is overdue, which is what the product is for. Weights are
  400/500/600/700 only — the old file used 620 and 650, which snap to 700 on
  any system without a variable UI font.
* **Asking.** Search lands in under a second and the answer takes 13–35s, so
  the sources appear first and the prose arrives above them later. The wait is
  never an empty box, and there is no fast/slow mode for Wei to choose.

**Nothing here loads from the network.** No CDN, no webfont, no icon package —
system fonts and inline SVG. That is what keeps installing this one command.

**Model output is inserted as text, never as markup.** `esc()` runs before any
formatting, and the formatter only ever rewrites strings it produced itself.
Answers are synthesised from mail written by strangers; treating them as HTML
would be a path from an inbound email to script running on the page that edits
the assistant's own permissions.
"""

from __future__ import annotations

from pathlib import Path


def _logo() -> str:
    """The Kineviz mark, inlined.

    Inlined rather than served, because the dashboard is deliberately one file
    with no static route — adding one for a 6KB image would be the first
    exception to that. Shipped at 64px, which is twice what any of the places
    it appears actually render.
    """
    import base64

    path = Path(__file__).with_name("assets") / "kineviz-logo.png"
    try:
        return ("data:image/png;base64,"
                + base64.b64encode(path.read_bytes()).decode())
    except OSError:
        # A missing asset should cost a logo, not the dashboard.
        return ""


PAGE = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<link rel="icon" href="{{LOGO}}">
<title>Kiran</title>
<style>
:root{
  color-scheme:light dark;
  --bg:#EEF0F3; --surface:#FFFFFF; --sunk:#F5F6F8;
  --ink:#14171B; --muted:#5B6472; --line:#DCE0E6;
  --accent:#1F5D7A; --accent-ink:#FFFFFF;
  /* Distinct from --line. The hairline was doubling as a FOREGROUND colour on
     the checkbox border, the disclosure chevron and the add-row plus, all at
     1.3:1 — the primary control in the product was effectively invisible in
     daylight. WCAG wants 3:1 for a control boundary. */
  --edge:#9BA3AE;
  --warn:#8A5A00; --danger:#A63426;
  --focus:color-mix(in srgb,var(--accent) 42%,transparent);
  --scrim:rgba(16,20,28,.34);
  --shadow:0 1px 2px rgba(16,20,28,.06);
  --shadow-lg:0 18px 48px rgba(10,14,20,.18);
  --font:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s5:24px; --s6:32px;
  --r1:6px; --r2:10px; --r3:16px;
  --rail:264px; --measure:46rem; --rowh:28px;
  --ease:cubic-bezier(.22,.61,.36,1);
}
@media (prefers-color-scheme:dark){:root{
  --bg:#101317; --surface:#191D22; --sunk:#14181D;
  --ink:#E4E8ED; --muted:#939CA9; --line:#272D34;
  --accent:#6FB2D4; --accent-ink:#0E1215; --edge:#5C6672;
  --warn:#D9A94E; --danger:#E88377;
  --scrim:rgba(0,0,0,.58); --shadow:none;
  --shadow-lg:0 18px 48px rgba(0,0,0,.55);
}}
*,*::before,*::after{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:400 15px/1.45 var(--font);-webkit-font-smoothing:antialiased;
  overflow:hidden}
h1,h2,h3{margin:0;font-weight:600;letter-spacing:-.006em}
p{margin:0}
button{font:inherit;color:inherit;background:none;border:0;padding:0;cursor:pointer}
input,textarea{font:inherit;color:var(--ink)}
:where(a,button,input,textarea,[tabindex]):focus-visible{
  outline:2px solid var(--accent);outline-offset:2px;border-radius:var(--r1)}
code{font:400 12.5px/1.4 var(--mono);background:var(--sunk);
  border:1px solid var(--line);padding:1px 5px;border-radius:var(--r1)}
.i{display:inline-flex;align-items:center;justify-content:center;
  width:16px;height:16px;flex:0 0 auto}

/* ── shell ───────────────────────────────────────────────────────── */
/* iOS does not shrink the layout viewport for the keyboard, so a composer
   pinned to 100% height ends up under it the moment you tap. */
.app{display:flex;height:100%;height:100dvh}
/* `overflow:hidden` is what makes the drag work, not decoration. A flex item
   defaults to min-width:auto, so the rail could never be narrower than the
   longest chat title in it — measured 399px against a 360px setting. Dragging
   left changed the variable and moved nothing, which read as "resize is
   broken". Anything but `visible` here resolves min-width to 0 and lets
   flex-basis govern. */
.rail{flex:0 0 var(--rail);display:flex;flex-direction:column;min-height:0;
  min-width:0;overflow:hidden;
  background:var(--bg);border-right:1px solid var(--line)}
/* Collapsible on desktop as well as phone. Wei: "I like claude.ai has left
   side panel list, but you can close them to create focus." */
.app.shut .rail{display:none}
/* Drag the rail's edge. Persisted, clamped so it can never be dragged to a
   width you cannot grab again. */
/* Sits fully inside the rail. It used to straddle the border at right:-3px,
   which the clip above would have cut to a 3px target. */
.grab{position:absolute;top:0;bottom:0;right:0;width:8px;cursor:col-resize;z-index:7;
  touch-action:none}
.grab:hover,.grab.on{background:color-mix(in srgb,var(--accent) 45%,transparent)}
.rail{position:relative}
body.resizing{cursor:col-resize;user-select:none}
.rail-top{display:flex;align-items:center;gap:var(--s2);padding:var(--s3) var(--s3) var(--s1)}
.railbtns{padding:0 var(--s3) var(--s2)}
.railbtn{display:flex;align-items:center;gap:var(--s2);width:100%;padding:6px 10px;
  border-radius:var(--r1);color:var(--muted);font-size:13.5px;text-align:left}
.railbtn:hover{color:var(--ink);background:color-mix(in srgb,var(--ink) 5%,transparent)}
.srchbox{padding:0 var(--s3) var(--s2);display:none}
.srchbox.on{display:block}
.srchbox input{width:100%;padding:6px 10px;border:1px solid var(--line);
  border-radius:var(--r1);background:var(--surface);font-size:13.5px}
.srchbox input:focus{outline:0;border-color:var(--accent)}
.ses{display:flex;align-items:center;gap:6px;padding:5px 8px 5px 10px;
  border-radius:var(--r1);color:var(--muted);font-size:13px;cursor:pointer}
.ses:hover{color:var(--ink);background:color-mix(in srgb,var(--ink) 5%,transparent)}
.ses[aria-current="true"]{color:var(--ink);font-weight:600;
  background:color-mix(in srgb,var(--accent) 12%,transparent)}
.ses.dragging{opacity:.4}
.ses .t{flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ses .x{flex:0 0 auto;width:20px;height:20px;border-radius:4px;opacity:0;
  display:inline-flex;align-items:center;justify-content:center;color:var(--muted)}
.ses:hover .x,.ses:focus-within .x{opacity:1}
@media(hover:none){.ses .x{opacity:1}}
.ses .x:hover{color:var(--ink);background:color-mix(in srgb,var(--ink) 9%,transparent)}
.ses input.ren{flex:1 1 auto;min-width:0;border:1px solid var(--accent);
  border-radius:4px;padding:1px 5px;font-size:13px;background:var(--surface)}
.ses input.ren:focus{outline:0}
.sesx{font-size:11.5px;color:var(--muted);padding:2px 10px 0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.menu{position:fixed;z-index:60;min-width:150px;background:var(--surface);
  border:1px solid var(--line);border-radius:var(--r1);box-shadow:var(--shadow-lg);
  padding:4px}
.menu button{display:block;width:100%;text-align:left;padding:6px 10px;
  border-radius:4px;font-size:13px;color:var(--ink)}
.menu button:hover{background:color-mix(in srgb,var(--ink) 7%,transparent)}
.menu button.del{color:var(--danger)}
.panelb{flex:0 0 auto;width:30px;height:30px;border-radius:var(--r1);color:var(--muted);
  display:inline-flex;align-items:center;justify-content:center}
.panelb:hover{color:var(--ink);background:color-mix(in srgb,var(--ink) 7%,transparent)}
/* Reopener, only while shut. Sits where the rail's own toggle was, so the
   control does not appear to move when you use it. */
.reopen{position:absolute;top:12px;left:12px;z-index:6;width:30px;height:30px;
  border-radius:var(--r1);color:var(--muted);background:var(--surface);
  display:none;align-items:center;justify-content:center}
.app.shut .reopen{display:inline-flex}
.reopen:hover{color:var(--ink);background:color-mix(in srgb,var(--ink) 7%,transparent)}
.work{position:relative}
.app.shut .scroll{padding-top:calc(var(--s5) + 22px)}
.newq{display:flex;align-items:center;gap:var(--s2);flex:1 1 auto;min-width:0;
  padding:8px 12px;border-radius:var(--r2);border:1px solid var(--line);
  background:var(--surface);box-shadow:var(--shadow);
  font-size:14px;font-weight:500;color:var(--muted)}
.newq:hover{color:var(--ink);border-color:color-mix(in srgb,var(--ink) 20%,var(--line))}
.rail nav{padding:var(--s2) var(--s2) 0;overflow-y:auto;flex:0 0 auto}
.navb{display:flex;align-items:center;gap:var(--s2);width:100%;
  padding:6px 10px;border-radius:var(--r1);color:var(--muted);
  font-size:13.5px;text-align:left}
.navb:hover{color:var(--ink);background:color-mix(in srgb,var(--ink) 5%,transparent)}
.navb[aria-current="true"]{color:var(--ink);font-weight:600;
  background:color-mix(in srgb,var(--accent) 12%,transparent)}
.navb .n{margin-left:auto;font-size:11.5px;font-variant-numeric:tabular-nums;
  color:var(--muted)}
/* Quieter than a real destination: it makes panels, it is not one. */
.navadd{color:color-mix(in srgb,var(--muted) 65%,transparent);font-size:12.5px}
.navadd:hover{color:var(--ink)}
/* Panels are places; the chat is a conversation. The rule says so. */
.navsep{height:1px;background:var(--line);margin:var(--s2) 10px}
.railsec{padding:var(--s4) var(--s3) var(--s1);font:600 11px/1.15 var(--font);
  letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.recents{flex:1 1 auto;min-height:0;overflow-y:auto;padding:0 var(--s2) var(--s3)}
.rec{display:block;width:100%;padding:5px 10px;border-radius:var(--r1);
  font-size:13px;color:var(--muted);text-align:left;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rec:hover{color:var(--ink);background:color-mix(in srgb,var(--ink) 5%,transparent)}
.rail-foot{flex:0 0 auto;display:flex;align-items:center;gap:var(--s2);
  padding:var(--s2) var(--s3) calc(var(--s3) + env(safe-area-inset-bottom));
  border-top:1px solid var(--line)}
/* The mark sits with the product name, not above the New chat button — the
   top of the rail is where you act, the bottom is where you find out what
   this is and whether it is healthy. */
.mark{flex:0 0 auto;display:block;width:18px;height:18px;object-fit:contain}
.dot{width:7px;height:7px;border-radius:999px;background:var(--muted);flex:0 0 auto}
.dot.warn{background:var(--warn)} .dot.bad{background:var(--danger)}
.who{flex:1 1 auto;min-width:0}
.who b{display:block;font-size:13.5px;font-weight:600}
.who span{display:block;font-size:11.5px;color:var(--muted);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.iconb{width:32px;height:32px;border-radius:var(--r1);color:var(--muted);
  display:inline-flex;align-items:center;justify-content:center}
.iconb:hover{color:var(--ink);background:color-mix(in srgb,var(--ink) 7%,transparent)}

.work{flex:1 1 auto;display:flex;flex-direction:column;min-width:0;min-height:0;
  background:var(--surface)}
.scroll{flex:1 1 auto;overflow-y:auto;padding:var(--s5) var(--s5) var(--s3)}
.col{max-width:var(--measure);margin:0 auto}
.composer{flex:0 0 auto;padding:var(--s2) var(--s5)
  calc(var(--s4) + env(safe-area-inset-bottom))}
.cbox{max-width:var(--measure);margin:0 auto;display:flex;align-items:flex-end;
  gap:var(--s2);background:var(--surface);border:1px solid var(--line);
  border-radius:var(--r3);padding:8px 8px 8px 16px;box-shadow:var(--shadow);
  transition:border-color .12s var(--ease),box-shadow .12s var(--ease)}
.cbox:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--focus)}
.cbox textarea{flex:1 1 auto;border:0;background:transparent;resize:none;
  padding:7px 0;max-height:180px;font-size:15px;line-height:1.45}
.cbox textarea:focus{outline:0}
.cbox textarea::placeholder{color:var(--muted);opacity:.75}
.send{width:32px;height:32px;border-radius:999px;background:var(--accent);
  color:var(--accent-ink);display:inline-flex;align-items:center;
  justify-content:center;flex:0 0 auto}
.send:disabled{opacity:.35}
.chint{max-width:var(--measure);margin:6px auto 0;font-size:11.5px;
  color:var(--muted);text-align:center}

/* ── list ────────────────────────────────────────────────────────── */
.grp{margin:0 0 var(--s4)}
.ghead{position:sticky;top:calc(var(--s5) * -1);z-index:2;display:flex;width:100%;text-align:left;
  align-items:center;gap:var(--s2);height:26px;background:var(--surface);
  font:600 11px/1 var(--font);letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted);cursor:pointer;user-select:none}
.ghead .chev{transition:transform .12s var(--ease);transform:rotate(90deg);
  color:var(--edge)}
.grp[data-open="0"] .chev{transform:none}
.grp[data-open="0"] .rows,.grp[data-open="0"] .add,
.grp[data-open="0"] .more{display:none}
.cnt{font-size:11px;font-weight:500;color:var(--muted);
  font-variant-numeric:tabular-nums}
.gsum{margin-left:auto;font-weight:400;letter-spacing:0;text-transform:none;
  font-size:11.5px;font-variant-numeric:tabular-nums}
.rows{min-height:30px;padding-top:3px;border-radius:var(--r1);
  transition:background .12s var(--ease)}
.rows.over{background:color-mix(in srgb,var(--accent) 9%,transparent);
  outline:1.5px dashed var(--accent);outline-offset:2px}
.empty{color:var(--muted);font-size:12.5px;padding:6px 4px;font-style:italic}

.r{display:grid;grid-template-columns:14px 18px minmax(0,1fr) auto;
  grid-template-rows:var(--rowh);align-items:center;column-gap:var(--s2);
  padding:0 var(--s2) 0 5px;border-left:3px solid transparent;
  border-radius:5px;scroll-margin:32px}
.r:hover{background:color-mix(in srgb,var(--ink) 5%,transparent)}
.r.dragging{opacity:.4}
.r[data-kind="owed"]{border-left-color:color-mix(in srgb,var(--warn) 70%,transparent)}
/* The draft button and what it reports back. Deliberately plain: this writes
   to Gmail, so it should not look like a toy. */
.mv button.draft{border:1px solid var(--accent);color:var(--accent);font-weight:600}
.mv button.draft:disabled{opacity:.6;cursor:default}
.dstat{flex:1 1 100%;font-size:12px;color:var(--muted);line-height:1.45;padding-top:4px}
.dstat a{color:var(--accent)}
.r[data-kind="quiet"]{border-left-color:color-mix(in srgb,var(--danger) 70%,transparent)}
.grip{color:transparent;cursor:grab;text-align:center;user-select:none}
.r:hover .grip{color:var(--edge)}
.tick{position:relative;width:15px;height:15px;border-radius:4px;
  border:1.5px solid var(--edge);display:inline-flex;align-items:center;
  justify-content:center;color:var(--accent-ink)}
.tick::after{content:"";position:absolute;inset:-7px}
.tick:hover{border-color:var(--accent)}
.tick .i{width:11px;height:11px;opacity:0}
.r.done .tick{background:var(--accent);border-color:var(--accent)}
.r.done .tick .i{opacity:1}
.txt{display:flex;align-items:baseline;gap:7px;min-width:0;cursor:default}
/* The title must survive; the detail gives way. Equal shrink factors made
   both ellipsise together, so "Talk applications" became "Talk ap…" while
   half a sentence of detail sat beside it. The title still shrinks, but only
   after the detail has been squeezed to nothing. */
.ttl{flex:0 1 auto;min-width:5ch;font-size:13.5px;font-weight:600;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dtl{flex:1 999 0;min-width:0;font-size:13px;color:var(--muted);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dtl::before{content:"·";margin-right:6px;opacity:.55}
.r.done .ttl{text-decoration:line-through;color:var(--muted);font-weight:500}
.r.done .dtl{opacity:.6}
.meta{display:flex;align-items:center;gap:var(--s2);color:var(--muted);
  font-size:11.5px;font-variant-numeric:tabular-nums;white-space:nowrap}
.cn{display:inline-flex;align-items:center;gap:2px}
.cn .i{width:11px;height:11px}
.age{min-width:4ch;text-align:right}
.age.w{color:var(--warn)} .age.b{color:var(--danger);font-weight:600}
.grp[data-src="mail"] .ttl{font-weight:500;
  color:color-mix(in srgb,var(--ink) 78%,var(--muted))}

.pnl{grid-column:3/-1;display:none;padding:1px 0 10px}
.r.open{background:var(--sunk)}
.r.open .pnl{display:block}
.pnl .full{margin:0 0 var(--s2);font-size:12.5px;line-height:1.5;
  color:var(--muted);white-space:pre-wrap}
.pnl .cmt{margin:0 0 5px;padding-left:var(--s2);font-size:12.5px;
  border-left:2px solid var(--line)}
.pnl .cmt time{margin-left:6px;font-size:11px;color:var(--muted)}
.pnl .note{width:100%;padding:4px 8px;font-size:12.5px;border:1px solid var(--line);
  border-radius:var(--r1);background:var(--surface)}
.pnl .note:focus{outline:0;border-color:var(--accent)}
.mv{display:flex;align-items:center;gap:5px;margin-top:var(--s2);flex-wrap:wrap}
.mv b{font:600 10.5px/1 var(--font);letter-spacing:.05em;text-transform:uppercase;
  color:var(--muted)}
.mv button{padding:2px 9px;font-size:11.5px;border:1px solid var(--line);
  border-radius:999px;color:var(--muted)}
.mv button:hover{color:var(--ink);border-color:var(--accent)}
.mv button[aria-current="true"]{color:var(--accent);border-color:var(--accent);
  font-weight:600}
.mv .rm{margin-left:auto;border-color:transparent;color:var(--danger)}
.add{display:grid;grid-template-columns:14px 18px 1fr;column-gap:var(--s2);
  align-items:center;height:24px;padding:0 var(--s2) 0 5px}
.add .plus{grid-column:2;text-align:center;color:var(--edge);font-size:13px}
.add input{border:0;background:transparent;font-size:13px;padding:0}
.add input:focus{outline:0}
.add input::placeholder{color:var(--line)}
.add input:focus::placeholder{color:var(--muted)}
.more{display:block;width:100%;height:24px;padding-left:40px;text-align:left;
  font-size:12px;color:var(--muted)}
.more:hover{color:var(--accent)}

/* ── chat ────────────────────────────────────────────────────────── */
.turn{margin:0 0 var(--s5)}
.you{display:flex;justify-content:flex-end;margin-bottom:var(--s3)}
.you span{max-width:80%;background:var(--sunk);border:1px solid var(--line);
  border-radius:var(--r3) var(--r3) var(--r1) var(--r3);padding:9px 14px;
  font-size:14.5px;white-space:pre-wrap}
.kir{display:flex;gap:var(--s3);align-items:flex-start}
.kav{flex:0 0 auto;width:24px;height:24px;border-radius:999px;margin-top:1px;
  background:var(--accent);color:var(--accent-ink);display:flex;
  align-items:center;justify-content:center;font:600 11px/1 var(--font)}
.kbody{flex:1 1 auto;min-width:0}
.ans{font-size:14.5px;line-height:1.62}
.ans,.you span,.st,.sx,.pnl .full,.cmt{overflow-wrap:anywhere}
.ans p{margin:0 0 9px} .ans ul{margin:0 0 9px;padding-left:19px}
.ans li{margin:2px 0} .ans b{font-weight:600}
.slug{color:var(--accent);cursor:pointer;
  border-bottom:1px solid color-mix(in srgb,var(--accent) 35%,transparent)}
/* Sticky, because toBottom() pins the scroller to the very bottom and the
   stage line sits ABOVE six source cards — so for the whole of a 69-second
   answer the only moving thing on the page was off-screen. */
.stage{position:sticky;top:0;z-index:3;background:var(--surface);
  padding:6px 0;margin:-6px 0 0;
  display:flex;align-items:center;gap:var(--s2);color:var(--muted);
  font-size:13px;font-variant-numeric:tabular-nums}
.blip{width:6px;height:6px;border-radius:999px;background:var(--accent);
  animation:blip 1.2s ease-in-out infinite}
@keyframes blip{0%,100%{opacity:.25}50%{opacity:1}}
@media(prefers-reduced-motion:reduce){.blip{animation:none;opacity:.6}}
.srcs{margin-top:var(--s3);border-top:1px solid var(--line)}
/* Open while waiting — the sources ARE the answer for the first twenty
   seconds. Closed once the prose lands, because by then they are evidence you
   may want rather than something you are reading. */
.srch{font:600 11px/1 var(--font);letter-spacing:.05em;text-transform:uppercase;
  color:var(--muted);margin:var(--s3) 0 2px;cursor:pointer;list-style:none}
.srch::-webkit-details-marker{display:none}
details.srch,details>.srch::before{content:""}
details>summary.srch::before{content:"▸ ";color:var(--edge)}
details[open]>summary.srch::before{content:"▾ "}
summary.srch:hover{color:var(--ink)}
.src{display:flex;gap:10px;align-items:flex-start;padding:7px 0;
  border-bottom:1px solid var(--line);cursor:pointer}
.src:last-child{border-bottom:0}
.src:hover .st{color:var(--accent)}
.smain{flex:1 1 auto;min-width:0}
.st{font-weight:600;font-size:13.5px}
.sm{color:var(--muted);font-size:11.5px;margin-top:1px;display:flex;gap:7px}
.kind{border:1px solid var(--line);border-radius:999px;padding:0 6px}
.sx{color:var(--muted);font-size:12.5px;margin-top:2px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.sadd{flex:0 0 auto;border:1px solid var(--line);border-radius:var(--r1);
  padding:2px 8px;font-size:11.5px;color:var(--muted);opacity:0}
.src:hover .sadd,.src:focus-within .sadd{opacity:1}
@media(hover:none){.sadd{opacity:1}}
.sadd:hover{color:var(--accent);border-color:var(--accent)}
.acts{display:flex;gap:6px;flex-wrap:wrap;margin-top:var(--s3)}
.chip{border:1px solid var(--line);border-radius:999px;padding:3px 11px;
  font-size:12.5px;color:var(--muted);max-width:100%;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.chip:hover{color:var(--ink);border-color:var(--accent)}
.chip.go{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,var(--line))}
.cached{font-size:11.5px;color:var(--muted);margin-bottom:5px}
/* Source drawer. Slides over rather than navigating, so an answer you are
   half-way through reading — and any question still running — survives. */
.dscrim{position:fixed;inset:0;z-index:55;background:var(--scrim)}
.drawer{position:fixed;z-index:56;top:0;right:0;bottom:0;width:min(560px,94vw);
  display:flex;flex-direction:column;background:var(--surface);
  border-left:1px solid var(--line);box-shadow:var(--shadow-lg)}
.dhead{display:flex;align-items:flex-start;gap:var(--s3);padding:var(--s4);
  border-bottom:1px solid var(--line);flex:0 0 auto}
.dhead h3{font-size:15px;margin-bottom:2px}
.dslug{font:400 11.5px/1.4 var(--mono);color:var(--muted);word-break:break-all}
.dbody{flex:1 1 auto;overflow-y:auto;padding:var(--s4);font-size:14px;line-height:1.6}
.dbody p{margin:0 0 9px} .dbody ul{margin:0 0 9px;padding-left:19px}
@media(max-width:820px){
  .drawer{top:auto;left:0;width:auto;border-left:0;border-top:1px solid var(--line);
    border-radius:var(--r3) var(--r3) 0 0;max-height:88vh}
}

/* ── blank state, banners, health ─────────────────────────────────── */
.blank{text-align:center;padding:14vh 0 0;color:var(--muted)}
.blank h2{font-size:19px;color:var(--ink);margin-bottom:5px}
.blank p{font-size:14px}
.banner{border:1px solid color-mix(in srgb,var(--danger) 32%,transparent);
  background:color-mix(in srgb,var(--danger) 8%,transparent);
  border-left:3px solid var(--danger);padding:10px 14px;border-radius:var(--r1);
  font-size:13px;margin-bottom:var(--s4)}
.hl{display:flex;align-items:center;gap:var(--s2);padding:5px 0;
  border-bottom:1px solid var(--line);font-size:13px}
.hl:last-child{border-bottom:0}
.pill{border-radius:999px;padding:0 8px;font:600 11px/1.7 var(--font)}
.pill.ok{color:var(--muted);border:1px solid var(--line)}
.pill.warn{color:var(--warn);background:color-mix(in srgb,var(--warn) 14%,transparent)}
.pill.fail,.pill.unknown{color:var(--danger);
  background:color-mix(in srgb,var(--danger) 13%,transparent)}
.hint{color:var(--muted);font-size:13px}
.card{border:1px solid var(--line);border-radius:var(--r2);padding:var(--s4);
  margin-bottom:var(--s4)}
.card h2{font-size:14px;margin-bottom:3px}

/* ── prospects rows — same skeleton as tasks, same density ───────── */
.pdot{width:8px;height:8px;border-radius:50%;justify-self:center;
  background:var(--line)}
.pdot.you{background:var(--warn)}
/* The row is a 4-column grid, so the dot cell must exist even when a panel
   has no whose-ball data — dropping the span slid the name into the 18px
   dot column and "Pricing page" rendered as "Prici…". */
.pdot.none{background:transparent}
.r[data-ball="you"]{border-left-color:color-mix(in srgb,var(--warn) 70%,transparent)}
.stg{font-size:11px;color:var(--muted);white-space:nowrap}
.hotstar{color:var(--warn)}
.focusg{border:1px solid color-mix(in srgb,var(--warn) 45%,transparent);
  border-radius:var(--r2);padding:2px 6px 6px;margin-bottom:var(--s3)}
.focusg .ghead{color:var(--ink);font-weight:600}
button.pfoc{border:1px solid var(--warn);color:var(--warn);font-weight:600}

/* ── settings sheet ──────────────────────────────────────────────── */
.scrim{position:fixed;inset:0;z-index:50;background:var(--scrim);opacity:0;
  pointer-events:none;transition:opacity .14s var(--ease)}
.scrim.on{opacity:1;pointer-events:auto}
.sheet{position:fixed;z-index:51;top:0;right:0;bottom:0;width:520px;max-width:94vw;
  display:flex;flex-direction:column;background:var(--surface);
  border-left:1px solid var(--line);box-shadow:var(--shadow-lg);
  transform:translateX(16px);opacity:0;pointer-events:none;
  transition:transform .18s var(--ease),opacity .18s var(--ease)}
.sheet.on{transform:none;opacity:1;pointer-events:auto;visibility:visible}
.sheet[aria-hidden="true"]{visibility:hidden}
.shead{display:flex;align-items:center;gap:var(--s3);height:52px;
  padding:0 var(--s3) 0 var(--s4);border-bottom:1px solid var(--line);flex:0 0 auto}
.shead h1{flex:1 1 auto;font-size:17px}
.sbody{flex:1 1 auto;display:flex;min-height:0}
.srail{flex:0 0 160px;border-right:1px solid var(--line);padding:var(--s3) var(--s2);
  overflow-y:auto}
.srail button{display:flex;align-items:center;justify-content:space-between;
  width:100%;padding:7px 10px;border-radius:var(--r1);color:var(--muted);
  font-size:13.5px;text-align:left}
.srail button:hover{color:var(--ink);background:color-mix(in srgb,var(--ink) 5%,transparent)}
.srail button[aria-current="true"]{color:var(--ink);font-weight:600;
  background:color-mix(in srgb,var(--accent) 12%,transparent)}
.spanel{flex:1 1 auto;overflow-y:auto;padding:var(--s4)}
.spanel label{display:block;margin:var(--s3) 0 var(--s1);font-size:13px;font-weight:600}
.spanel label .note{font-weight:400;color:var(--muted)}
.spanel input,.spanel textarea{width:100%;padding:8px 10px;border:1px solid var(--line);
  border-radius:var(--r1);background:var(--surface);font-size:14px}
.spanel textarea{min-height:76px;resize:vertical;font:400 12.5px/1.5 var(--mono)}
.spanel input:focus,.spanel textarea:focus{outline:0;border-color:var(--accent);
  box-shadow:0 0 0 3px var(--focus)}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:var(--s3)}
.toggle{display:flex;align-items:center;gap:9px;margin:var(--s3) 0}
.toggle input{width:auto}
.save{background:var(--accent);color:var(--accent-ink);border-radius:var(--r1);
  padding:8px 16px;font-weight:600;font-size:14px}
.save:disabled{opacity:.45}
.saved{font-size:13px;color:var(--muted);margin-left:10px}
.sfoot{flex:0 0 auto;display:flex;align-items:center;padding:var(--s3) var(--s4)
  calc(var(--s3) + env(safe-area-inset-bottom));border-top:1px solid var(--line)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{font:600 11px/1.2 var(--font);letter-spacing:.05em;text-transform:uppercase;
  color:var(--muted);text-align:left;padding:0 var(--s2) var(--s2)}
td{padding:6px var(--s2);border-top:1px solid var(--line);vertical-align:top}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap;width:1%}

/* ── phone ───────────────────────────────────────────────────────── */
.railtoggle{display:none}
@media(max-width:820px){
  :root{--rowh:34px}
  .app.shut .rail,.rail{display:flex;position:fixed;z-index:45;top:0;bottom:0;left:0;
    width:86vw;max-width:320px;transform:translateX(-101%);
    transition:transform .2s var(--ease);box-shadow:var(--shadow-lg)}
  .rail.on{transform:none}
  .reopen,.app.shut .reopen{display:inline-flex}
  .app.shut .scroll{padding-top:calc(var(--s3) + 22px)}
  .railtoggle{display:inline-flex}
  /* The reopener is shown unconditionally on a phone, but the padding that
     made room for it was gated on .app.shut — so it landed exactly on TODAY's
     collapse chevron and stole the tap. 26px was four pixels short: the button
     ends at y=42 and the first header still began at y=38, so the top of the
     chevron was under it. 34px clears the button outright. */
  .scroll{padding:calc(var(--s3) + 34px) var(--s4) var(--s3)}
  /* The rail is a fixed-width overlay here, so dragging its edge resizes
     nothing — the handle only sat over the rail swallowing swipes. */
  .grab{display:none}
  .composer{padding:var(--s2) var(--s4) calc(var(--s3) + env(safe-area-inset-bottom))}
  .r{grid-template-columns:20px minmax(0,1fr) auto;column-gap:10px}
  .grip{display:none}
  /* Was display:none, which turned "see what you owe people" into six names
     with no idea what any of them want — and there are no tooltips on touch.
     Wrap to a second line instead. */
  .r{grid-template-rows:auto}
  .txt{flex-direction:column;align-items:flex-start;gap:1px;padding:6px 0}
  /* Wrapping brought the detail back, but a mail subject plus a snippet ran
     four lines at 390px and pushed the next two tasks off the screen. Two
     lines then an ellipsis: the detail is legible and the density Wei chose
     this skeleton for survives. */
  .dtl{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
    overflow:hidden;flex:none;max-width:100%;white-space:normal}
  .dtl::before{content:none}
  .ttl{font-size:14px;white-space:normal}
  /* Drawn at 18px, hit at 44px. A finger is not a cursor, and ticking things
     off is the one thing done most on a phone. */
  .tick{width:18px;height:18px}
  .tick::after{inset:-13px}
  /* Was indented into the title column, so a note field and six buttons
     shared 320px of a 390px screen. The panel starts at the row's own edge
     and gets the 30px back. */
  .pnl{grid-column:1/-1;padding-bottom:var(--s3)}
  .pnl .note{padding:8px 10px}
  /* Move / Stage / Attention are the ONLY way to move a row on a phone:
     dragging needs the grip, and the grip is hidden here. They were 21px
     tall and 5px apart, which is a miss or a wrong button. The label takes
     its own line so the first button is not squeezed against it. */
  .mv{gap:8px}
  .mv b{flex:0 0 100%}
  .mv button{padding:8px 14px;font-size:13px;min-height:36px}
  /* A group header is a tap target too — collapsing Waiting-on-you is how
     the list fits a screen. Let it wrap rather than crush the summary. */
  .ghead{height:auto;min-height:36px;flex-wrap:wrap}
  .more{height:36px;padding-left:30px;font-size:13px}
  .add{grid-template-columns:20px 1fr;height:40px}
  .add .plus{grid-column:1}
  /* Nothing reveals on hover here, so a placeholder drawn in the hairline
     colour means the add-a-task line is invisible until you tap it. */
  .add input::placeholder{color:var(--edge)}
  /* Under 16px, iOS Safari zooms the whole page on focus and does not zoom
     back — every note added cost a pinch. The composer and the settings
     sheet already opted out; the row note, the add line, the chat search and
     the rename box did not. */
  .cbox textarea,.spanel input,.spanel textarea,
  .pnl .note,.add input,.srchbox input,.ses input.ren{font-size:16px}
  .sheet{left:0;width:100%;max-width:none;border-left:0;transform:translateY(24px)}
  .sheet.on{transform:none}
  .sbody{flex-direction:column}
  .srail{flex:0 0 auto;border-right:0;border-bottom:1px solid var(--line);
    display:flex;gap:4px;overflow-x:auto;padding:var(--s2)}
  .srail button{width:auto;white-space:nowrap}
  .row2{grid-template-columns:1fr}
}
/* A finger cannot drag: the grip is hidden on a phone and HTML5 drag never
   fires from touch anyway. So the empty attention list has to name the
   control that does work there instead of the one that does not. */
.deskonly{display:inline}
.mobonly{display:none}
@media(max-width:820px){.deskonly{display:none}.mobonly{display:inline}}
</style>

<div class="app" id="app">
  <aside class="rail" id="rail">
    <div class="rail-top">
      <button class="newq" id="newq"><span class="i" id="i-plus"></span>New chat</button>
      <button class="panelb" id="shut" aria-label="Hide the panel"></button>
    </div>
    <div class="railbtns">
      <button class="railbtn" id="srchtoggle"><span class="i" id="i-search"></span>Search in chats</button>
    </div>
    <div class="srchbox" id="srchbox"><input id="srchq" placeholder="Search your chats…"></div>
    <nav id="nav"></nav>
    <div class="railsec" id="seshead">Chats</div>
    <div class="recents" id="recents"></div>
    <div class="grab" id="grab"></div>
    <div class="rail-foot">
      <img class="mark" src="{{LOGO}}" alt="Kineviz" width="18" height="18">
      <span class="dot" id="dot"></span>
      <span class="who"><b id="who">cos</b><span id="asof">loading…</span></span>
      <button class="iconb" id="gear" aria-label="Settings"></button>
    </div>
  </aside>

  <main class="work">
    <button class="reopen" id="reopen" aria-label="Show the panel"></button>
    <div class="scroll" id="scroll"><div class="col" id="view"></div></div>
    <div class="composer">
      <form class="cbox" id="cbox">
        <textarea id="q" rows="1" placeholder="Ask Kiran anything…"></textarea>
        <button class="send" id="send" type="submit" aria-label="Ask"></button>
      </form>
      <div class="chint" id="chint"></div>
    </div>
  </main>
</div>

<div class="scrim" id="scrim"></div>
<aside class="sheet" id="sheet" role="dialog" aria-modal="true" aria-label="Settings" aria-hidden="true">
  <div class="shead">
    <h1>Settings</h1>
    <button class="iconb" id="sclose" aria-label="Close"></button>
  </div>
  <div class="sbody">
    <nav class="srail" id="srail"></nav>
    <div class="spanel" id="spanel"></div>
  </div>
</aside>

<script>
/* ── icons: inline, 16px box, 1.4 stroke ─────────────────────────── */
const ICO={
 gear:'<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="2.35"/><circle cx="8" cy="8" r="4.9"/><path d="M12.9 8h1.4M11.47 4.53l.99-.99M8 3.1V1.7M4.53 4.53l-.99-.99M3.1 8H1.7M4.53 11.47l-.99.99M8 12.9v1.4M11.47 11.47l.99.99"/></svg>',
 check:'<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3.4 8.5 6.4 11.5 12.8 4.8"/></svg>',
 chev:'<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.6 10.4 8 6 12.4"/></svg>',
 close:'<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M4.2 4.2 11.8 11.8M11.8 4.2 4.2 11.8"/></svg>',
 plus:'<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M8 3.2v9.6M3.2 8h9.6"/></svg>',
 up:'<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M8 12.6V3.9M4.2 7.7 8 3.9l3.8 3.8"/></svg>',
 menu:'<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/></svg>',
 bub:'<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M2 3h12v8H6l-4 3V3z"/></svg>',
 search:'<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="7" r="4.25"/><path d="M10.15 10.15 13.6 13.6"/></svg>',
 dots:'<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><circle cx="8" cy="3.4" r="1.15"/><circle cx="8" cy="8" r="1.15"/><circle cx="8" cy="12.6" r="1.15"/></svg>',
 panel:'<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><rect x="1.9" y="2.9" width="12.2" height="10.2" rx="1.8"/><path d="M6.3 2.9v10.2"/></svg>',
 grip:'<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><circle cx="6" cy="4" r=".95"/><circle cx="10" cy="4" r=".95"/><circle cx="6" cy="8" r=".95"/><circle cx="10" cy="8" r=".95"/><circle cx="6" cy="12" r=".95"/><circle cx="10" cy="12" r=".95"/></svg>'};
const ic=(n)=>`<span class="i">${ICO[n]}</span>`;

/* ── state ───────────────────────────────────────────────────────── */
let S={},D={},A={},G=[],CHAT=[],MODE='list',POLL=null;
let SES=[],CUR=null,FIND='';
// User-created panels, straight from the database. Wei: "UI should not be
// static. we should be able to on demand add a new dashboard tab."
let PANELS=[];
// Which panel the current view shows — 'prospects', a custom id, or null.
const PID=()=>MODE==='pros'?'prospects':MODE.startsWith('p:')?MODE.slice(2):null;
const PDATA=pid=>pid==='prospects'
  ?{title:'Prospects',rows:D.prospects||[],states:D.prospect_states||[],mail:true}
  :(PANELS.find(p=>p.id===pid)||{title:pid,rows:[],states:[]});
const BUCKETS=[['today','Today'],['soon','Soon'],['backlog','Back list']];
const KIND={owed:'waiting on you',quiet:'gone quiet',manual:'',todo:''};

const el=h=>{const t=document.createElement('template');t.innerHTML=h.trim();return t.content.firstChild};
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const $=id=>document.getElementById(id);

/* ── markdown: esc FIRST, then only rewrite what we produced ─────── */
const SLUGRE=/\b(?:email|calendar|atoms|90_agent|10_wiki|00_source|05_workspace|clients|companies|people|events|extracts)\/[a-z0-9][a-z0-9\/_.-]*[a-z0-9]/g;
function md(t,link){
  // Bullet RUNS, not all-or-nothing. The old test was ls.every(isBullet), so a
  // real answer beginning "What happened:" followed by three bullets rendered
  // as one run-on paragraph with the dashes visible. Also handles 1. lists and
  // `code`, which Kiran emits and which came through literally.
  const html=esc(t||'').split(/\n{2,}/).map(b=>{
    const ls=b.split('\n'); let out='',buf=[];
    const flush=()=>{if(buf.length){
      out+='<ul>'+buf.map(l=>`<li>${l}</li>`).join('')+'</ul>';buf=[]}};
    for(const l of ls){
      const m=l.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
      if(m) buf.push(m[1]);
      else if(l.trim()) {flush(); out+=`<p>${l}</p>`;}
    }
    flush(); return out;
  }).join('').replace(/\*\*(.+?)\*\*/g,'<b>$1</b>')
    .replace(/`([^`\n]+)`/g,'<code>$1</code>');
  return link ? html.replace(SLUGRE,s=>
    `<a class="slug" role="link" tabindex="0" data-slug="${s}">${s.split('/').pop().replace(/-/g,' ')}</a>`) : html;
}

/* ── rail ────────────────────────────────────────────────────────── */
function renderRail(){
  const live=G.filter(i=>!i.done);
  const counts={
    today:live.filter(i=>i.bucket==='today'&&i.kind!=='owed'&&i.kind!=='quiet').length,
    soon:live.filter(i=>i.bucket==='soon'&&i.kind!=='owed'&&i.kind!=='quiet').length,
    backlog:live.filter(i=>i.bucket==='backlog'&&i.kind!=='owed'&&i.kind!=='quiet').length,
    mail:live.filter(i=>i.kind==='owed'||i.kind==='quiet').length,
    done:G.filter(i=>i.done).length};
  // Two destinations, no sub-list. The rail used to repeat every section
  // heading — Today, Soon, Back list, Waiting on you, Done — as an indented
  // jump link. Wei: "we don't need today, soon, etc on the left bar." They
  // were a table of contents for a page already on screen: same words, same
  // counts, one scroll apart.
  const pros=(D.prospects||[]);
  const yourBall=pros.filter(p=>p.ball==='you').length;
  $('nav').innerHTML=
    `<button class="navb" data-go="list" ${MODE==='list'?'aria-current="true"':''}>Tasks
       <span class="n">${counts.today+counts.soon+counts.backlog}</span></button>`+
    (pros.length?`<button class="navb" data-go="pros" ${MODE==='pros'?'aria-current="true"':''}>Prospects
       <span class="n">${yourBall||pros.length}</span></button>`:'')+
    PANELS.map(p=>`<button class="navb" data-go="p:${p.id}" ${MODE==='p:'+p.id?'aria-current="true"':''}>${esc(p.title)}
       <span class="n">${p.rows.length}</span></button>`).join('')+
    `<button class="navb navadd" data-add-panel title="Add a panel">+ panel</button>`+
    (CUR?`<div class="navsep"></div><button class="navb" data-go="chat" ${MODE==='chat'?'aria-current="true"':''}
       >Current chat</button>`:'');
  $('nav').querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>{
    show(b.dataset.go); if(b.dataset.go==='chat') toBottom()});
  const addp=$('nav').querySelector('[data-add-panel]');
  if(addp) addp.onclick=async()=>{
    const t=prompt('Name the new panel — e.g. GTM, Hiring, Partnerships');
    if(!t||!t.trim()) return;
    if(await panelAct({action:'create_panel',title:t.trim()})){
      const made=PANELS.find(p=>p.title.toLowerCase()===t.trim().toLowerCase().replace(/\s+/g,' '));
      if(made) show('p:'+made.id);
    }};

  renderSessions();

  // A red dot for "1 uncommitted change" is how you teach someone to ignore
  // the health indicator. warn and fail are different colours for a reason.
  const cs=D.health||[];
  const worst = cs.some(c=>c.status!=='ok'&&c.status!=='warn') ? ' bad'
              : cs.some(c=>c.status==='warn') ? ' warn' : '';
  $('dot').className='dot'+worst;
  $('dot').title = worst
    ? cs.filter(c=>c.status!=='ok').map(c=>c.name).join(', ')
    : 'All checks passing';
  $('who').textContent=S['agent.name']||'cos';
  $('asof').textContent=D.generated_at?`as of ${D.generated_at}`:'no data yet';
}
function closeRail(){
  $('rail').classList.remove('on');
  if(!$('sheet').classList.contains('on')) $('scrim').classList.remove('on');
}

function renderSessions(){
  $('seshead').textContent = FIND ? 'Results' : 'Chats';
  const list = SES;
  $('recents').innerHTML = list.length
    ? list.map(s=>`<div class="ses" data-s="${s.id}" draggable="true"
        role="button" tabindex="0" ${CUR===s.id?'aria-current="true"':''}>
        <span class="t">${esc(s.title)}</span>
        <button class="x" data-menu="${s.id}" aria-label="Options">${ICO.dots}</button>
      </div>${s.excerpt?`<div class="sesx">…${esc(s.excerpt)}…</div>`:''}`).join('')
    : `<div class="rec" style="cursor:default">${FIND?'Nothing found':'No chats yet'}</div>`;

  $('recents').querySelectorAll('.ses').forEach(n=>{
    const id=n.dataset.s;
    n.onclick=e=>{if(e.target.closest('[data-menu]'))return; openSession(id)};
    n.onkeydown=e=>{
      if(e.target!==n)return;
      if(e.key==='Enter'||e.key===' '){e.preventDefault();openSession(id)}};
    n.querySelector('[data-menu]').onclick=e=>{e.stopPropagation();sessionMenu(e,id,n)};
  });
  wireSessionDrag();
}

function sessionMenu(ev,id,node){
  document.querySelectorAll('.menu').forEach(m=>m.remove());
  const m=el(`<div class="menu">
    <button data-a="rename">Rename</button>
    <button data-a="up">Move up</button>
    <button data-a="down">Move down</button>
    <button class="del" data-a="delete">Delete</button></div>`);
  let x=ev.clientX,y=ev.clientY;
  if(!x&&!y){const r=(ev.currentTarget||node).getBoundingClientRect();
    x=r.left; y=r.bottom;}
  m.style.left=Math.min(x,innerWidth-170)+'px';
  m.style.top=Math.min(y,innerHeight-140)+'px';
  document.body.append(m);
  const close=()=>m.remove();
  setTimeout(()=>addEventListener('click',close,{once:true}),0);
  m.querySelectorAll('[data-a]').forEach(b=>b.onclick=async e=>{
    e.stopPropagation(); close();
    const a=b.dataset.a;
    if(a==='rename') return startRename(id,node);
    if(a==='delete'){
      // No confirm dialog: a modal blocks the extension and this is one
      // undoable-by-asking-again list, not a destructive act on real data.
      await chatPost({action:'delete',id});
      if(CUR===id){CUR=null;CHAT=[];show('chat')}
      return;
    }
    const i=SES.findIndex(s=>s.id===id);
    if(a==='up'&&i>0) await chatPost({action:'move',id,
      above:SES[i-2]?.id||null, below:SES[i-1].id});
    if(a==='down'&&i<SES.length-1) await chatPost({action:'move',id,
      above:SES[i+1].id, below:SES[i+2]?.id||null});
  });
}

function startRename(id,node){
  const s=SES.find(x=>x.id===id); if(!s)return;
  const inp=el(`<input class="ren" value="${esc(s.title)}">`);
  node.querySelector('.t').replaceWith(inp);
  inp.focus(); inp.select();
  const done=async(save)=>{
    const v=inp.value.trim();
    if(save&&v&&v!==s.title) await chatPost({action:'rename',id,title:v});
    else renderSessions();
  };
  inp.onkeydown=e=>{
    if(e.key==='Enter'){e.preventDefault();done(true)}
    if(e.key==='Escape'){e.preventDefault();done(false)}};
  inp.onblur=()=>done(true);
  inp.onclick=e=>e.stopPropagation();
}

function wireSessionDrag(){
  let dragged=null;
  const host=$('recents');
  host.querySelectorAll('.ses').forEach(n=>{
    n.addEventListener('dragstart',e=>{dragged=n;n.classList.add('dragging');
      e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',n.dataset.s)});
    n.addEventListener('dragend',()=>{n.classList.remove('dragging');dragged=null});
  });
  host.addEventListener('dragover',e=>{
    e.preventDefault(); if(!dragged)return;
    const after=[...host.querySelectorAll('.ses:not(.dragging)')].find(x=>{
      const r=x.getBoundingClientRect();return e.clientY<r.top+r.height/2});
    after?host.insertBefore(dragged,after):host.appendChild(dragged);
  });
  host.addEventListener('drop',async e=>{
    e.preventDefault(); if(!dragged)return;
    const rows=[...host.querySelectorAll('.ses')],at=rows.indexOf(dragged);
    await chatPost({action:'move',id:dragged.dataset.s,
      above:rows[at-1]?.dataset.s||null, below:rows[at+1]?.dataset.s||null});
  });
}

async function chatPost(body){
  const j=await (await fetch('/api/chats',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  if(j.sessions){SES=j.sessions;renderSessions()}
  return j;
}

async function openSession(id){
  CUR=id; localStorage.setItem('cos.chat',id);
  const s=await (await fetch('/api/chats/'+id)).json();
  // Newest first, to match where the composer is and where a new answer lands.
  // Keep the recorded status. Forcing 'done' here made an in-flight question
  // impossible to resume even in principle.
  CHAT=(s.turns||[]).map(t=>({...t,status:t.status||'done'}));
  show('chat'); closeRail(); renderSessions(); toBottom();
  if(CHAT.some(t=>t.status==='running')) schedule();
}

async function newChat(){
  const s=await (await fetch('/api/chats',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'new'})})).json();
  CUR=s.id; CHAT=[]; localStorage.setItem('cos.chat',s.id);
  SES=(await (await fetch('/api/chats')).json()).sessions||[];
  show('chat'); renderSessions(); closeRail(); $('q').focus();
}

async function runSearch(){
  const q=$('srchq').value.trim(); FIND=q;
  const url=q?'/api/chats/search?q='+encodeURIComponent(q):'/api/chats';
  SES=(await (await fetch(url)).json()).sessions||[];
  renderSessions();
}

/* ── list ────────────────────────────────────────────────────────── */
const ageCls=d=>d==null?'':d>45?' b':d>14?' w':'';
function row(i){return `<div class="r${i.done?' done':''}" data-id="${i.id}" data-kind="${i.kind}"
   tabindex="0"${i.detail?` title="${esc(i.detail)}"`:''}>
  <span class="grip">${ICO.grip}</span>
  <button class="tick" aria-label="${i.done?'Reopen':'Done'}">${ic('check')}</button>
  <span class="txt"><span class="ttl">${esc(i.title)}</span>
    ${i.detail?`<span class="dtl">${esc(i.detail)}</span>`:''}</span>
  <span class="meta">
    ${i.comments.length?`<span class="cn">${ic('bub')}${i.comments.length}</span>`:''}
    ${i.days!=null?`<span class="age${ageCls(i.days)}">${i.days}d</span>`:''}</span>
  <div class="pnl">
    ${i.detail?`<p class="full">${esc(i.detail)}</p>`:''}
    ${i.comments.map(c=>`<div class="cmt">${esc(c.text)}<time>${esc(c.ts.slice(0,10))}</time></div>`).join('')}
    <input class="note" placeholder="Add a note…">
    ${i.kind==='owed'&&i.msg?`<div class="mv"><b>Reply</b>
      <button class="draft" data-a="draft">Draft it in Gmail</button>
      <span class="dstat"></span></div>`:''}
    <div class="mv"><b>Move</b>
      ${BUCKETS.map(([k,l])=>`<button data-b="${k}"${i.bucket===k?' aria-current="true"':''}>${l}</button>`).join('')}
      ${i.kind==='manual'?'<button class="rm" data-a="remove">Delete</button>':''}
    </div></div></div>`}

// Drafting is a whole assistant run — 30 to 90 seconds — so the button says
// what it is doing. It only ever writes to Gmail's Drafts; nothing here can
// send, and the server derives the recipient from the message id rather than
// from anything typed or generated.
async function draft(n,btn,id){
  const item=G.find(x=>x.id===id); if(!item||!item.msg) return;
  const stat=n.querySelector('.dstat');
  btn.disabled=true; btn.textContent='Writing…';
  stat.textContent='Kiran is reading the thread. This takes a minute.';
  try{
    const r=await fetch('/api/draft',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({msg:item.msg,who:item.title,subject:item.detail,days:item.days})});
    const d=await r.json();
    if(!r.ok||d.error){throw new Error(d.error||('HTTP '+r.status))}
    btn.textContent='Drafted';
    stat.innerHTML='In your Gmail drafts, to '+esc((d.draft.to||[]).join(', '))
      +'. <a href="https://mail.google.com/mail/u/0/#drafts" target="_blank" rel="noopener">Open drafts</a>';
  }catch(err){
    btn.disabled=false; btn.textContent='Draft it in Gmail';
    stat.textContent=String(err.message||err);
  }
}

function group(key,label,rows,o={}){
  // A saved choice beats the default. With `&& o.open!==false` the preference
  // could never win for Waiting-on-you or Done, so those slammed shut on every
  // write and took your scroll with them.
  const saved=localStorage.getItem('cos.g.'+key);
  const open = saved!=null ? saved==='1' : o.open!==false;
  return `<div class="grp" data-bucket="${key}"${o.src?` data-src="${o.src}"`:''} data-open="${open?1:0}">
    <button class="ghead" aria-expanded="${open?'true':'false'}"><span class="i chev">${ICO.chev}</span><span>${label}</span>
      <span class="cnt">${rows.length+(o.more||0)}</span>
      ${o.sum?`<span class="gsum">${o.sum}</span>`:''}</button>
    <div class="rows">${rows.map(row).join('')||'<div class="empty">Nothing here</div>'}</div>
    ${o.more?`<button class="more">+ ${o.more} more</button>`:''}
    ${o.add?`<div class="add"><span class="plus">+</span>
      <input class="newitem" data-bucket="${key}" placeholder="Add to ${label}…"></div>`:''}
  </div>`}

function renderList(){
  // Every write re-renders the whole view. That closed the open row, wiped a
  // half-typed note, dropped focus and jumped to the top — so the everyday
  // "open a row, type a note, press Enter" ended with you at the top of the
  // page unable to see what you just wrote.
  const sc=$('scroll');
  const keep={top:sc?sc.scrollTop:0,
    open:document.querySelector('.r.open')?.dataset.id||null,
    note:document.querySelector('.r.open .note')?.value||'',
    focus:document.activeElement?.closest?.('.r')?.dataset.id||null};
  const live=G.filter(i=>!i.done),done=G.filter(i=>i.done);
  const mine=live.filter(i=>i.kind!=='owed'&&i.kind!=='quiet');
  const mail=live.filter(i=>i.kind==='owed'||i.kind==='quiet').sort((a,b)=>(b.days||0)-(a.days||0));
  const showMail=window.__allmail?mail.length:5;
  const over=mail.filter(m=>(m.days||0)>30).length;
  let h='';
  if(D.error) h+=`<div class="banner">${esc(D.error)}</div>`;
  if(D.stale) h+=`<div class="banner">These numbers are ${D.age_minutes} minutes old — the refresh may be stuck.</div>`;
  (A.warnings||[]).forEach(w=>h+=`<div class="banner">${esc(w)}</div>`);
  h+=BUCKETS.map(([k,l])=>group(k,l,mine.filter(i=>i.bucket===k),{add:true})).join('');
  // Clamped. With fewer than five people waiting the subtraction went
  // negative, so the collapsed header read "WAITING ON YOU  -1" and the group
  // offered a "+ -3 more" button — on a phone that header is often the only
  // part of the group on screen.
  if(mail.length) h+=group('__mail','Waiting on you',mail.slice(0,showMail),
    {src:'mail',open:false,more:Math.max(0,mail.length-showMail),
     sum:`oldest ${mail[0].days}d · ${over} over 30d`});
  if(done.length) h+=group('__done','Done',done,{open:false});
  const bad=(D.health||[]).filter(c=>c.status!=='ok');
  if(bad.length) h+=`<div class="card"><h2>Needs attention</h2>${
    bad.map(c=>`<div class="hl"><span class="pill ${c.status}">${c.status}</span>
      <span><b>${esc(c.name)}</b> — ${esc(c.detail)}</span></div>`).join('')}</div>`;
  $('view').innerHTML=h;
  wireList();
  if(keep.open){
    const r=document.querySelector(`.r[data-id="${CSS.escape(keep.open)}"]`);
    if(r){r.classList.add('open');
      const n=r.querySelector('.note'); if(n) n.value=keep.note;}
  }
  if(keep.focus)
    document.querySelector(`.r[data-id="${CSS.escape(keep.focus)}"]`)?.focus();
  if(sc) sc.scrollTop=keep.top;
}

/* ── prospects ───────────────────────────────────────────────────── */
// Wei: "tasks panel has good information density, prospects is too loose."
// So this is the Tasks skeleton, reused: the same collapsible groups, the
// same one-line rows, the same click-to-expand panel. One group per stage,
// one line per deal — name, newest note, days quiet. The database is the
// master copy; days quiet and the last email are computed and display-only.
function prow(p,showStage,states){
  const notes=(p.notes&&p.notes.length)?p.notes:(p.next?[{ts:'',text:p.next}]:[]);
  const latest=notes[notes.length-1];
  const earlier=notes.slice(0,-1).reverse();
  return `<div class="r" data-pid="${p.id}" data-ball="${p.ball||''}" tabindex="0"
     ${latest?` title="${esc(latest.text)}"`:''}>
    <span class="grip">${ICO.grip}</span>
    <span class="pdot${p.ball==='you'?' you':p.ball===undefined?' none':''}" title="${p.ball==='you'?'your move':p.ball==='them'?'their move':''}"></span>
    <span class="txt"><span class="ttl">${p.focus?'<span class="hotstar" title="Needs attention">★</span> ':''}${esc(p.name)}</span>
      ${showStage&&p.stage?`<span class="stg">${esc(p.stage)}</span>`:''}
      ${latest?`<span class="dtl">${esc(latest.text)}</span>`:''}</span>
    <span class="meta">
      ${notes.length>1?`<span class="cn">${ic('bub')}${notes.length}</span>`:''}
      ${p.days!=null?`<span class="age${ageCls(p.days)}">${p.days}d</span>`:''}</span>
    <div class="pnl">
      ${latest?`<p class="full"><time style="color:var(--muted)">${esc(latest.ts)}</time> ${esc(latest.text)}</p>`:''}
      ${earlier.map(n=>`<div class="cmt">${esc(n.text)}<time>${esc(n.ts)}</time></div>`).join('')}
      ${p.last_subject?`<p class="full" style="color:var(--muted)">Last email: ${esc(p.last_subject)}${p.last_from?' — '+esc(p.last_from):''}</p>`:''}
      <input class="note" placeholder="Add a note…">
      <div class="mv"><b>Attention</b>
        <button class="pfoc" data-on="${p.focus?'0':'1'}">${p.focus?'★ Remove from top':'☆ Needs attention now'}</button>
      </div>
      <div class="mv"><b>Stage</b>
        ${(states||[]).map(s=>`<button data-s="${esc(s)}"${p.stage===s?' aria-current="true"':''}>${esc(s)}</button>`).join('')}
        <button class="rm" data-a="arch">Archive</button>
      </div></div></div>`}

// One renderer for every panel of this shape. Prospects was the prototype;
// a panel created on demand ("add a GTM panel") gets the same machinery —
// stage groups, dated notes, drag, the attention list — with no mail overlay.
function renderPanel(pid){
  pid=pid||PID()||'prospects';
  const sc=$('scroll');
  const keep={top:sc?sc.scrollTop:0,
    open:document.querySelector('.r.open')?.dataset.pid||null,
    note:document.querySelector('.r.open .note')?.value||''};
  const PD=PDATA(pid);
  const pros=PD.rows;
  const states=PD.states;
  const groups=[...states.filter(s=>pros.some(p=>p.stage===s&&!p.focus)),
                ...(pros.some(p=>!states.includes(p.stage)&&!p.focus)?['']:[])];
  let h='';
  if(PD.mail&&D.stale) h+=`<div class="banner">These numbers are ${D.age_minutes} minutes old — the refresh may be stuck.</div>`;

  // Needs attention now. A separate list, not a stage: urgency this week
  // says nothing about whether a deal is Qualified or Engaged, and folding
  // the two would lose the stage the moment you flagged something. Drag a
  // row in, or use the star in its panel.
  const hot=pros.filter(p=>p.focus)
    .sort((a,b)=>(a.focus_pos||0)-(b.focus_pos||0));
  const hkey=pid+':focus';
  const hsaved=localStorage.getItem('cos.g.'+hkey);
  const hopen=hsaved!=null?hsaved==='1':true;
  h+=`<div class="grp focusg" data-bucket="${hkey}" data-focus="1" data-open="${hopen?1:0}">
    <button class="ghead" aria-expanded="${hopen?'true':'false'}"><span class="i chev">${ICO.chev}</span><span>Needs attention now</span>
      <span class="cnt">${hot.length}</span></button>
    <div class="rows">${hot.map(p=>prow(p,true,states)).join('')
      ||`<div class="empty"><span class="deskonly">Drag an item here, or open one and press the star.</span><span class="mobonly">Tap a row, then press ☆ Needs attention now.</span></div>`}</div>
  </div>`;

  h+=groups.map(g=>{
    const rows=pros.filter(p=>!p.focus&&(g===''?!states.includes(p.stage):p.stage===g));
    const days=rows.map(p=>p.days).filter(d=>d!=null);
    const yours=rows.filter(p=>p.ball==='you').length;
    const bits=[];
    if(yours) bits.push(yours+' your move');
    if(days.length) bits.push('quietest '+Math.max(...days)+'d');
    const key=pid+':'+(g||'none');
    const saved=localStorage.getItem('cos.g.'+key);
    const open=saved!=null?saved==='1':true;
    return `<div class="grp" data-bucket="${key}" data-state="${esc(g)}" data-open="${open?1:0}">
      <button class="ghead" aria-expanded="${open?'true':'false'}"><span class="i chev">${ICO.chev}</span><span>${esc(g||'No stage')}</span>
        <span class="cnt">${rows.length}</span>
        ${bits.length?`<span class="gsum">${bits.join(' · ')}</span>`:''}</button>
      <div class="rows">${rows.map(p=>prow(p,false,states)).join('')||'<div class="empty">Nothing here</div>'}</div>
    </div>`}).join('');
  h+=`<div class="add"><span class="plus">+</span>
    <input class="newpros" placeholder="${pid==='prospects'?'Add a prospect…':'Add to '+esc(PD.title)+'…'}"></div>`;
  $('view').innerHTML=h;
  wirePanel(pid,pros,states);
  if(keep.open){
    const r=document.querySelector(`.r[data-pid="${CSS.escape(keep.open)}"]`);
    if(r){r.classList.add('open');
      const n=r.querySelector('.note'); if(n) n.value=keep.note;}
  }
  if(sc) sc.scrollTop=keep.top;
}

async function panelAct(body){
  if(!body.panel) body.panel=PID()||'prospects';
  const r=await fetch('/api/panel',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  if(j.error){alert(j.error);return false}
  if(j.dashboard) D=j.dashboard;
  if(j.panels) PANELS=j.panels;
  if(PID()) renderPanel(PID());
  renderRail();
  return true;
}

function wirePanel(pid,pros,states){
  let dragged=null;
  document.querySelectorAll('.r[data-pid]').forEach(n=>{
    const id=n.dataset.pid;
    n.draggable=false;
    const g=n.querySelector('.grip');
    g.addEventListener('pointerdown',()=>n.draggable=true);
    n.addEventListener('dragend',()=>{n.draggable=false;
      n.classList.remove('dragging');
      document.querySelectorAll('.over').forEach(x=>x.classList.remove('over'));dragged=null});
    n.addEventListener('dragstart',e=>{dragged=n;n.classList.add('dragging');
      e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',id)});
    const open=()=>{const was=n.classList.contains('open');
      document.querySelectorAll('.r.open').forEach(x=>x.classList.remove('open'));
      if(!was){n.classList.add('open');n.querySelector('.note').focus()}};
    n.querySelector('.txt').onclick=open;
    n.addEventListener('keydown',e=>{if(e.target!==n)return;
      if(e.key==='Enter'){e.preventDefault();open()}});
    const note=n.querySelector('.note');
    note.onkeydown=e=>{
      if(e.key==='Escape'){n.classList.remove('open');n.focus()}
      if(e.key==='Enter'&&note.value.trim()){
        panelAct({action:'update',id,note:note.value.trim()})}};
    n.querySelectorAll('.mv button').forEach(b=>b.onclick=()=>{
      if(b.classList.contains('pfoc'))
        return panelAct({action:'focus',id,on:b.dataset.on==='1'});
      b.dataset.a==='arch'
        ?panelAct({action:'update',id,archived:true})
        :panelAct({action:'move',id,state:b.dataset.s})});
  });
  document.querySelectorAll('.ghead').forEach(h=>{const g=h.closest('.grp');
    h.onclick=()=>{const v=g.dataset.open==='1'?'0':'1';g.dataset.open=v;
      h.setAttribute('aria-expanded',v==='1'?'true':'false');
      localStorage.setItem('cos.g.'+g.dataset.bucket,v)}});
  document.querySelectorAll('.grp .rows').forEach(list=>{
    list.addEventListener('dragover',e=>{e.preventDefault();if(!dragged)return;
      list.classList.add('over');
      const after=[...list.querySelectorAll('.r:not(.dragging)')].find(x=>{
        const r=x.getBoundingClientRect();return e.clientY<r.top+r.height/2});
      after?list.insertBefore(dragged,after):list.appendChild(dragged)});
    list.addEventListener('dragleave',()=>list.classList.remove('over'));
    list.addEventListener('drop',async e=>{e.preventDefault();list.classList.remove('over');
      if(!dragged)return;
      const grp=list.closest('.grp');
      const pid=dragged.dataset.pid;
      // Dropping into the attention list flags it; dropping into a stage
      // group both sets the stage and clears the flag, so dragging a row
      // back out is how you say "handled".
      if(grp.dataset.focus){
        const sibs=[...list.querySelectorAll('.r')],at=sibs.indexOf(dragged);
        return panelAct({action:'focus',id:pid,on:true,
                         above:sibs[at+1]?.dataset.pid||null})}
      const was=pros.find(p=>p.id===pid);
      if(was&&was.focus) await panelAct({action:'focus',id:pid,on:false});
      const sibs=[...list.querySelectorAll('.r')],at=sibs.indexOf(dragged);
      await panelAct({action:'move',id:pid,state:grp.dataset.state,
        above:sibs[at+1]?.dataset.pid||null})});
  });
  const add=document.querySelector('.newpros');
  if(add) add.onkeydown=e=>{
    if(e.key==='Enter'&&add.value.trim()){
      panelAct({action:'add',name:add.value.trim()}).then(ok=>{if(ok)add.value=''})}};
}

function wireList(){
  document.querySelectorAll('.r').forEach(n=>{
    const id=n.dataset.id;
    n.draggable=false;
    const g=n.querySelector('.grip');
    if(g){g.addEventListener('pointerdown',()=>n.draggable=true);
      n.addEventListener('dragend',()=>n.draggable=false);
      document.addEventListener('pointerup',()=>n.draggable=false,{once:true})}
    n.querySelector('.tick').onclick=e=>{e.stopPropagation();
      post({id,action:n.classList.contains('done')?'undone':'done'})};
    const open=()=>{const was=n.classList.contains('open');
      document.querySelectorAll('.r.open').forEach(x=>x.classList.remove('open'));
      if(!was){n.classList.add('open');n.querySelector('.note').focus()}};
    n.querySelector('.txt').onclick=open;
    n.addEventListener('keydown',e=>{if(e.target!==n)return;
      if(e.key==='Enter'){e.preventDefault();open()}
      if(e.key==='x'&&!e.metaKey&&!e.ctrlKey&&!e.altKey){
        e.preventDefault();n.querySelector('.tick').click()}});
    const note=n.querySelector('.note');
    note.onkeydown=e=>{
      if(e.key==='Escape'){n.classList.remove('open');n.focus()}
      if(e.key==='Enter'&&note.value.trim()){post({id,action:'comment',text:note.value.trim()});note.value=''}};
    n.querySelectorAll('.mv button').forEach(b=>b.onclick=()=>{
      if(b.dataset.a==='draft') return draft(n,b,id);
      b.dataset.a==='remove'?post({id,action:'remove'}):post({id,action:'move',bucket:b.dataset.b})});
  });
  document.querySelectorAll('.ghead').forEach(h=>{const g=h.closest('.grp');
    h.onclick=()=>{const v=g.dataset.open==='1'?'0':'1';g.dataset.open=v;
      h.setAttribute('aria-expanded',v==='1'?'true':'false');
      localStorage.setItem('cos.g.'+g.dataset.bucket,v)}});
  document.querySelectorAll('.more').forEach(b=>b.onclick=()=>{window.__allmail=1;renderList()});
  document.querySelectorAll('.newitem').forEach(i=>i.onkeydown=e=>{
    if(e.key==='Enter'&&i.value.trim()){post({action:'add',title:i.value.trim(),bucket:i.dataset.bucket});i.value=''}});
  wireDrag();
}

function wireDrag(){
  let dragged=null;
  document.querySelectorAll('.r').forEach(n=>{
    n.addEventListener('dragstart',e=>{dragged=n;n.classList.add('dragging');
      e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',n.dataset.id)});
    n.addEventListener('dragend',()=>{n.classList.remove('dragging');
      document.querySelectorAll('.over').forEach(x=>x.classList.remove('over'));dragged=null});
  });
  document.querySelectorAll('.rows').forEach(list=>{
    list.addEventListener('dragover',e=>{e.preventDefault();if(!dragged)return;
      list.classList.add('over');
      const after=[...list.querySelectorAll('.r:not(.dragging)')].find(x=>{
        const r=x.getBoundingClientRect();return e.clientY<r.top+r.height/2});
      after?list.insertBefore(dragged,after):list.appendChild(dragged)});
    list.addEventListener('dragleave',()=>list.classList.remove('over'));
    list.addEventListener('drop',async e=>{e.preventDefault();list.classList.remove('over');
      if(!dragged)return;
      const bucket=list.closest('.grp').dataset.bucket;
      if(bucket.startsWith('__')){renderList();return}
      const sibs=[...list.querySelectorAll('.r')],at=sibs.indexOf(dragged);
      await post({id:dragged.dataset.id,action:'move',bucket,
        above:sibs[at-1]?.dataset.id||null,below:sibs[at+1]?.dataset.id||null})});
  });
}

async function post(body){
  const r=await fetch('/api/agenda',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  if(j.items){G=j.items;renderList();renderRail()}
}

/* ── chat ────────────────────────────────────────────────────────── */
function stage(t){
  const s=Math.round(t.elapsed||0);
  if(t.status!=='running') return '';
  if(t.queued) return t.queue_position>1
    ? `Waiting — ${t.queue_position} questions ahead of it`
    : 'Waiting for the question ahead of it';
  if(s<3) return 'Looking…';
  if(s<10) return `Reading ${t.hits?.length||0} pages…`;
  if(s<45) return `Thinking · ${s}s`;
  if(s<120) return `Still thinking · ${s}s — the sources below are already real`;
  return `Unusually long · ${s}s`;
}
function turn(t,n){
  const live=t.status==='running';
  const srcs=(t.hits||[]).map(h=>`<div class="src" data-slug="${esc(h.slug)}">
    <div class="smain"><div class="st">${esc(h.title)}</div>
      <div class="sm"><span class="kind">${esc(h.kind)}</span>${h.date?`<span>${esc(h.date)}</span>`:''}</div>
      ${h.excerpt?`<div class="sx">${esc(h.excerpt)}</div>`:''}</div>
    <button class="sadd" data-add="${esc(h.slug)}">+ Today</button></div>`).join('');
  return `<div class="turn" data-n="${n}">
    <div class="you"><span>${esc(t.question)}</span></div>
    <div class="kir"><span class="kav">${esc((S['agent.name']||'K')[0])}</span>
      <div class="kbody">
        ${t.cached_age!=null&&t.cached_age>60?`<div class="cached">Answered ${Math.round(t.cached_age/60)} min ago — <button class="chip" data-again="${n}">ask again</button></div>`:''}
        ${live?`<div class="stage"><span class="blip"></span>${esc(stage(t))}</div>`:''}
        ${t.answer?`<div class="ans">${md(t.answer,true)}</div>`:''}
        ${t.error?`<div class="banner" style="margin:0">${esc(t.error)}</div>`:''}
        ${srcs?(t.answer
          ? `<details class="srcs"><summary class="srch">${(t.hits||[]).length} sources</summary>${srcs}</details>`
          : `<div class="srcs"><div class="srch">Found so far</div>${srcs}</div>`):''}
        ${!live?`<div class="acts">
          ${t.follow_up?`<button class="chip go" data-ask="${esc(t.follow_up)}">${esc(t.follow_up)}</button>`:''}
          <button class="chip" data-copy="${n}">Copy</button>
          <button class="chip" data-again="${n}">Ask again</button></div>`:''}
      </div></div></div>`}

function renderChat(){
  const live=CHAT.some(t=>t.status==='running');
  const name=S['agent.name']||'cos';
  document.title = live ? '· '+name : (document.hidden&&CHAT.length?'✓ '+name:name);
  $('view').innerHTML=CHAT.length
    ? CHAT.map(turn).join('')
    : `<div class="blank"><h2>Ask ${esc(S['agent.name']||'Kiran')}</h2>
       <p>Anything you'd ask on Telegram — “when did I last talk to Northwind”.</p></div>`;
  document.querySelectorAll('[data-ask]').forEach(b=>b.onclick=()=>ask(b.dataset.ask));
  document.querySelectorAll('[data-again]').forEach(b=>b.onclick=()=>
    ask(CHAT[b.dataset.again].question,true));
  document.querySelectorAll('[data-copy]').forEach(b=>b.onclick=()=>{
    navigator.clipboard.writeText(CHAT[b.dataset.copy].answer||'');
    b.textContent='Copied';setTimeout(()=>b.textContent='Copy',1400)});
  document.querySelectorAll('.src').forEach(n=>n.onclick=e=>{
    if(e.target.dataset.add)return; openPage(n.dataset.slug)});
  document.querySelectorAll('[data-add]').forEach(b=>b.onclick=async e=>{
    e.stopPropagation();
    const t=CHAT.find(t=>(t.hits||[]).some(h=>h.slug===b.dataset.add));
    const h=t.hits.find(h=>h.slug===b.dataset.add);
    b.textContent='Added';b.disabled=true;
    await post({action:'add',title:h.title,detail:h.slug,bucket:'today'})});
  // A citation you cannot open is a claim you have to take on trust.
  document.querySelectorAll('.slug').forEach(a=>{
    a.onclick=()=>openPage(a.dataset.slug);
    a.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openPage(a.dataset.slug)}}});
}

async function openPage(slug){
  closePage();
  const scrim=el('<div class="dscrim"></div>');
  const dw=el(`<div class="drawer" role="dialog" aria-label="${esc(slug)}">
    <div class="dhead"><div style="flex:1 1 auto;min-width:0">
      <h3>${esc(slug.split('/').pop().replace(/-/g,' '))}</h3>
      <div class="dslug">${esc(slug)}</div></div>
      <button class="iconb" aria-label="Close">${ICO.close}</button></div>
    <div class="dbody">Opening…</div></div>`);
  scrim.onclick=closePage; dw.querySelector('.iconb').onclick=closePage;
  document.body.append(scrim,dw);
  dw.querySelector('.iconb').focus();
  const j=await (await fetch('/api/page?slug='+encodeURIComponent(slug))).json();
  dw.querySelector('.dbody').innerHTML = j.error
    ? `<div class="banner" style="margin:0">${esc(j.error)}</div>`
    : md(j.markdown,false);
}
function closePage(){document.querySelectorAll('.dscrim,.drawer').forEach(n=>n.remove())}

// What is on screen, as text, so a question typed under a panel is about
// that panel. "Which of these should I chase first?" was going to whatever
// the last chat happened to be about — the box looked panel-specific and
// was not.
function screenText(){
  if(MODE==='pros'){
    const rows=(D.prospects||[]).map(p=>{
      const n=(p.notes&&p.notes.length)?p.notes[p.notes.length-1]:null;
      return `- ${p.focus?'[NEEDS ATTENTION] ':''}${p.name} · ${p.stage||'no stage'} · ${p.days==null?'no contact':p.days+' days quiet'}${p.ball?` · ball: ${p.ball}`:''}`
        +(n?` · note (${n.ts}): ${n.text}`:p.next?` · note: ${p.next}`:'');}).join('\n');
    return rows?'Prospects panel — the tracked deals:\n'+rows:'';
  }
  if(MODE.startsWith('p:')){
    const PD=PDATA(MODE.slice(2));
    const rows=PD.rows.map(p=>{
      const n=(p.notes&&p.notes.length)?p.notes[p.notes.length-1]:null;
      return `- ${p.focus?'[NEEDS ATTENTION] ':''}${p.name} · ${p.stage||'no state'}`
        +(n?` · note (${n.ts}): ${n.text}`:'');}).join('\n');
    return `${PD.title} panel (id: ${MODE.slice(2)}) — the items:\n`
      +(rows||'(empty)');
  }
  if(MODE==='list'){
    const live=G.filter(i=>!i.done);
    const mine=live.filter(i=>i.kind!=='owed'&&i.kind!=='quiet')
      .map(i=>`- [${i.bucket}] ${i.title||''}${i.detail?' — '+i.detail:''}`);
    const mail=live.filter(i=>i.kind==='owed')
      .map(i=>`- waiting ${i.days||'?'}d: ${i.title||''} — ${i.detail||''}`);
    const parts=[];
    if(mine.length) parts.push('Tasks panel — the to-do list:\n'+mine.join('\n'));
    if(mail.length) parts.push('People waiting on a reply:\n'+mail.join('\n'));
    return parts.join('\n');
  }
  return '';
}

async function ask(q,fresh){
  q=(q||'').trim(); if(!q) return;
  const screen=screenText();
  // A question from a panel starts its own chat. Appending it to whatever
  // conversation was last open buried the answer under an unrelated thread.
  if(!CUR||screen) await newChat();
  show('chat');
  const r=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({question:q,fresh:!!fresh,session:CUR,screen})});
  const j=await r.json();
  if(j.error){CHAT.push({question:q,status:'failed',error:j.error,hits:[]});renderChat();toBottom();return}
  CHAT.push(j);renderChat();toBottom();
  if(j.status==='running') schedule();
  else refreshSessions();
}
// Newest at the bottom, as in ChatGPT and Claude: the conversation reads top
// to bottom and the newest thing is nearest the box you type in. Only follow
// the tail if the user is already near it, so reading back through a long
// chat is not yanked away when an answer lands.
function toBottom(force){
  const s=$('scroll');
  if(force===false) return;
  requestAnimationFrame(()=>{s.scrollTop=s.scrollHeight});
}
function nearBottom(){
  const s=$('scroll');
  return s.scrollHeight - s.scrollTop - s.clientHeight < 120;
}
function schedule(){if(!POLL)POLL=setTimeout(poll,700)}
// Nothing re-fetched after boot, so a tab left open all day showed the same
// numbers under a footer timestamp that reads as live.
async function refreshData(){
  try{
    const [b,c,p]=await Promise.all([fetch('/api/dashboard'),fetch('/api/agenda'),
      fetch('/api/panels')]);
    D=await b.json(); G=(await c.json()).items||[];
    try{PANELS=(await p.json()).panels||[]}catch(e){}
    if(MODE==='list') renderList();
    if(PID()) renderPanel(PID());
    renderRail();
  }catch(e){}
}
async function refreshSessions(){
  SES=(await (await fetch('/api/chats')).json()).sessions||[];renderSessions()}
async function poll(){
  POLL=null;
  const live=CHAT.filter(t=>t.status==='running');
  if(!live.length) return;
  await Promise.all(live.map(async t=>{
    const r=await fetch('/api/ask/'+t.id);
    if(r.status===404){
      // Jobs live in memory and are capped, and the server restarts. Without
      // this the turn stays 'running', the blip animates forever and the page
      // polls a 404 every second for as long as the tab is open.
      t.status='failed';
      t.error='Lost track of this answer — the assistant restarted. Ask again.';
      return;
    }
    if(!r.ok)return;
    Object.assign(t,await r.json())}));
  const follow=nearBottom();
  renderChat();
  if(follow) toBottom();
  // An answer may have edited a panel — archived a deal, added a note — so
  // refetch the data with the sessions, or the change only shows a minute
  // later and "archived" reads as a lie in the meantime.
  if(live.some(t=>t.status!=='running')){refreshSessions();refreshData()}
  POLL=setTimeout(poll, live.some(t=>(t.elapsed||0)>60)?2000:1000);
}

/* ── view switch ─────────────────────────────────────────────────── */
function show(m){
  MODE=m;
  m==='list'?renderList():PID()?renderPanel(PID()):renderChat();
  renderRail();
  $('chint').textContent = m!=='chat' ? ''
    : (matchMedia('(hover:none)').matches
        ? 'Tap the arrow to ask'
        : 'Enter to ask · Shift+Enter for a new line');
}

/* ── settings ────────────────────────────────────────────────────── */
const GROUPS=[['setup','Setup'],['model','Model'],['data','Data & access'],
              ['perm','Permissions'],['tune','Rhythm']];
let SGROUP='setup';
// Each field is one grid child and carries a real label. Without the wrapper
// a .row2 received LABEL,INPUT,LABEL,INPUT as four children of a two-column
// grid, so labels sat BESIDE their inputs. Without for/id every input in the
// sheet was anonymous to a screen reader.
const fid=k=>'s-'+k.replace(/[^a-z0-9]+/gi,'-');
function field(k,l,note,type='text'){return `<div class="f">
  <label for="${fid(k)}">${l}${note?` <span class="note">— ${note}</span>`:''}</label>
  <input id="${fid(k)}" data-k="${k}" type="${type}" value="${esc(S[k]??'')}"></div>`}
function area(k,l,note){const v=S[k];const t=Array.isArray(v)?v.join('\n'):(v??'');
  return `<div class="f"><label for="${fid(k)}">${l}${note?` <span class="note">— ${note}</span>`:''}</label>
  <textarea id="${fid(k)}" data-k="${k}">${esc(t)}</textarea></div>`}
function tog(k,l){return `<div class="toggle">
  <input id="${fid(k)}" data-k="${k}" type="checkbox" ${S[k]?'checked':''}>
  <label for="${fid(k)}" style="margin:0">${l}</label></div>`}

function renderSettings(){
  $('srail').innerHTML=GROUPS.map(([k,l])=>
    `<button data-g="${k}"${SGROUP===k?' aria-current="true"':''}>${l}</button>`).join('');
  $('srail').querySelectorAll('[data-g]').forEach(b=>b.onclick=()=>{SGROUP=b.dataset.g;renderSettings()});
  const P={
    setup:`<h2>Who this is for</h2><p class="hint">Your assistant's name is what it calls itself.</p>
      ${field('agent.name','Assistant name','e.g. Kiran, Ada')}
      <div class="row2">${field('owner.name','Your name')}${field('owner.company','Company')}</div>
      ${area('owner.addresses','Your email addresses','one per line')}
      ${tog('agent.paused','Pause scheduled work')}`,
    model:`<h2>Answering model</h2>
      <div class="banner">Saved here, but the running assistant still reads its model from its own config.</div>
      <div class="row2">${field('model.provider','Provider')}${field('model.id','Model')}</div>
      ${field('model.base_url','Base URL','blank for the default')}
      ${field('model.api_key','API key','blank keeps the saved key','password')}
      <h2 style="margin-top:22px">Extraction model</h2>
      <p class="hint">Reads your mail in the background. Local by default — no cost.</p>
      <div class="row2">${field('model.extract_provider','Provider')}${field('model.extract_id','Model')}</div>
      ${field('model.extract_base_url','Base URL')}`,
    data:`<h2>Where it reads</h2>
      ${field('source.vault_root','Notes folder',`now <code>${esc(A.vault_root||'—')}</code>`)}
      ${A.google?`<h2 style="margin-top:22px">Google</h2><table>
        <tr><td>Mail</td><td>${esc(A.google.address)}</td></tr>
        <tr><td>Access</td><td>${(A.google.scopes||[]).map(s=>
          `<span class="pill ok">${esc(s.split('/').pop())}</span>`).join(' ')}</td></tr></table>
        <p class="hint" style="margin-top:8px">Read only — cannot send, delete or change anything.</p>`:''}
      <h2 style="margin-top:22px">Where it can write</h2>
      <label>The assistant's own files</label>
      <input data-k="write.hermes_safe_root" value="${esc(S['write.hermes_safe_root']||'')}"
        placeholder="${esc(A.hermes_safe_root||'')}">
      <p class="hint">Now: <code>${esc(A.hermes_safe_root||'not set')}</code></p>
      ${area('write.roots','Folders in your notes it may update','one per line')}
      <div class="banner">Folders holding your keys, settings or this program's code are refused.</div>`,
    perm:`<h2>Sending email</h2>
      <div class="banner">Not connected yet — there is no code in this system that can send.</div>
      ${tog('send.enabled','Allow sending')}
      ${area('send.allowed','Allowed recipients','one per line')}
      ${field('send.delay_minutes','Cancel window (minutes)','','number')}
      <h2 style="margin-top:22px">Who can talk to it</h2>
      ${area('telegram.allowed','Allowed on Telegram','one per line')}`,
    tune:`<h2>Daily summary</h2>
      <div class="row2">${field('digest.time','Time','24h')}${field('digest.target','Send to')}</div>
      <div class="row2">${field('quiet_hours.start','Quiet from')}${field('quiet_hours.end','Quiet until')}</div>
      <h2 style="margin-top:22px">Thresholds</h2>
      <div class="row2">${field('report.quiet_days','Quiet after (days)','','number')}
        ${field('report.owed_window_days','Look back (days)','','number')}</div>`};
  $('spanel').innerHTML=P[SGROUP]+
    `<div style="margin-top:20px"><button class="save">Save</button><span class="saved" hidden></span></div>`;
  $('spanel').querySelector('.save').onclick=saveSettings;
}
async function saveSettings(){
  const b=$('spanel').querySelector('.save'),msg=$('spanel').querySelector('.saved');
  const payload={};
  $('spanel').querySelectorAll('[data-k]').forEach(i=>
    payload[i.dataset.k]=i.type==='checkbox'?i.checked:i.value);
  b.disabled=true;
  const j=await (await fetch('/api/settings',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})).json();
  b.disabled=false;
  if(j.settings)S=j.settings;
  $('spanel').querySelectorAll('.rej').forEach(x=>x.remove());
  const errs=Object.values(j.errors||{});
  if(errs.length)$('spanel').prepend(el(
    `<div class="banner rej"><b>Not saved</b><br>${errs.map(esc).join('<br>')}</div>`));
  msg.hidden=false;
  msg.textContent=errs.length?`${(j.changed||[]).length} saved, ${errs.length} refused`
    :((j.changed||[]).length?`Saved`:'No changes');
  setTimeout(()=>msg.hidden=true,3000);
  renderRail();
}
let LASTFOCUS=null;
function openSheet(){
  LASTFOCUS=document.activeElement;
  renderSettings();
  $('sheet').removeAttribute('aria-hidden');
  $('scrim').classList.add('on');$('sheet').classList.add('on');
  $('app').setAttribute('inert','');
  $('sheet').querySelector('input,button')?.focus();
}
function closeSheet(){
  $('scrim').classList.remove('on');$('sheet').classList.remove('on');
  $('sheet').setAttribute('aria-hidden','true');
  $('app').removeAttribute('inert');
  LASTFOCUS?.focus();
}

/* ── boot ────────────────────────────────────────────────────────── */
async function boot(){
  $('gear').innerHTML=ICO.gear; $('sclose').innerHTML=ICO.close;
  $('shut').innerHTML=ICO.panel; $('reopen').innerHTML=ICO.panel;
  $('i-search').innerHTML=ICO.search;
  const w=localStorage.getItem('cos.railw');
  if(w) document.documentElement.style.setProperty('--rail',w+'px');
  // Remembered, because "create focus" is a mode you stay in, not a gesture
  // you repeat every time the page loads.
  if(localStorage.getItem('cos.rail')==='0') $('app').classList.add('shut');
  $('send').innerHTML=ICO.up; $('i-plus').innerHTML=ICO.plus;
  const [a,b,c,p]=await Promise.all([fetch('/api/settings'),fetch('/api/dashboard'),
    fetch('/api/agenda'),fetch('/api/panels')]);
  const sj=await a.json(); S=sj.settings; A={...(sj.actual||{}),warnings:sj.warnings||[]};
  D=await b.json(); G=(await c.json()).items||[];
  try{PANELS=(await p.json()).panels||[]}catch(e){}
  try{SES=(await (await fetch('/api/chats')).json()).sessions||[]}catch(e){}
  document.title=(S['agent.name']||'cos');
  // Restore the conversation that was open. Losing it on a reload, or on a
  // detour to the list, was the "chat window is not stable" complaint.
  const last=localStorage.getItem('cos.chat');
  if(last && SES.some(s=>s.id===last)) await openSession(last);
  else show('list');
  renderRail();

  setInterval(()=>{if(!document.hidden) refreshData()},60000);
  addEventListener('visibilitychange',()=>{if(!document.hidden) refreshData()});

  $('gear').onclick=openSheet; $('sclose').onclick=closeSheet; $('scrim').onclick=closeSheet;
  $('newq').onclick=newChat;
  $('srchtoggle').onclick=()=>{
    const box=$('srchbox'); box.classList.toggle('on');
    if(box.classList.contains('on')) $('srchq').focus();
    else {$('srchq').value='';runSearch()}};
  let sdeb=null;
  $('srchq').oninput=()=>{clearTimeout(sdeb);sdeb=setTimeout(runSearch,180)};
  $('srchq').onkeydown=e=>{if(e.key==='Escape'){$('srchq').value='';runSearch();
    $('srchbox').classList.remove('on')}};

  // Resizable rail. Clamped at both ends: below ~180px the titles stop being
  // readable, and past ~460px it starts eating the column you work in.
  const grab=$('grab'); let startX=0,startW=0;
  const onMove=e=>{
    const w=Math.max(180,Math.min(460,startW+(e.clientX-startX)));
    document.documentElement.style.setProperty('--rail',w+'px')};
  const onUp=()=>{document.body.classList.remove('resizing');grab.classList.remove('on');
    removeEventListener('pointermove',onMove);removeEventListener('pointerup',onUp);
    localStorage.setItem('cos.railw',
      parseInt(getComputedStyle(document.documentElement).getPropertyValue('--rail')))};
  grab.addEventListener('pointerdown',e=>{
    e.preventDefault(); startX=e.clientX;
    try{grab.setPointerCapture(e.pointerId)}catch{}
    startW=parseInt(getComputedStyle(document.documentElement).getPropertyValue('--rail'));
    document.body.classList.add('resizing'); grab.classList.add('on');
    addEventListener('pointermove',onMove); addEventListener('pointerup',onUp)});
  const phone=()=>matchMedia('(max-width:820px)').matches;
  $('shut').onclick=()=>{
    if(phone()){closeRail();return}
    $('app').classList.add('shut');localStorage.setItem('cos.rail','0')};
  $('reopen').onclick=()=>{
    if(phone()){$('rail').classList.add('on');$('scrim').classList.add('on');return}
    $('app').classList.remove('shut');localStorage.setItem('cos.rail','1')};
  const q=$('q');
  const grow=()=>{q.style.height='auto';q.style.height=Math.min(q.scrollHeight,180)+'px'};
  q.addEventListener('input',grow);
  // The box is cleared before the request. If the server is unreachable —
  // it restarting, the laptop waking, wifi flapping — the sentence was simply
  // gone, with nothing on screen to say so.
  const submit=(v)=>{
    q.value='';grow();
    ask(v).catch(()=>{
      q.value=v; grow();
      CHAT.push({question:v,status:'failed',hits:[],
        error:'Could not reach the assistant — it may be restarting. '
              +'Your question is back in the box.'});
      renderChat(); toBottom();});
  };
  q.addEventListener('keydown',e=>{
    if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();submit(q.value)}});
  $('cbox').onsubmit=e=>{e.preventDefault();submit(q.value)};
  addEventListener('keydown',e=>{
    if(e.key==='Escape'){
      if(document.querySelector('.drawer'))return closePage();
      if($('rail').classList.contains('on'))return closeRail();
      if($('sheet').classList.contains('on'))return closeSheet();
      if(document.activeElement===q)q.blur();return}
    // Modifier shortcuts BEFORE the typing guard. They were all below it, so
    // every one of them died whenever the cursor was in the composer — which
    // is where the cursor lives.
    const mod=e.metaKey||e.ctrlKey, k=e.key.toLowerCase();
    if(mod&&k==='k'){e.preventDefault();return q.focus()}
    if(mod&&k==='f'){e.preventDefault();$('srchbox').classList.add('on');
      return $('srchq').focus()}
    if(mod&&e.shiftKey&&k==='o'){e.preventDefault();return newChat()}
    if(/^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName))return;
    if(e.key==='/'){e.preventDefault();q.focus()}
    if(e.key===','&&(e.metaKey||e.ctrlKey)){e.preventDefault();openSheet()}
    if(e.key==='\\'&&(e.metaKey||e.ctrlKey)){e.preventDefault();
      if(phone()) $('rail').classList.toggle('on');
      else ($('app').classList.contains('shut') ? $('reopen') : $('shut')).click()}
  });
}
boot();
</script>
"""


def rendered_page() -> str:
    """The page with its assets substituted in, built once per process."""
    global _RENDERED
    if _RENDERED is None:
        _RENDERED = PAGE.replace("{{LOGO}}", _logo())
    return _RENDERED


_RENDERED: str | None = None
