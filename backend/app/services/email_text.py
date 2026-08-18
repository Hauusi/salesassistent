"""Text-prep helpers for what gets sent to Claude, separate from what gets
stored/displayed.

`EmailMessage.raw_content` (see app.services.gmail_client._extract_plain_text)
is the plain-text body Gmail returns, which for a reply almost always
includes the entire quoted thread below the new text (">" lines, "Am ...
schrieb ...:" / "On ... wrote:" headers, Outlook "Von/Gesendet/An/Betreff"
blocks). That quoted history is useful to show a human in the inbox UI, but
for classification/draft-generation prompts it is mostly redundant tokens -
the classifier and drafter only need the new message, not a mail's own
copy of everything that came before it.
"""
from __future__ import annotations

import re

_QUOTE_MARKERS: list[re.Pattern[str]] = [
    # Outlook-style separator: "---- Ursprüngliche Nachricht ----" / "-----Original Message-----"
    re.compile(r"^-{2,}\s*(Ursprüngliche Nachricht|Original Message)\s*-{2,}.*$", re.MULTILINE | re.IGNORECASE),
    # Gmail-style reply header: "Am 12.03.2024 um 10:15 schrieb Max Mustermann <max@example.com>:"
    re.compile(r"^Am\s.{1,80}\sschrieb\s.{1,160}:\s*$", re.MULTILINE),
    # English equivalent: "On Tue, 12 Mar 2024 at 10:15, Max Mustermann <max@example.com> wrote:"
    re.compile(r"^On\s.{1,80}\swrote:\s*$", re.MULTILINE),
    # Outlook forwarded/replied header block, German or English.
    re.compile(r"^(Von|From):\s*.+\n^(Gesendet|Sent):\s*.+", re.MULTILINE),
    # Any line starting with the conventional '>' quote prefix.
    re.compile(r"^\s*>", re.MULTILINE),
]


def strip_quoted_reply(text: str) -> str:
    """Cuts off the quoted/forwarded trail from an email body, keeping only
    the new top-level message the sender actually wrote. Falls back to the
    full text unchanged if no quote marker is found (e.g. a first mail in a
    thread, or a body that doesn't follow these conventions) - this is a
    best-effort trim, not a guarantee, so it never raises or drops content
    it isn't confident about."""
    if not text:
        return text

    earliest = len(text)
    for pattern in _QUOTE_MARKERS:
        match = pattern.search(text)
        if match and match.start() < earliest:
            earliest = match.start()

    return text[:earliest].rstrip()
