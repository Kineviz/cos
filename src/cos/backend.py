"""Which mail backend the CLI is talking to.

`cos owed`, `quiet`, `brief`, `dashboard` and `export-brain` were written
against a Kuzu client. Rather than rewrite every call site, the backend is a
small object with the same shape, and the three functions that did the graph
queries dispatch on it. That keeps the mirror path working — the machine with
twelve years already loaded should not be forced to re-source it — while a new
install talks only to Google.

Selection is by capability, not configuration: if a Google token exists, use it.
Nobody setting this up on a new machine should have to learn that a setting
called `mail_source` decides whether the product works.
"""

from __future__ import annotations

from datetime import datetime

from .google_auth import TOKEN_FILE


class GmailBackend:
    """Marker + entry points. The real work lives in gmail_ledger / gmail_source."""

    is_gmail = True

    def __init__(self, principals: tuple[str, ...]) -> None:
        self.principals = principals
        self._source = None

    # Context-manager shape so `with open_backend(cfg) as client:` reads the
    # same for both backends.
    def __enter__(self) -> "GmailBackend":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def source(self):
        if self._source is None:
            from .gmail_source import GmailApiSource

            self._source = GmailApiSource(self.principals)
        return self._source

    def ledger(self) -> dict:
        from .gmail_ledger import load_or_build

        ledger, _ = load_or_build(self.principals)
        return ledger

    def freshness(self) -> datetime | None:
        from .gmail_ledger import corpus_freshness

        return corpus_freshness()

    def health(self) -> bool:
        """A cheap authenticated call. Proves the grant, reads no content."""
        try:
            from googleapiclient.discovery import build

            from .google_auth import load_credentials

            svc = build("gmail", "v1",
                        credentials=load_credentials(interactive=False),
                        cache_discovery=False)
            svc.users().getProfile(userId="me").execute()
            return True
        except Exception:
            return False

    def describe(self) -> str:
        return "Gmail API (live)"


class MsGraphBackend:
    """Microsoft 365 / Exchange Online, through Microsoft Graph.

    Same shape as GmailBackend; the modules underneath are ms_auth,
    msgraph and ms_ledger.
    """

    is_gmail = False

    def __init__(self, principals: tuple[str, ...]) -> None:
        self.principals = principals

    def __enter__(self) -> "MsGraphBackend":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def ledger(self) -> dict:
        from .ms_ledger import load_or_build

        ledger, _ = load_or_build(self.principals)
        return ledger

    def freshness(self) -> datetime | None:
        return None

    def health(self) -> bool:
        """A cheap authenticated call. Proves the grant, reads no content."""
        try:
            from .msgraph import profile

            return bool(profile().get("address"))
        except Exception:  # noqa: BLE001
            return False

    def describe(self) -> str:
        return "Microsoft Graph (live)"


def gmail_available() -> bool:
    """A completed consent flow is the whole prerequisite."""
    return TOKEN_FILE.exists()


def ms_available() -> bool:
    from .ms_auth import TOKEN_FILE as MS_TOKEN

    return MS_TOKEN.exists()


def open_backend(cfg):
    """Gmail when it is connected; Microsoft when IT is connected and Gmail
    is not; otherwise the local mirror.

    Gmail deliberately wins when both tokens exist. This backend was built
    to be tested on a second machine — Wei: "it does not mess up Kiran" —
    and the strongest form of that promise is that on a machine where Kiran
    already runs on Gmail, a stray Microsoft sign-in changes nothing.
    """
    if gmail_available():
        return GmailBackend(tuple(cfg.principal_addresses))
    if ms_available():
        return MsGraphBackend(tuple(cfg.principal_addresses))
    from .kuzu import KuzuClient

    return KuzuClient(cfg.kuzu_url)
