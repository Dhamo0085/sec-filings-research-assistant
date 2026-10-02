"""Formatting money, percents and ratios for answer text (P2-08).

    format_money(Decimal("391035000000"))       # "$391.04 billion"
    format_money(Decimal("39000966000"))        # "$39.00 billion"
    format_percent(Decimal("8.15"))             # "8.15%"

Two rules shape all of it.

**Never let formatting change the magnitude.** Netflix reports in thousands and
v1's prompt told the model to "assume millions" (K2), so the single most likely
way for this project to be confidently wrong is a factor of 1,000. The scaling
here is driven only by the value itself, which is already fully scaled by the
extractor; no unit hint from the question, the filing or the prompt reaches it.

**Say what was rounded.** ``$391.04 billion`` is the readable form of
391,035,000,000, but a reader comparing it against the filing sees 391,035.
:func:`format_money` therefore has an ``exact`` companion, and the answer
templates in P3-04 print both.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

TRILLION = Decimal("1e12")
BILLION = Decimal("1e9")
MILLION = Decimal("1e6")
THOUSAND = Decimal("1e3")

# Boundaries are >= so 1,000,000,000 reads as "$1.00 billion" rather than
# "$1000.00 million". A value is scaled only when it reaches the next unit,
# never when it is merely close.
_SCALES = (
    (TRILLION, "trillion"),
    (BILLION, "billion"),
    (MILLION, "million"),
)


def _quantize(value: Decimal, places: int) -> Decimal:
    exponent = Decimal(1).scaleb(-places)
    # ROUND_HALF_UP, not Python's default ROUND_HALF_EVEN: financial reporting
    # rounds halves away from zero, and a figure that disagrees with the filing
    # in the last printed digit invites exactly the wrong kind of doubt.
    return value.quantize(exponent, rounding=ROUND_HALF_UP)


def format_exact(value: Decimal, unit: Optional[str] = None) -> str:
    """The full figure with thousands separators: ``$391,035,000,000``."""
    negative = value < 0
    body = f"{abs(value):,f}"
    if "." in body:
        body = body.rstrip("0").rstrip(".")
    prefix = "$" if _is_monetary(unit) else ""
    return f"{'-' if negative else ''}{prefix}{body}"


def _is_currency(unit: Optional[str]) -> bool:
    """A plain currency amount — not a per-share or per-unit rate."""
    return bool(unit) and unit.upper().startswith("USD") and "/" not in unit


def _is_monetary(unit: Optional[str]) -> bool:
    """Currency OR a currency rate, i.e. anything that takes a ``$``."""
    return bool(unit) and unit.upper().startswith("USD")


def format_money(value: Decimal, unit: Optional[str] = "USD",
                 places: int = 2) -> str:
    """Readable money: ``$391.04 billion``, ``$6.08`` for a per-share figure.

    A per-share unit (``USD/shares``) is never abbreviated — "$6.08 per share"
    is the number, and "$6.08" scaled to anything would be nonsense.
    """
    if unit and "/" in unit:
        return f"${_quantize(value, places)} per share"
    if not _is_currency(unit):
        return format_exact(value, unit)

    negative = value < 0
    magnitude = abs(value)
    for index, (threshold, name) in enumerate(_SCALES):
        if magnitude < threshold:
            continue
        scaled = _quantize(magnitude / threshold, places)
        # Rounding can push a value past the next boundary: 999,999,999 is
        # below a billion, so it picks the million scale, and then rounds to
        # 1000.00 — which is not how anyone writes a billion. Promote to the
        # LARGER unit (_SCALES is descending, so that is index - 1) and
        # re-round. 999,499,999 is untouched and still reads $999.50 million,
        # because only the printed mantissa decides, not a fudged threshold.
        if scaled >= THOUSAND and index > 0:
            threshold, name = _SCALES[index - 1]
            scaled = _quantize(magnitude / threshold, places)
        return f"{'-' if negative else ''}${scaled} {name}"
    if magnitude >= THOUSAND:
        return f"{'-' if negative else ''}${magnitude:,.0f}"
    return f"{'-' if negative else ''}${_quantize(magnitude, places)}"


def format_percent(value: Decimal, places: int = 2) -> str:
    return f"{_quantize(value, places)}%"


def format_ratio(value: Decimal, places: int = 2) -> str:
    return f"{_quantize(value, places)}x"


def format_value(value: Decimal, unit: Optional[str]) -> str:
    """Dispatch on the unit recorded with the fact, never on a guess."""
    if unit == "percent":
        return format_percent(value)
    if unit == "ratio":
        return format_ratio(value)
    if unit in ("shares", "pure", None, ""):
        return format_exact(value, unit)
    return format_money(value, unit)


def format_with_exact(value: Decimal, unit: Optional[str]) -> str:
    """Readable form plus the exact figure, for answer text.

    ``$391.04 billion ($391,035,000,000)`` — the first is for the reader, the
    second is what they will see if they open the filing.
    """
    readable = format_value(value, unit)
    exact = format_exact(value, unit)
    if readable == exact or exact in readable:
        # Nothing was abbreviated, so a parenthetical would just repeat
        # itself — "$6.08 per share (6.08)" reads like two different numbers.
        return readable
    return f"{readable} ({exact})"
