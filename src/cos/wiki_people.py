"""Map email addresses to wiki People pages.

This is the join the design review called the highest-leverage work in the
project: the graph knows people by address, the wiki knows them by name, and
`Pipeline.md` knows them by deal. Nothing reconciled the three.

Two rules govern everything here, both from the review:

  * **Address-first, never name-first.** A page claims an address, or it does
    not match. Name similarity only ever *generates a candidate*; it never
    concludes. Name-first matching with the disambiguating parenthetical
    stripped is precisely the bug that collapsed the two Claudia Brenners.
  * **Splitting is cheap, merging is unrecoverable.** An unmatched address
    produces a missing link, which is visible and harmless. A wrong match
    silently attributes one person's email to another, and nothing surfaces it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .identity import classify_address

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---", re.DOTALL)
_EMAILS_LINE = re.compile(r"^emails:\s*\[([^\]]*)\]", re.MULTILINE)
_ADDR = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

# Same slug rule gbrain applies to filenames, so `Morgan Reyes.md` in the
# vault and `martin-dubbey` in the brain agree.
def slug_of(name: str) -> str:
    return _SLUG_STRIP.sub("-", name.lower()).strip("-")


@dataclass
class PersonPage:
    slug: str            # gbrain basename, e.g. "martin-dubbey"
    title: str           # "Morgan Reyes"
    path: Path
    addresses: list[str] = field(default_factory=list)
    disambiguator: str | None = None  # the "(Kineviz 2015)" part, if any

    @property
    def is_disambiguated(self) -> bool:
        return self.disambiguator is not None


_PAREN = re.compile(r"^(?P<base>.+?)\s*\((?P<disamb>[^)]+)\)\s*$")


def load_people_pages(vault_root: Path) -> list[PersonPage]:
    """Every 10_wiki/People page, with the addresses it claims."""
    people_dir = vault_root / "10_wiki" / "People"
    pages: list[PersonPage] = []
    if not people_dir.is_dir():
        return pages

    for path in sorted(people_dir.rglob("*.md")):
        stem = path.stem
        m = _PAREN.match(stem)
        # The parenthetical is an entity-resolution decision someone made by
        # hand ("(Kineviz 2015)" means "not the other Claudia Brenner").
        # Keep it in the slug — stripping it is what fused distinct people.
        disamb = m.group("disamb") if m else None
        title = stem

        addresses: list[str] = []
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            head = ""
        fm = _FRONTMATTER.search(head)
        if fm:
            em = _EMAILS_LINE.search(fm.group(1))
            if em:
                addresses = [
                    a.lower() for a in _ADDR.findall(em.group(1))
                    # A robot address on a person page is a data defect, not an
                    # identity. One Google Drive notifier was claimed by 35
                    # different People pages before the upstream fix.
                    if classify_address(a).is_person
                ]

        pages.append(
            PersonPage(
                slug=slug_of(stem),
                title=title,
                path=path,
                addresses=addresses,
                disambiguator=disamb,
            )
        )
    return pages


@dataclass
class AddressIndex:
    by_address: dict[str, PersonPage] = field(default_factory=dict)
    conflicts: dict[str, list[str]] = field(default_factory=dict)

    def resolve(self, address: str) -> PersonPage | None:
        return self.by_address.get(address.lower())

    @property
    def size(self) -> int:
        return len(self.by_address)


def _normalize_name(name: str) -> str:
    """Fold a display name for comparison. `Reyes, Morgan` -> `morgan reyes`."""
    n = re.sub(r"\s+", " ", (name or "").strip())
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n)          # trailing parenthetical
    n = re.sub(r"\s*<[^>]*>\s*", "", n)              # embedded address
    if "," in n and n.count(",") == 1:               # Lastname, Firstname
        last, first = (p.strip() for p in n.split(","))
        if last and first:
            n = f"{first} {last}"
    n = re.sub(r"[^a-z ]", "", n.lower()).strip()
    return re.sub(r"\s+", " ", n)


def build_name_index(pages: list[PersonPage]) -> dict[str, PersonPage]:
    """Normalized full name -> page, but ONLY where the name is unambiguous.

    A name shared by two pages resolves to neither, and a page carrying a
    disambiguating parenthetical is excluded entirely — the parenthetical
    exists precisely because the bare name is not sufficient to identify that
    person. This is what keeps the two Claudia Brenners apart.
    """
    counts: dict[str, list[PersonPage]] = {}
    for page in pages:
        if page.is_disambiguated:
            continue
        key = _normalize_name(page.title)
        if len(key.split()) < 2:  # single-token names are never distinctive
            continue
        counts.setdefault(key, []).append(page)
    return {k: v[0] for k, v in counts.items() if len(v) == 1}


def resolve_by_name(
    display_name: str | None, name_index: dict[str, PersonPage]
) -> PersonPage | None:
    """Candidate from a display name. Exact normalized match only — no fuzz.

    Name evidence is weaker than an address claim, so this is only ever
    consulted after `AddressIndex.resolve` has already failed.
    """
    if not display_name:
        return None
    return name_index.get(_normalize_name(display_name))


def build_address_index(pages: list[PersonPage]) -> AddressIndex:
    """Address -> page. An address claimed by two pages resolves to neither.

    That is deliberate: a contested address means the wiki disagrees with
    itself about who someone is, and guessing would silently merge them.
    Contested addresses are recorded so they can be reviewed.
    """
    claims: dict[str, list[PersonPage]] = {}
    for page in pages:
        for addr in page.addresses:
            claims.setdefault(addr, []).append(page)

    index = AddressIndex()
    for addr, owners in claims.items():
        if len(owners) == 1:
            index.by_address[addr] = owners[0]
        else:
            index.conflicts[addr] = sorted(p.title for p in owners)
    return index
