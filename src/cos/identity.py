"""Telling humans apart from machines.

The single most damaging thing this tool could do is tell Wei that "Google has
gone quiet" because the last message from google.com was a Drive share
notification. Robot and role addresses must never be treated as counterparties.

This is deliberately all deterministic rules — no model. Every decision here is
inspectable and reproducible, which is the whole point of the Stage 1 design.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Local parts that are never a person. Matched on the whole local part or as a
# dot/dash/underscore-delimited token, so `sales` matches but `salesforce-dave`
# does not match on `sales`.
ROLE_LOCALPARTS = frozenset(
    """
    noreply no-reply donotreply do-not-reply dontreply notifications notification
    notify alerts alert alerting mailer mailerdaemon mailer-daemon postmaster
    bounce bounces bounced daemon automated automailer autoreply auto-reply
    info information contact contactus hello hi hey enquiries enquiry inquiry
    support helpdesk help service services customerservice care
    admin administrator webmaster hostmaster root sysadmin system
    billing invoices invoice invoicing receipts receipt payments payment accounts
    accounting accountspayable ar ap finance
    news newsletter newsletters press media marketing campaigns campaign promo
    promotions offers deals updates update digest weekly monthly
    sales presales leads
    careers jobs recruiting recruitment hr people talent
    security abuse privacy legal compliance dpo
    team teams group groups all everyone staff
    bot robot mail email reply replies feedback survey surveys
    events event webinar webinars rsvp invites invitations
    desk office reception general main
    """.split()
)

# A role word plus this many other tokens still reads as a role address:
# `partner-support-desk`, `sales-team-emea`. Real people are `first.last` or
# `flast`, whose tokens are names, not role words.
_MAX_ROLE_TOKENS = 3

# Registrable domains that exist only to send bulk mail.
BULK_SENDER_DOMAINS = frozenset(
    """
    sendgrid.net mailgun.org mailgun.net mailchimp.com mandrillapp.com
    amazonses.com sparkpostmail.com createsend.com cmail19.com cmail20.com
    hubspotemail.net marketo.com pardot.com exacttarget.com constantcontact.com
    klaviyomail.com customeriomail.com sendinblue.com mailjet.com postmarkapp.com
    substack.com beehiiv.com luma-mail.com
    """.split()
)

# Substrings that mark a *subdomain* as a sending relay. Checked only on
# subdomain labels, so `mail.acme.com` and `customermail.microsoft.com` are
# caught while `gmail.com` and `email.co.uk` (a registrable domain) are not.
BULK_SUBDOMAIN_TOKENS = (
    "mail", "email", "reply", "replies", "notification", "notifications",
    "notify", "alert", "alerts", "news", "newsletter", "marketing", "campaign",
    "send", "smtp", "bounce", "bounces", "info", "updates", "connect",
)

# A local part that is mostly a machine token: long hex runs, uuids, or the
# `something+hash@` reply-address pattern used by Asana, Figma, Dropbox Paper.
_HEXISH = re.compile(r"[0-9a-f]{16,}", re.IGNORECASE)
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_LONG_DIGITS = re.compile(r"\d{10,}")
_TOKEN_SPLIT = re.compile(r"[._\-+]")


@dataclass(frozen=True)
class AddressVerdict:
    address: str
    kind: str  # person | role | robot
    reason: str

    @property
    def is_person(self) -> bool:
        return self.kind == "person"


def split_address(address: str) -> tuple[str, str]:
    local, _, domain = address.lower().strip().rpartition("@")
    return local, domain


def classify_address(
    address: str, names_per_address_on_domain: float | None = None
) -> AddressVerdict:
    """Classify one address. `names_per_address_on_domain` is the structural
    signal: a domain carrying far more distinct display names than addresses is
    a shared/robot sender (a ticketing system, a notification relay)."""
    addr = (address or "").lower().strip()
    if not addr or "@" not in addr:
        return AddressVerdict(addr, "robot", "malformed address")

    local, domain = split_address(addr)

    if _UUID.search(local):
        return AddressVerdict(addr, "robot", "uuid in local part")
    if _HEXISH.search(local):
        return AddressVerdict(addr, "robot", "long hex token in local part")
    if _LONG_DIGITS.search(local):
        return AddressVerdict(addr, "robot", "long digit run in local part")

    if domain in BULK_SENDER_DOMAINS:
        return AddressVerdict(addr, "robot", f"bulk-sender domain ({domain})")

    labels = domain.split(".")
    if len(labels) >= 3:
        # Everything left of the registrable domain. `.co.uk`-style suffixes
        # would make this slightly conservative, which is the safe direction.
        for label in labels[:-2]:
            if any(token in label for token in BULK_SUBDOMAIN_TOKENS):
                return AddressVerdict(
                    addr, "robot", f"sending subdomain ({label}.…)"
                )

    # `+` suffixes are usually routing tokens on an otherwise-role address.
    base_local = local.split("+", 1)[0]
    tokens = {t for t in _TOKEN_SPLIT.split(base_local) if t}
    if base_local in ROLE_LOCALPARTS or (
        tokens & ROLE_LOCALPARTS and len(tokens) <= _MAX_ROLE_TOKENS
    ):
        return AddressVerdict(addr, "role", f"role local part ({base_local})")

    if names_per_address_on_domain is not None and names_per_address_on_domain > 3.0:
        return AddressVerdict(
            addr,
            "robot",
            f"shared-sender domain ({names_per_address_on_domain:.1f} names/address)",
        )

    return AddressVerdict(addr, "person", "no robot or role signal")


def domain_of(address: str) -> str:
    return split_address(address)[1]


# Free mail providers: a person there is a person, but the *domain* is not an
# organization and must never be reported as a counterparty org.
FREEMAIL_DOMAINS = frozenset(
    """
    gmail.com googlemail.com yahoo.com yahoo.co.uk ymail.com hotmail.com
    outlook.com live.com msn.com icloud.com me.com mac.com aol.com
    proton.me protonmail.com pm.me gmx.com gmx.de web.de mail.com
    qq.com 163.com 126.com foxmail.com sina.com yandex.ru zoho.com
    fastmail.com hey.com pacbell.net sbcglobal.net comcast.net verizon.net
    """.split()
)


def is_freemail(address_or_domain: str) -> bool:
    d = address_or_domain.lower()
    if "@" in d:
        d = domain_of(d)
    return d in FREEMAIL_DOMAINS
