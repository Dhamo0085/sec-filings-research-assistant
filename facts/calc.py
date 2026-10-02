"""Deterministic arithmetic on resolved facts (P2-08). Decimal, never float.

    from facts.calc import growth_pct, margin_pct
    result = growth_pct(later, earlier)
    result.value        # Decimal("8.15")
    result.formula      # "(637959000000 - 574785000000) / 574785000000 x 100"
    result.inputs       # the facts it used

Every function returns a :class:`Calculation` carrying the value, a formula
string and the input facts, or raises a typed error. Nothing returns a bare
number, because an answer template has to be able to show its work: "revenue
grew 11.0%" is unverifiable, "(637,959 - 574,785) / 574,785" is not.

The guards are the substance of this module, not boilerplate:

* **Unit mismatch.** Diluted EPS is ``USD/shares``; revenue is ``USD``. A
  growth rate between them is meaningless, so it raises. The registry marks
  ``eps_diluted`` as ``unit_kind: per_share`` for exactly this.
* **Period mismatch.** A growth rate from two facts covering the same year, or
  an instant compared with a duration, is not a growth rate.
* **Zero or negative base.** Growth from a base of 0 is undefined, not
  infinite. Growth from a *negative* base is the trap: a company whose
  operating income went from -100 to +50 has not grown 150%, and reporting
  that number would be worse than abstaining.
* **Decimal context.** Division is done at 28 significant digits and rounded
  only at the end, so (a/b)*100 and a*100/b agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import (
    ROUND_HALF_UP,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    localcontext,
)
from typing import List, Optional, Sequence, Tuple

from facts.errors import FactsError

# Percentages are reported to two decimals; ratios to four. Both are rounded
# once, at the end, from a 28-digit intermediate.
PERCENT_PLACES = Decimal("0.01")
RATIO_PLACES = Decimal("0.0001")
_WORKING_PRECISION = 28
# ROUND_HALF_UP, stated explicitly and matching facts/format.py. Decimal's
# default is ROUND_HALF_EVEN, so leaving it implicit meant the calculator and
# the formatter could disagree in the last printed digit — the kind of
# inconsistency that makes a reader doubt both numbers.
_ROUNDING = ROUND_HALF_UP


class CalculationError(FactsError):
    """A calculation that must not produce a number."""


class UnitMismatch(CalculationError):
    pass


class PeriodMismatch(CalculationError):
    pass


class ZeroBase(CalculationError):
    pass


class NegativeBase(CalculationError):
    pass


@dataclass(frozen=True)
class Operand:
    """One input to a calculation, as much of a fact as the arithmetic needs."""

    value: Decimal
    unit: Optional[str] = None
    label: str = ""
    period_type: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    ticker: Optional[str] = None
    accession: Optional[str] = None

    @classmethod
    def from_resolution(cls, resolution) -> "Operand":
        return cls(
            value=resolution.value,
            unit=resolution.unit,
            label=f"{resolution.ticker} {resolution.metric_label} "
                  f"FY{resolution.fiscal_label}",
            period_type=resolution.period_type,
            start_date=resolution.start_date,
            end_date=resolution.end_date,
            ticker=resolution.ticker,
            accession=resolution.accession,
        )


@dataclass(frozen=True)
class Calculation:
    operation: str
    value: Decimal
    unit: str
    formula: str
    inputs: Tuple[Operand, ...] = field(default_factory=tuple)

    def describe(self) -> str:
        return f"{self.operation}: {self.formula} = {self.value}{self._suffix()}"

    def _suffix(self) -> str:
        return "%" if self.unit == "percent" else ""


def _as_operand(value) -> Operand:
    if isinstance(value, Operand):
        return value
    if hasattr(value, "value") and hasattr(value, "metric_label"):
        return Operand.from_resolution(value)
    raise TypeError(f"expected an Operand or a Resolution, got {type(value).__name__}")


def _require_same_unit(a: Operand, b: Operand, operation: str) -> str:
    if a.unit != b.unit:
        raise UnitMismatch(
            f"{operation} needs matching units, got {a.unit!r} ({a.label}) and "
            f"{b.unit!r} ({b.label}). Comparing a per-share figure with a "
            f"currency total produces a number with no meaning.")
    return a.unit or ""


def _require_comparable_periods(a: Operand, b: Operand, operation: str) -> None:
    if a.period_type and b.period_type and a.period_type != b.period_type:
        raise PeriodMismatch(
            f"{operation} cannot compare a {a.period_type} with a "
            f"{b.period_type} ({a.label} vs {b.label})")


def _require_distinct_periods(a: Operand, b: Operand, operation: str) -> None:
    if a.end_date and b.end_date and a.end_date == b.end_date:
        raise PeriodMismatch(
            f"{operation} needs two different periods; both operands end "
            f"{a.end_date} ({a.label}, {b.label})")


def _check_base(base: Decimal, operation: str, label: str) -> None:
    if base == 0:
        raise ZeroBase(
            f"{operation} from a base of zero is undefined, not infinite "
            f"({label})")
    if base < 0:
        # The trap worth spelling out: -100 -> +50 is not "growth of 150%".
        # Percentage change off a negative base has no agreed sign convention,
        # so any number printed here would be defensible and useless.
        raise NegativeBase(
            f"{operation} from a negative base ({base}) has no meaningful "
            f"percentage: a swing from a loss to a profit is a change in kind, "
            f"not a percentage change ({label})")


def growth_pct(later, earlier) -> Calculation:
    """Percentage change from ``earlier`` to ``later``."""
    a, b = _as_operand(later), _as_operand(earlier)
    _require_same_unit(a, b, "growth_pct")
    _require_comparable_periods(a, b, "growth_pct")
    _require_distinct_periods(a, b, "growth_pct")
    _check_base(b.value, "growth_pct", b.label)
    with localcontext() as ctx:
        ctx.prec = _WORKING_PRECISION
        value = (a.value - b.value) / b.value * 100
    return Calculation(
        operation="growth_pct",
        value=value.quantize(PERCENT_PLACES, rounding=_ROUNDING),
        unit="percent",
        formula=f"({a.value} - {b.value}) / {b.value} x 100",
        inputs=(a, b),
    )


def cagr_pct(later, earlier, years: int) -> Calculation:
    """Compound annual growth rate over ``years`` periods."""
    a, b = _as_operand(later), _as_operand(earlier)
    _require_same_unit(a, b, "cagr_pct")
    _require_comparable_periods(a, b, "cagr_pct")
    _require_distinct_periods(a, b, "cagr_pct")
    _check_base(b.value, "cagr_pct", b.label)
    if years <= 0:
        raise CalculationError(f"cagr_pct needs at least one year, got {years}")
    if a.value < 0:
        raise NegativeBase(
            f"cagr_pct cannot take the {years}-th root of a negative ratio "
            f"({a.value} / {b.value}); a CAGR through a loss year is undefined")
    with localcontext() as ctx:
        ctx.prec = _WORKING_PRECISION
        try:
            value = ((a.value / b.value) ** (Decimal(1) / Decimal(years)) - 1) * 100
        except (InvalidOperation, DivisionByZero) as exc:
            raise CalculationError(
                f"cagr_pct failed for {a.label} / {b.label}: {exc}") from exc
    return Calculation(
        operation="cagr_pct",
        value=value.quantize(PERCENT_PLACES, rounding=_ROUNDING),
        unit="percent",
        formula=f"(({a.value} / {b.value})^(1/{years}) - 1) x 100",
        inputs=(a, b),
    )


def margin_pct(numerator, denominator) -> Calculation:
    """``numerator`` as a percentage of ``denominator`` (same period)."""
    a, b = _as_operand(numerator), _as_operand(denominator)
    _require_same_unit(a, b, "margin_pct")
    _require_comparable_periods(a, b, "margin_pct")
    if a.end_date and b.end_date and a.end_date != b.end_date:
        raise PeriodMismatch(
            f"margin_pct needs one period; got {a.end_date} ({a.label}) and "
            f"{b.end_date} ({b.label})")
    if b.value == 0:
        raise ZeroBase(f"margin_pct cannot divide by zero ({b.label})")
    with localcontext() as ctx:
        ctx.prec = _WORKING_PRECISION
        value = a.value / b.value * 100
    return Calculation(
        operation="margin_pct",
        value=value.quantize(PERCENT_PLACES, rounding=_ROUNDING),
        unit="percent",
        formula=f"{a.value} / {b.value} x 100",
        inputs=(a, b),
    )


def ratio(numerator, denominator) -> Calculation:
    """A plain ratio. Units need not match (debt/equity, price/earnings)."""
    a, b = _as_operand(numerator), _as_operand(denominator)
    _require_comparable_periods(a, b, "ratio")
    if b.value == 0:
        raise ZeroBase(f"ratio cannot divide by zero ({b.label})")
    with localcontext() as ctx:
        ctx.prec = _WORKING_PRECISION
        value = a.value / b.value
    return Calculation(
        operation="ratio",
        value=value.quantize(RATIO_PLACES, rounding=_ROUNDING),
        unit="ratio",
        formula=f"{a.value} / {b.value}",
        inputs=(a, b),
    )


def difference(later, earlier) -> Calculation:
    """``later - earlier``, in the shared unit."""
    a, b = _as_operand(later), _as_operand(earlier)
    unit = _require_same_unit(a, b, "difference")
    _require_comparable_periods(a, b, "difference")
    return Calculation(
        operation="difference",
        value=a.value - b.value,
        unit=unit,
        formula=f"{a.value} - {b.value}",
        inputs=(a, b),
    )


def total(operands: Sequence) -> Calculation:
    """Sum of operands sharing one unit."""
    items: List[Operand] = [_as_operand(o) for o in operands]
    if not items:
        raise CalculationError("sum needs at least one operand")
    unit = items[0].unit
    for item in items[1:]:
        if item.unit != unit:
            raise UnitMismatch(
                f"sum needs matching units, got {unit!r} and {item.unit!r} "
                f"({item.label})")
    value = sum((i.value for i in items), Decimal(0))
    return Calculation(
        operation="sum",
        value=value,
        unit=unit or "",
        formula=" + ".join(str(i.value) for i in items),
        inputs=tuple(items),
    )
