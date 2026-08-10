"""A dashboard and settings page, reachable over Tailscale and nowhere else.

Two design constraints drove everything here.

**No new dependencies.** The point of this project is that someone can install
it with one command. Pulling in a web framework to render six forms would work
against that, so this is `http.server` from the standard library and one
self-contained HTML page.

**Tailscale or localhost, nothing else.** Wei asked to reach this from his
laptop, and the obvious answer — bind to the LAN — is wrong here. His Ollama
is deliberately open on all interfaces so his other machine can use it, which
means his home network is already a place where an unauthenticated service
lives. This page decides *who Kiran is allowed to email*. It is a much higher
value target than a local model, and "anyone on the wifi" is not an acceptable
audience for it.

So every request's peer address is checked against loopback and Tailscale's
CGNAT range (100.64.0.0/10). Anything else gets 403 and a line in the log,
whatever the URL says. Tailscale does the authentication; this enforces that
Tailscale is the only way in.

The agent cannot reach any of this: its Telegram toolset is `hermes-telegram`,
`gbrain` and `clock`, with terminal, browser, code execution and web all
disabled. That is what makes "config is a user action" true rather than
hopeful — and `cos serve` checks it at startup rather than assuming it.
"""

from __future__ import annotations

import ipaddress
import re
import json
import os
import subprocess
import threading
from datetime import datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import settings as settings_mod
from .page import PAGE, rendered_page

DEFAULT_PORT = 8787

# Loopback, plus Tailscale's CGNAT block. Not the LAN — see the module docstring.
ALLOWED_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("100.64.0.0/10"),
]

# Toolsets that would let the agent reach this server and edit its own
# permissions. Checked at startup, not assumed.
DANGEROUS_TOOLSETS = {"web", "browser", "terminal", "code_execution"}

# The source-IP check is not enough on its own, and believing it was left this
# open for a night: a POST with `Content-Type: text/plain` is a CORS *simple
# request*, so it needs no preflight and carries no Origin restriction. Any web
# page in any browser on this machine or the tailnet could therefore rewrite
# the settings — including send.allowed, write.roots and the API key — because
# the request genuinely came from 127.0.0.1. Verified against the running
# server before this was written: it set agent.name to "PWNED" and returned
# 200.
#
# Three checks close it. A state-changing request must be application/json
# (which is NOT a simple request, so a cross-origin attempt needs a preflight
# this server refuses); its Host must be one we recognise (which also stops DNS
# rebinding, where an attacker domain re-resolves to 127.0.0.1 and becomes
# same-origin); and an Origin, if present, must be us.
JSON_TYPE = "application/json"


def _host_ok(host: str, port: int) -> bool:
    """Whether the Host header names this server.

    Without this, a hostile domain that resolves to 127.0.0.1 is same-origin
    to the browser and can read every conversation from /api/chats.
    """
    name = (host or "").rsplit(":", 1)[0].strip("[]").lower()
    if not name:
        return False
    if name in {"localhost", "127.0.0.1", "::1"}:
        return True
    ts = tailscale_ip()
    if ts and name == ts:
        return True
    # MagicDNS names are the normal way to reach a tailnet host.
    return name.endswith(".ts.net")


def peer_allowed(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in ALLOWED_NETS)


# The Mac app does not put `tailscale` on PATH, so the bare command finds
# nothing and the startup banner would claim Tailscale was absent on a machine
# that is plainly on it.
_TAILSCALE_BINS = [
    "tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
]


def tailscale_ip() -> str | None:
    for binary in _TAILSCALE_BINS:
        try:
            r = subprocess.run([binary, "ip", "-4"], capture_output=True,
                               text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0]
    # Last resort: read it off the interface. Works even with no CLI at all.
    try:
        import re

        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"inet (100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+)", out)
        return m.group(1) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def agent_can_reach_us() -> list[str]:
    """Toolsets that are enabled and would break the 'config is a user action'
    guarantee. Empty list means the guarantee holds."""
    cfg = Path.home() / ".hermes" / "config.yaml"
    if not cfg.exists():
        return []
    try:
        text = cfg.read_text(errors="replace")
    except OSError:
        return []
    disabled = set()
    in_block = False
    for line in text.splitlines():
        if line.strip().startswith("disabled_toolsets:"):
            in_block = True
            continue
        if in_block:
            s = line.strip()
            if s.startswith("- "):
                disabled.add(s[2:].strip())
            elif s and not s.startswith("#"):
                break
    return sorted(DANGEROUS_TOOLSETS - disabled)


# --------------------------------------------------------------------------
# Data for the dashboard


def _git_head() -> str:
    """The commit this process was started from.

    /api/page existed in the working tree and 404'd on the live server for an
    hour, because `cos serve` loads its modules once and launchd only restarts
    it on a crash. This project's whole monitoring thesis is "assert the
    indexed commit equals HEAD"; it was not applying that to its own server.
    """
    try:
        r = subprocess.run(["git", "-C", str(Path(__file__).resolve().parents[2]),
                            "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip()[:12] if r.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


RUNNING_COMMIT = _git_head()

SNAPSHOT = Path.home() / ".cos" / "dashboard.json"
# Older than this and the page says so rather than presenting stale numbers as
# current. The refresh runs every 15 minutes.
SNAPSHOT_STALE_MINUTES = 45


def read_snapshot() -> dict:
    """What the page actually serves.

    The first version computed this per request: health checks, a Gmail round
    trip, and a full ledger build. It took over five minutes, which is not a
    web page. Everything here is already produced by the 15-minute refresh, so
    that job writes a snapshot and this reads it — instant, and honest about
    its age.
    """
    try:
        data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "generated_at": None,
            "error": "No snapshot yet. It is written by the 15-minute refresh; "
                     "run `cos snapshot` to make one now.",
        }
    ts = data.get("generated_epoch")
    if ts:
        age_min = (datetime.now().timestamp() - ts) / 60
        data["age_minutes"] = round(age_min)
        data["stale"] = age_min > SNAPSHOT_STALE_MINUTES
    return data


def dashboard_data() -> dict:
    """Compute the snapshot. Slow — called by the refresh job, not by a page."""
    out: dict = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "generated_epoch": datetime.now().timestamp(),
    }

    try:
        from . import health as health_mod

        checks = health_mod.run_all()
        out["health"] = [
            {"name": c.name, "status": c.status, "detail": c.detail} for c in checks
        ]
        out["health_bad"] = sum(1 for c in checks if c.bad)
    except Exception as e:  # noqa: BLE001
        out["health"] = []
        out["health_error"] = f"{type(e).__name__}: {e}"

    try:
        from .draft_outcomes import classify, summarise

        out["drafts"] = summarise(classify())
    except Exception:  # noqa: BLE001
        out["drafts"] = None

    try:
        from .backend import open_backend
        from .cli import _build
        from .config import Config
        from .contacts import utc_now
        from .reports import deal_status, owed_replies
        from .vault import (attach_domains, load_deal_domains, load_deals,
                            load_internal_domains)

        cfg = Config.load()
        now = utc_now()
        # The panel database is the master copy for deals — Wei: "Database
        # take over as the master copy." The markdown files seeded it once
        # and are now a generated view.
        deals = _deals_from_db(cfg)
        attach_domains(deals, load_deal_domains(cfg.deal_domains_path))
        with open_backend(cfg) as client:
            ledger = _build(cfg, client, now)
        statuses = deal_status(deals, ledger, now)
        owed = owed_replies(ledger, now, cfg.owed_window_days,
                            internal_domains=load_internal_domains(cfg.deal_domains_path))
        out["owed"] = [
            {"days": i.days_waiting, "who": i.who or i.counterparty.address,
             "org": i.counterparty.domain, "subject": i.subject[:70],
             # The message a reply would be threaded to. It is the ONLY thing
             # that decides where a draft is addressed — see draft_broker.
             "msg": i.counterparty.last_inbound_id,
             "thread": i.counterparty.last_inbound_thread}
            for i in owed[:15]
        ]
        out["owed_total"] = len(owed)
        quiet = [s for s in statuses
                 if s.mapped and (s.days_quiet(now) or 0) >= cfg.quiet_days]
        quiet.sort(key=lambda s: s.days_quiet(now) or 0, reverse=True)
        out["quiet"] = [
            {"name": s.deal.name, "days": s.days_quiet(now),
             "ball": "you" if s.ball_in_our_court() else "them"}
            for s in quiet
        ]
        out["deal_domains"] = load_deal_domains(cfg.deal_domains_path)

        # The prospects panel: every mapped deal, not just the quiet ones.
        # Sorted by who needs attention first — your ball beats their ball,
        # and within that, quietest first. "Mapped" means we know the deal's
        # email domains, so the quiet-days number is real rather than absent.
        # Every row from the database, editable fields and all, with the
        # computed mail overlay where the deal's domains are known. Unmapped
        # deals still appear — a prospect with no email history yet is
        # exactly the one worth seeing.
        from . import paneldb

        by_name = {s.deal.name: s for s in statuses}
        rows = []
        for r in paneldb.list_items(paneldb.PROSPECTS):
            s = by_name.get(r["name"])
            rows.append(
                {"id": r["id"], "name": r["name"], "stage": r["state"],
                 "next": r["note"], "notes": r["notes"],
                 "focus": bool(r["extra"].get("focus")),
             "focus_pos": r["extra"].get("focus_pos", 0.0),
                 "focus_pos": r["extra"].get("focus_pos", 0.0),
                 "paper": "✅" in r["extra"].get("paper", ""),
                 "days": s.days_quiet(now) if s else None,
                 "ball": ("you" if s and s.ball_in_our_court() else
                          "them" if s else ""),
                 "last_from": (s.last_inbound_from or "") if s else "",
                 "last_subject": ((s.last_inbound_subject or "")[:70]
                                  if s else "")})
        rows.sort(key=lambda r: (r["ball"] != "you", -(r["days"] or 0)))
        out["prospects"] = rows
        out["prospect_states"] = paneldb.states(paneldb.PROSPECTS)
    except Exception as e:  # noqa: BLE001
        out["reports_error"] = f"{type(e).__name__}: {e}"

    return out


_REPORT_KEYS = ("owed", "owed_total", "quiet", "deal_domains", "prospects",
                "prospect_states")


def _repatch_prospects() -> None:
    """Refresh the snapshot's prospects rows from the database, keeping the
    computed mail overlay each row already had.

    A full refresh reads the whole mail ledger and takes minutes; an edit
    changed a name, a stage or a note, none of which the ledger knows about.
    So the owned fields are re-read and the computed ones carried over.
    """
    from . import paneldb

    if not SNAPSHOT.is_file():
        return
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    old = {r.get("id") or r.get("name"): r for r in data.get("prospects", [])}
    rows = []
    for r in paneldb.list_items(paneldb.PROSPECTS):
        prev = old.get(r["id"]) or old.get(r["name"]) or {}
        rows.append(
            {"id": r["id"], "name": r["name"], "stage": r["state"],
             "next": r["note"], "notes": r["notes"],
             "focus": bool(r["extra"].get("focus")),
             "focus_pos": r["extra"].get("focus_pos", 0.0),
             "paper": "✅" in r["extra"].get("paper", ""),
             "days": prev.get("days"), "ball": prev.get("ball", ""),
             "last_from": prev.get("last_from", ""),
             "last_subject": prev.get("last_subject", "")})
    rows.sort(key=lambda r: (r["ball"] != "you", -(r["days"] or 0)))
    data["prospects"] = rows
    data["prospect_states"] = paneldb.states(paneldb.PROSPECTS)
    tmp = SNAPSHOT.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(SNAPSHOT)


def _deals_from_db(cfg) -> list:
    """Deal objects for the mail-status computation, from the panel DB.

    Seeds the database from the markdown files on first run, so the takeover
    needs no manual step and an empty database never reads as 'no deals'.
    """
    from . import paneldb
    from .vault import Deal

    rows = paneldb.list_items(paneldb.PROSPECTS)
    if not rows:
        paneldb.seed_prospects(cfg.vault_root)
        rows = paneldb.list_items(paneldb.PROSPECTS)
    return [Deal(name=r["name"],
                 source_file=r["extra"].get("source_file", ""),
                 stage=r["state"], owner=r["extra"].get("owner", ""),
                 next_step=r["note"], paper=r["extra"].get("paper", ""))
            for r in rows]


def write_snapshot() -> Path:
    """Compute and store, keeping the last good report numbers if this run
    could not produce new ones.

    The ledger refuses to return a partial result — if a handful of threads
    fail to read, it raises rather than under-report who is waiting on you.
    That is the right call, but it happens on a small fraction of runs, and
    without this the dashboard would swing between "33 waiting" and blank.
    Blank reads as "nothing to do", which is the one wrong answer.

    So a failed run keeps the previous numbers and stamps them with their real
    age. Stale and labelled beats absent.
    """
    fresh = dashboard_data()
    if "reports_error" in fresh:
        try:
            previous = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        if any(k in previous for k in _REPORT_KEYS):
            for k in _REPORT_KEYS:
                if k in previous:
                    fresh[k] = previous[k]
            fresh["reports_as_of"] = previous.get("generated_at")

    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    tmp = SNAPSHOT.with_suffix(".tmp")
    tmp.write_text(json.dumps(fresh, indent=1), encoding="utf-8")
    tmp.replace(SNAPSHOT)
    return SNAPSHOT


# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "cos"

    def log_message(self, fmt, *args):  # quieter than the default
        pass

    # -- guard ------------------------------------------------------------
    def _guarded(self) -> bool:
        addr = self.client_address[0]
        if peer_allowed(addr):
            return True
        # Loud: someone reached this from a network it should not be on.
        print(f"[cos serve] REFUSED {addr} -> {self.path} "
              f"(only loopback and Tailscale 100.64.0.0/10 are allowed)")
        self._send(403, "text/plain", b"Forbidden: reach this over Tailscale.")
        return False

    def _draft(self, payload: dict) -> None:
        """Draft a reply to one of the people waiting, into Gmail's Drafts.

        The recipient comes from `msg` — the id of a message already in the
        snapshot this page is displaying — and never from the request body or
        from anything the model wrote. `draft_broker` derives every address and
        header from that message, and has no send path at all.
        """
        from . import drafting
        from .draft_broker import DraftError

        msg = (payload.get("msg") or "").strip()
        # Gmail message ids are hex. Anything else is not one, and this is the
        # only caller-supplied value that reaches Google.
        if not msg or not re.fullmatch(r"[0-9a-f]{5,32}", msg):
            self._json({"error": "That is not a message id."}, 400)
            return
        try:
            out = drafting.compose(
                msg,
                str(payload.get("who") or "them")[:120],
                str(payload.get("subject") or "")[:200],
                payload.get("days") if isinstance(payload.get("days"), int) else None,
            )
        except DraftError as e:
            self._json({"error": str(e)}, 400)
            return
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            return
        self._json({"draft": out})

    def _agenda(self, payload: dict) -> None:
        from . import agenda

        action = payload.get("action", "")
        try:
            if action == "add":
                item = agenda.add(payload.get("title", ""), payload.get("detail", ""))
                if payload.get("bucket"):
                    agenda.move(item.id, payload["bucket"])
                note = "added"
            elif action == "move":
                note = agenda.move(payload.get("id", ""), payload.get("bucket", ""),
                                   above=payload.get("above"),
                                   below=payload.get("below"))
            elif action == "remove":
                note = "removed" if agenda.remove(payload.get("id", "")) else "not found"
            else:
                note = agenda.act(payload.get("id", ""), action,
                                  payload.get("text", ""))
        except ValueError as e:
            self._json({"error": str(e)}, 400)
            return

        items = agenda.build()
        _mirror_agenda(items)
        print(f"[cos serve] list: {note}")
        self._json({"note": note, "items": [i.as_dict() for i in items]})

    def _panel(self, payload: dict) -> None:
        """Edits to the prospects panel. The database is the master copy;
        the markdown view is re-exported after every write, best-effort."""
        from . import paneldb

        action = payload.get("action", "")
        item_id = str(payload.get("id", ""))[:40]
        try:
            if action == "add":
                row = paneldb.add_item(paneldb.PROSPECTS,
                                       payload.get("name", ""),
                                       state=payload.get("state", ""),
                                       note=payload.get("note", ""))
                note = f"added {row['name']}"
            elif action == "update":
                row = paneldb.update_item(
                    item_id,
                    name=payload.get("name"),
                    state=payload.get("state"),
                    note=payload.get("note"),
                    archived=payload.get("archived"))
                note = f"updated {row['name']}"
            elif action == "move":
                row = paneldb.move_item(item_id, payload.get("state", ""),
                                        above_id=payload.get("above"))
                note = f"moved {row['name']} to {row['state'] or 'no stage'}"
            elif action == "focus":
                on = bool(payload.get("on"))
                if on and "above" in payload:
                    row = paneldb.move_focus(item_id, payload.get("above"))
                else:
                    row = paneldb.set_focus(item_id, on)
                note = (f"{row['name']} " +
                        ("needs attention" if on else "cleared from attention"))
            else:
                self._json({"error": f"unknown action {action!r}"}, 400)
                return
        except (ValueError, KeyError) as e:
            self._json({"error": str(e)}, 400)
            return

        try:
            from .config import Config
            paneldb.export_markdown(Config.load().vault_root)
        except Exception as e:  # noqa: BLE001
            # Best-effort, but never silent: a swallowed NameError here is
            # exactly how the first export quietly did not happen.
            print(f"[cos serve] panel export failed: {type(e).__name__}: {e}")
        # The snapshot's prospects block is stale until the next refresh;
        # rebuild just that part so the panel reflects the edit immediately.
        try:
            _repatch_prospects()
        except Exception:  # noqa: BLE001
            pass
        print(f"[cos serve] panel: {note}")
        self._json({"note": note, "dashboard": read_snapshot()})

    def _origin_ok(self, mutating: bool) -> bool:
        """Reject anything a hostile page could make the browser send."""
        port = self.server.server_address[1]
        if not _host_ok(self.headers.get("Host", ""), port):
            print(f"[cos serve] REFUSED Host={self.headers.get('Host')!r} -> {self.path}")
            self._send(403, "text/plain", b"Forbidden: unrecognised Host.")
            return False

        origin = self.headers.get("Origin")
        if origin:
            from urllib.parse import urlparse

            if not _host_ok(urlparse(origin).netloc, port):
                print(f"[cos serve] REFUSED Origin={origin!r} -> {self.path}")
                self._send(403, "text/plain", b"Forbidden: cross-origin.")
                return False

        if mutating:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != JSON_TYPE:
                # text/plain and form types are CORS-simple and need no
                # preflight; requiring JSON is what forces one.
                print(f"[cos serve] REFUSED Content-Type={ctype!r} -> {self.path}")
                self._send(415, "text/plain", b"Send application/json.")
                return False
            site = (self.headers.get("Sec-Fetch-Site") or "").lower()
            if site and site not in ("same-origin", "none"):
                print(f"[cos serve] REFUSED Sec-Fetch-Site={site!r} -> {self.path}")
                self._send(403, "text/plain", b"Forbidden: cross-site.")
                return False
        return True

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, "application/json", json.dumps(obj).encode())

    # -- routes -----------------------------------------------------------
    def do_GET(self):  # noqa: N802
        if not self._guarded() or not self._origin_ok(False):
            return
        if self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", rendered_page().encode())
        elif self.path == "/api/settings":
            self._json({
                "settings": settings_mod.load().public(),
                "warnings": _warnings(),
                "actual": actual_access(),
            })
        elif self.path == "/api/dashboard":
            self._json(read_snapshot())
        elif self.path == "/api/agenda":
            from . import agenda

            self._json({"items": [i.as_dict() for i in agenda.build()]})
        elif self.path.startswith("/api/ask/"):
            from . import ask

            job = ask.get(self.path.rsplit("/", 1)[-1])
            self._json(job.as_dict() if job else {"error": "unknown"},
                       200 if job else 404)
        elif self.path == "/api/ask":
            from . import ask

            self._json({"history": ask.history()})
        elif self.path.startswith("/api/page"):
            import urllib.parse

            from . import ask

            slug = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query).get("slug", [""])[0]
            self._json(ask.page(slug) if slug else {"error": "no slug"})
        elif self.path == "/api/version":
            self._json({"running": RUNNING_COMMIT, "head": _git_head()})
        elif self.path == "/api/chats":
            from . import chats

            self._json({"sessions": chats.summaries()})
        elif self.path.startswith("/api/chats/search"):
            import urllib.parse

            from . import chats

            q = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query).get("q", [""])[0]
            self._json({"sessions": chats.search(q)})
        elif self.path.startswith("/api/chats/"):
            from . import chats

            s = chats.get(self.path.rsplit("/", 1)[-1])
            self._json(s or {"error": "no such chat"}, 200 if s else 404)
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):  # noqa: N802
        if not self._guarded() or not self._origin_ok(True):
            return
        if self.path not in ("/api/settings", "/api/agenda", "/api/ask",
                             "/api/chats", "/api/draft", "/api/panel"):
            self._send(404, "text/plain", b"not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "invalid JSON"}, 400)
            return

        if self.path == "/api/agenda":
            self._agenda(payload)
            return

        if self.path == "/api/panel":
            self._panel(payload)
            return

        if self.path == "/api/draft":
            self._draft(payload)
            return

        if self.path == "/api/chats":
            from . import chats

            act = payload.get("action", "")
            sid = payload.get("id", "")
            if act == "new":
                self._json(chats.create(payload.get("title", "")))
            elif act == "rename":
                ok = chats.rename(sid, payload.get("title", ""))
                self._json({"ok": ok, "sessions": chats.summaries()})
            elif act == "delete":
                ok = chats.delete(sid)
                self._json({"ok": ok, "sessions": chats.summaries()})
            elif act == "move":
                ok = chats.move(sid, payload.get("above"), payload.get("below"))
                self._json({"ok": ok, "sessions": chats.summaries()})
            else:
                self._json({"error": f"unknown action {act!r}"}, 400)
            return

        if self.path == "/api/ask":
            from . import ask

            try:
                job = ask.start(payload.get("question", ""),
                                fresh=bool(payload.get("fresh")),
                                session=payload.get("session", ""),
                                screen=payload.get("screen", ""))
            except ValueError as e:
                self._json({"error": str(e)}, 400)
                return
            print(f"[cos serve] asked: {job.question[:70]}"
                  + (" (cached)" if job.cached_age is not None else ""))
            self._json(job.as_dict())
            return
        try:
            changed, errors = settings_mod.save(payload, actor=self.client_address[0])
        except settings_mod.SettingsError as e:
            self._json({"error": str(e)}, 500)
            return
        if changed:
            print(f"[cos serve] {self.client_address[0]} changed: {', '.join(changed)}")
        for key, why in errors.items():
            print(f"[cos serve] REJECTED {key}: {why}")
        self._json({"changed": changed, "errors": errors,
                    "settings": settings_mod.load().public()})


def _mirror_agenda(items) -> None:
    """Write the list into the vault after every change.

    Doing it here rather than only on the 15-minute refresh matters: a comment
    Wei types is content, and content that exists solely in a JSON file under
    ~/.cos is content the agent cannot see and git is not protecting.
    """
    try:
        from . import agenda
        from .config import Config

        agenda.write_page(Config.load().vault_root, items)
    except Exception as e:  # noqa: BLE001 — never fail the request over the mirror
        print(f"[cos serve] could not mirror the list into the vault: {e}")


def actual_access() -> dict:
    """What is really in force right now, read from the live config.

    Shown beside the editable fields on purpose. A settings page that only
    displays what you typed will happily show a permission you believe you set
    while something else is actually governing the system — which is exactly
    the situation with the two different write controls here.
    """
    out: dict = {}
    try:
        from .config import Config, load_env

        env = load_env()
        cfg = Config.load()
        out["vault_root"] = str(cfg.vault_root)
        out["cos_write_roots"] = [
            r.strip() for r in env.get("COS_WRITE_ROOTS", "").split(",") if r.strip()
        ]
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"

    # The agent's own write reach, which COS_WRITE_ROOTS does not govern.
    hermes_env = Path.home() / ".hermes" / ".env"
    out["hermes_safe_root"] = None
    if hermes_env.exists():
        try:
            for line in hermes_env.read_text(errors="replace").splitlines():
                if line.startswith("HERMES_WRITE_SAFE_ROOT="):
                    out["hermes_safe_root"] = line.split("=", 1)[1].strip()
                    break
        except OSError:
            pass

    try:
        from .google_auth import TOKEN_FILE, check

        if TOKEN_FILE.exists():
            info = check()
            out["google"] = {
                "address": info.get("address"),
                "calendar": info.get("calendar"),
                "scopes": info.get("scopes", []),
            }
        else:
            out["google"] = None
    except Exception as e:  # noqa: BLE001
        out["google_error"] = f"{type(e).__name__}: {e}"

    try:
        rows = subprocess.run(
            ["psql", "postgresql://localhost:5435/kiran_brain", "-tAF\x1f", "-c",
             "select name, local_path, left(last_commit,8) from sources order by name"],
            capture_output=True, text=True, timeout=10,
        )
        if rows.returncode == 0:
            out["brain_sources"] = [
                dict(zip(("name", "path", "commit"), line.split("\x1f")))
                for line in rows.stdout.strip().splitlines() if line
            ]
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def _warnings() -> list[str]:
    out = []
    for root in settings_mod.writable_by_agent():
        out.append(
            f"Settings file sits inside {root}, which the agent can write to. "
            "The send list is not trustworthy until this is moved."
        )
    reachable = agent_can_reach_us()
    if reachable:
        out.append(
            "The agent has these tools enabled: " + ", ".join(reachable) +
            ". It could reach this page and change its own permissions. "
            "Disable them in ~/.hermes/config.yaml."
        )
    return out


def serve(port: int = DEFAULT_PORT, open_browser: bool = False) -> None:
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    ts = tailscale_ip()
    print(f"cos dashboard on http://127.0.0.1:{port}")
    if ts:
        print(f"  over Tailscale:  http://{ts}:{port}")
    else:
        print("  Tailscale not detected — reachable from this machine only.")
    for w in _warnings():
        print(f"  ! {w}")
    print("  Everything else is refused, including your local network.")
    if open_browser:
        import webbrowser

        threading.Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        srv.server_close()
