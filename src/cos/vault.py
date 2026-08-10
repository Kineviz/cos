"""Read the vault. Strictly read-only.

Stage 1 reads exactly two files — Pipeline.md and Prospects.md — because those
are the ones Wei already maintains as part of the Monday ritual. Nothing here
writes, moves, or creates anything in the vault.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_WIKILINK = re.compile(r"\[\[#?([^\]|]+)(?:\|[^\]]+)?\]\]")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _clean_cell(cell: str) -> str:
    text = _WIKILINK.sub(r"\1", cell)
    text = _BOLD.sub(r"\1", text)
    return text.strip()


def parse_markdown_table(lines: list[str]) -> list[dict[str, str]]:
    """Parse the first pipe-table found in `lines`."""
    rows: list[list[str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells if c):
            continue  # separator row
        rows.append(cells)
    if len(rows) < 2:
        return []
    header = [_clean_cell(h).lower() for h in rows[0]]
    out = []
    for row in rows[1:]:
        if len(row) != len(header):
            continue
        out.append({header[i]: _clean_cell(row[i]) for i in range(len(header))})
    return out


@dataclass
class Deal:
    name: str
    source_file: str
    stage: str = ""
    owner: str = ""
    next_step: str = ""
    paper: str = ""
    domains: list[str] = field(default_factory=list)

    @property
    def has_paper(self) -> bool:
        return "✅" in self.paper or "signed" in self.paper.lower()


def _load_table_after_heading(path: Path, heading: str) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith(heading.lower()):
            return parse_markdown_table(lines[i + 1 :])
    return []


def load_deals(vault_root: Path) -> list[Deal]:
    """Named deals from Pipeline.md plus top-of-funnel from Prospects.md."""
    tm = vault_root / "05_workspace" / "Task_management"
    deals: list[Deal] = []

    for filename, name_col in (("Pipeline.md", "deal"), ("Prospects.md", "prospect")):
        path = tm / filename
        for row in _load_table_after_heading(path, "## At a glance"):
            name = row.get(name_col, "").strip()
            if not name:
                continue
            deals.append(
                Deal(
                    name=name,
                    source_file=filename,
                    stage=row.get("stage", ""),
                    owner=row.get("owner", ""),
                    next_step=row.get("next step", ""),
                    paper=row.get("paper?", ""),
                )
            )
    return deals


def load_deal_domains(path: Path) -> dict[str, list[str]]:
    """Deal name -> email domains. Hand-maintained; absence is not an error,
    it just means the deal cannot be matched to traffic yet."""
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    mapping = data.get("deals", {}) or {}
    return {
        str(k): [str(d).lower().strip() for d in (v or [])]
        for k, v in mapping.items()
    }


def load_internal_domains(path: Path) -> frozenset[str]:
    """Domains belonging to the company itself — never a counterparty."""
    if not path.is_file():
        return frozenset()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return frozenset(
        str(d).lower().strip() for d in (data.get("internal_domains") or [])
    )


def attach_domains(deals: list[Deal], mapping: dict[str, list[str]]) -> None:
    lowered = {k.lower(): v for k, v in mapping.items()}
    for deal in deals:
        deal.domains = lowered.get(deal.name.lower(), [])
