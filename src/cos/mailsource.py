"""Where mail comes from.

Kiran was built against one machine's local Gmail mirror: mbsync writes a
maildir, a separate project loads it into Kuzu, and `export_brain` reads
structure from the graph and bodies off disk. That works, and for a 165k-message
archive it is genuinely faster and cheaper than any API. It is also the single
worst thing about installing Kiran on someone else's computer — an IMAP app
password, mbsync, a launchd job, a graph server and a nightly rebuild, all
before the first question can be answered.

So "where mail comes from" becomes an interface with two implementations:

    LocalMirrorSource   maildir + Kuzu — what Wei runs, unchanged
    GmailApiSource      the Gmail API — real-time, and a consent screen instead
                        of an afternoon of setup

Nothing above this line cares which is in use.

── The rule that protects the backfill ──────────────────────────────────────

Extracting insights from 25,782 threads took about 60 hours. That work is keyed
by `content_hash`: gbrain skips any page whose content it has already processed,
no matter where the content came from. Page identity is
`email/{date-of-last-message}-{slugified-subject}`.

So a second source costs nothing **as long as it produces byte-identical pages**
for threads that have not changed. Two consequences, and both are requirements
rather than preferences:

1. **Fetch `format=raw` and parse with `mailtext.py`.** The API then returns the
   same RFC-822 bytes mbsync wrote to disk, and the same parser turns them into
   the same text. Anything else — using Gmail's parsed payload, or its snippet —
   changes the body, changes the hash, and re-runs the extraction.

2. **Threading will move some pages, and that is unavoidable.** Gmail's
   `threadId` is authoritative; the mirror reconstructed threading and at one
   point had 8,868 of 11,526 messages with no thread id at all. The API will
   thread some conversations *better*, which regroups them, which changes their
   subject/date and therefore their slug. Those pages get re-extracted. That is
   a one-time cost paid in background time, and the data is more correct after.

Measure before switching: generate pages from the new source into a scratch
directory and diff them against `~/brain/email`. The identical fraction is the
fraction of the backfill preserved.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .mailtext import MessageText, read_message_body


@runtime_checkable
class MailSource(Protocol):
    """Structure and bodies. Two methods, because that is all Kiran needs."""

    def load_threads(
        self, principals: tuple[str, ...], since: datetime
    ) -> dict[str, "object"]:
        """Threads with at least one message after `since`, keyed by thread id.

        Returns `export_brain.Thread` objects. The annotation is loose to keep
        this module import-light — `export_brain` imports *this*, not the
        reverse.
        """
        ...

    def body(self, message: "object") -> MessageText:
        """The novel text of one message: quotes, signatures and boilerplate
        already stripped. Both implementations route through `mailtext.py` so
        the output — and therefore the page hash — is identical."""
        ...

    def describe(self) -> str:
        """One line for `cos check`, naming the source and its freshness."""
        ...


class LocalMirrorSource:
    """maildir + Kuzu. The original path, behind the interface unchanged.

    Freshness is bounded by how often mbsync runs and the graph is rebuilt —
    about four hours on Wei's machine. That lag is the main thing the API
    source removes.
    """

    def __init__(self, client, maildir_root) -> None:
        self._client = client
        self._maildir_root = maildir_root

    def load_threads(self, principals: tuple[str, ...], since: datetime):
        # Imported here rather than at module scope: export_brain imports this
        # module, and a top-level import would be circular.
        from .export_brain import load_threads

        return load_threads(self._client, principals, since)

    def body(self, message) -> MessageText:
        # `maildir_path` is relative to the Gmail project root, not to the
        # maildir directory — reading it as absolute produced zero pages on the
        # first dry run and looked like an empty mailbox rather than a bad path.
        return read_message_body(self._maildir_root / message.maildir_path)

    def describe(self) -> str:
        return f"local mirror ({self._maildir_root})"
