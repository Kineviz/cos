"""Client for the existing Kuzu HTTP server (Gmail/scripts/serve.py).

Read-only by construction: `query()` refuses anything that is not a plain
read. The Gmail graph is rebuilt in full from the maildir on a schedule and is
not ours to write to.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

# Anything that could mutate the graph or the schema. Checked against the
# statement with string literals removed, so a subject line containing the
# word "delete" cannot trip it.
_FORBIDDEN = re.compile(
    r"\b(create|merge|set|delete|detach|drop|alter|copy|install|load|attach|"
    r"call|export|import)\b",
    re.IGNORECASE,
)
_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'")


class KuzuError(RuntimeError):
    pass


class ReadOnlyViolation(KuzuError):
    pass


class KuzuClient:
    def __init__(self, url: str, timeout: float = 120.0) -> None:
        self.url = url
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "KuzuClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _assert_read_only(cypher: str) -> None:
        stripped = _STRING_LITERAL.sub("''", cypher)
        match = _FORBIDDEN.search(stripped)
        if match:
            raise ReadOnlyViolation(
                f"refusing to run a statement containing {match.group(0)!r}: "
                "the Gmail graph is a read-only source system"
            )

    def query(self, cypher: str) -> list[dict[str, Any]]:
        """Run a read query and return rows as dicts."""
        self._assert_read_only(cypher)
        try:
            resp = self._client.post(self.url, json={"query": cypher})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise KuzuError(
                f"cannot reach the Kuzu server at {self.url}: {exc}\n"
                "Start it with:  cd ~/projects/Gmail && python scripts/serve.py --port 7001"
            ) from exc

        payload = resp.json()
        if payload.get("status") != 0:
            raise KuzuError(f"query failed: {payload.get('message')}")

        table = payload.get("data", {})
        rows = table.get("data") or []
        if not rows:
            return []
        header, *body = rows
        return [dict(zip(header, r)) for r in body]

    def health(self) -> bool:
        base = self.url.split("/kuzudb/")[0]
        try:
            return self._client.get(f"{base}/health", timeout=5.0).status_code == 200
        except httpx.HTTPError:
            return False
