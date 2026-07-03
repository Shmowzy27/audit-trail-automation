"""Chronological ordering for standardized owner-statement filenames.

Standardized names embed the statement date as ``MM-YY`` (e.g.
``Owner Packet - Keyrenter - 04-23 - 1426.39.pdf``). ``MM-YY`` is human-readable
but sorts *month-first* as plain text — ``04-23`` sorts before ``05-21`` — so a
naive string sort groups every April together instead of ordering by date.

``pdf_sort_key`` parses the real ``(year, month)`` so listings read
``01-21, 02-21, ... 12-21, 01-22, ...`` within each folder. See BUG-016.
"""

from __future__ import annotations

import re
from pathlib import Path

# The date/amount tail of a standardized name; anchored to ``.pdf`` so the
# amount group does not swallow the trailing dot before the extension.
_PDF_DATE_RE = re.compile(r"- (\d{2})-(\d{2}) - ([-\d.]+)\.pdf$", re.IGNORECASE)


def pdf_sort_key(path: str | Path) -> tuple:
    """Sort key giving chronological order within each folder.

    Order: folder, then standardized-before-unknown, then year, month, amount,
    name. Names that do not match the standard format sort last (by name) so a
    not-yet-renamed file never jumps into the middle of the dated list.
    """
    p = Path(path)
    folder = p.parent.name.lower()
    m = _PDF_DATE_RE.search(p.name)
    if m:
        month, year = int(m.group(1)), 2000 + int(m.group(2))
        try:
            amount = float(m.group(3))
        except ValueError:
            amount = 0.0
        return (folder, 0, year, month, amount, p.name.lower())
    return (folder, 1, 0, 0, 0.0, p.name.lower())
