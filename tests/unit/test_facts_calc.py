"""T2-06, T2-07 — the calculator and the formatter.

The property tests are the part that matters. Unit tests check the cases I
thought of; the properties check the ones I did not, and the calculator is
where a silent arithmetic error becomes a confidently wrong financial claim.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from facts.calc import (
    CalculationError,
    NegativeBase,
    Operand,
    PeriodMismatch,
    UnitMismatch,
    ZeroBase,
    cagr_pct,
    difference,
    growth_pct,
    margin_pct,
    ratio,
    total,
)
from facts.format import (
    format_exact,
    format_money,
    format_percent,
    format_ratio,
    format_value,
    format_with_exact,
)

pytestmark = pytest.mark.unit


def usd(value, end_date="2024-12-31", label="x") -> Operand:
    return Operand(value=Decimal(str(value)), unit="USD", label=label,
                   period_type="duration", end_date=end_date)


def per_share(value, end_date="2024-12-31") -> Operand:
    return Operand(value=Decimal(str(value)), unit="USD/shares", label="eps",
                   period_type="duration", end_date=end_date)


def instant(value, end_date="2024-12-31") -> Operand:
    return Operand(value=Decimal(str(value)), unit="USD", label="assets",
                   period_type="instant", end_date=end_date)


# ── growth ─────────────────────────────────────────────────────────────────

def test_growth_pct_real_values():
    """Amazon FY2023 -> FY2024: 574,785 -> 637,959."""
    result = growth_pct(usd("637959000000", "2024-12-31"),
                        usd("574785000000", "2023-12-31"))
    assert result.value == Decimal("10.99")
    assert result.unit == "percent"
    assert "574785000000" in result.formula
    assert len(result.inputs) == 2


def test_growth_pct_negative_change():
    result = growth_pct(usd("90000000000", "2024-12-31"),
                        usd("100000000000", "2023-12-31"))
    assert result.value == Decimal("-10.00")


def test_growth_from_zero_base_is_refused():
    with pytest.raises(ZeroBase):
        growth_pct(usd(100, "2024-12-31"), usd(0, "2023-12-31"))


def test_growth_from_negative_base_is_refused():
    """-100 -> +50 is not 'growth of 150%'; it is a change in kind."""
    with pytest.raises(NegativeBase) as exc:
        growth_pct(usd(50, "2024-12-31"), usd(-100, "2023-12-31"))
    assert "negative base" in str(exc.value)


def test_growth_across_units_is_refused():
    with pytest.raises(UnitMismatch):
        growth_pct(usd(100, "2024-12-31"), per_share(6.08, "2023-12-31"))


def test_growth_across_period_types_is_refused():
    with pytest.raises(PeriodMismatch):
        growth_pct(usd(100, "2024-12-31"), instant(90, "2023-12-31"))


def test_growth_within_one_period_is_refused():
    """Two facts for the same year cannot describe growth."""
    with pytest.raises(PeriodMismatch):
        growth_pct(usd(110, "2024-12-31"), usd(100, "2024-12-31"))


# ── margin, ratio, difference, sum ─────────────────────────────────────────

def test_margin_pct_real_values():
    """Apple FY2024: net income 93,736 / revenue 391,035."""
    result = margin_pct(usd("93736000000"), usd("391035000000"))
    assert result.value == Decimal("23.97")


def test_margin_requires_one_period():
    with pytest.raises(PeriodMismatch):
        margin_pct(usd(10, "2024-12-31"), usd(100, "2023-12-31"))


def test_margin_by_zero_is_refused():
    with pytest.raises(ZeroBase):
        margin_pct(usd(10), usd(0))


def test_ratio_allows_different_units():
    """Debt/equity and price/earnings mix units legitimately."""
    result = ratio(usd("125172000000"), usd("325084000000"))
    assert result.value == Decimal("0.3850")
    assert result.unit == "ratio"


def test_difference_keeps_the_unit():
    result = difference(usd("637959000000", "2024-12-31"),
                        usd("574785000000", "2023-12-31"))
    assert result.value == Decimal("63174000000")
    assert result.unit == "USD"


def test_difference_across_units_is_refused():
    with pytest.raises(UnitMismatch):
        difference(usd(100, "2024-12-31"), per_share(6, "2023-12-31"))


def test_sum_requires_one_unit():
    assert total([usd(1), usd(2), usd(3)]).value == Decimal(6)
    with pytest.raises(UnitMismatch):
        total([usd(1), per_share(2)])
    with pytest.raises(CalculationError):
        total([])


def test_cagr_real_values():
    """Amazon FY2021 -> FY2024 over three years."""
    result = cagr_pct(usd("637959000000", "2024-12-31"),
                      usd("469822000000", "2021-12-31"), years=3)
    assert result.value == Decimal("10.74")


def test_cagr_needs_a_positive_year_count():
    with pytest.raises(CalculationError):
        cagr_pct(usd(2, "2024-12-31"), usd(1, "2023-12-31"), years=0)


def test_cagr_through_a_loss_is_refused():
    with pytest.raises(NegativeBase):
        cagr_pct(usd(-10, "2024-12-31"), usd(100, "2021-12-31"), years=3)


def test_calculation_shows_its_work():
    result = growth_pct(usd(110, "2024-12-31"), usd(100, "2023-12-31"))
    assert result.formula == "(110 - 100) / 100 x 100"
    assert "growth_pct" in result.describe()
    assert "10.00%" in result.describe()


def test_rounding_is_half_away_from_zero_like_the_formatter():
    """Decimal's default is half-to-even; financial reporting is half-up."""
    # 0.125 -> 12.5% exactly, which must round to 12.50 not 12.00.
    result = margin_pct(usd("12345"), usd("98765"))
    assert result.value == Decimal("12.50")


# ── property tests (spec section 10: calculator invariants) ────────────────

AMOUNTS = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1e13"),
                      allow_nan=False, allow_infinity=False, places=2)


@given(later=AMOUNTS, earlier=AMOUNTS)
@settings(max_examples=250, deadline=None)
def test_growth_sign_follows_the_direction(later, earlier):
    result = growth_pct(usd(later, "2024-12-31"), usd(earlier, "2023-12-31"))
    if later > earlier:
        assert result.value > 0
    elif later < earlier:
        assert result.value < 0
    else:
        assert result.value == 0


@given(base=AMOUNTS, factor=st.decimals(min_value=Decimal("1.01"),
                                        max_value=Decimal("5"), places=2,
                                        allow_nan=False, allow_infinity=False))
@settings(max_examples=250, deadline=None)
def test_growth_inverse(base, factor):
    """Growing by f and then shrinking back must return to the start.

    The invariant is on the VALUES, not the percentages: g% up then the
    corresponding % down is not symmetric, and asserting that it were would be
    testing a false property.
    """
    grown = (base * factor).quantize(Decimal("0.01"))
    assume(grown > 0)
    up = growth_pct(usd(grown, "2024-12-31"), usd(base, "2023-12-31"))
    down = growth_pct(usd(base, "2024-12-31"), usd(grown, "2023-12-31"))
    assert up.value >= 0
    assert down.value <= 0
    # Recovering the later value from the earlier one and the growth rate.
    recovered = base * (1 + up.value / 100)
    assert abs(recovered - grown) <= grown * Decimal("0.0001") + Decimal("0.01")


@given(numerator=AMOUNTS, denominator=AMOUNTS)
@settings(max_examples=250, deadline=None)
def test_margin_matches_a_direct_ratio(numerator, denominator):
    m = margin_pct(usd(numerator), usd(denominator))
    r = ratio(usd(numerator), usd(denominator))
    assert abs(m.value / 100 - r.value) <= Decimal("0.001")


@given(a=AMOUNTS, b=AMOUNTS)
@settings(max_examples=200, deadline=None)
def test_difference_is_antisymmetric(a, b):
    forward = difference(usd(a, "2024-12-31"), usd(b, "2023-12-31"))
    backward = difference(usd(b, "2024-12-31"), usd(a, "2023-12-31"))
    assert forward.value == -backward.value


@given(values=st.lists(AMOUNTS, min_size=1, max_size=8))
@settings(max_examples=200, deadline=None)
def test_sum_is_order_independent(values):
    first = total([usd(v) for v in values]).value
    second = total([usd(v) for v in reversed(values)]).value
    assert first == second


# ── T2-07: formatting ──────────────────────────────────────────────────────

@pytest.mark.parametrize(("value", "unit", "expected"), [
    ("391035000000", "USD", "$391.04 billion"),
    ("39000966000", "USD", "$39.00 billion"),
    ("2628553000000", "USD", "$2.63 trillion"),
    ("1000000000", "USD", "$1.00 billion"),
    ("1000000", "USD", "$1.00 million"),
    ("1500", "USD", "$1,500"),
    ("999", "USD", "$999.00"),
    ("0", "USD", "$0.00"),
    ("-13212000000", "USD", "-$13.21 billion"),
    ("6.08", "USD/shares", "$6.08 per share"),
])
def test_money_formatting(value, unit, expected):
    assert format_money(Decimal(value), unit) == expected


@pytest.mark.parametrize(("value", "expected"), [
    # Rounding must not print a four-digit mantissa: 999,999,999 is below a
    # billion, picks the million scale, and rounds to 1000.00 — which nobody
    # writes. It is promoted instead.
    ("999999999", "$1.00 billion"),
    ("999999999999", "$1.00 trillion"),
    # ...but a value that genuinely rounds inside its own scale stays there.
    ("999499999", "$999.50 million"),
    ("999994999", "$999.99 million"),
])
def test_scale_boundaries_do_not_print_a_four_digit_mantissa(value, expected):
    assert format_money(Decimal(value), "USD") == expected


def test_percent_and_ratio_formatting():
    assert format_percent(Decimal("8.154")) == "8.15%"
    assert format_percent(Decimal("8.155")) == "8.16%"      # half away from zero
    assert format_percent(Decimal("-3.5")) == "-3.50%"
    assert format_ratio(Decimal("1.5")) == "1.50x"


def test_exact_formatting_keeps_every_digit():
    assert format_exact(Decimal("391035000000"), "USD") == "$391,035,000,000"
    assert format_exact(Decimal("39000966000"), "USD") == "$39,000,966,000"
    assert format_exact(Decimal("-565000000"), "USD") == "-$565,000,000"
    assert format_exact(Decimal("15115823000"), "shares") == "15,115,823,000"


def test_format_with_exact_shows_both_only_when_they_differ():
    both = format_with_exact(Decimal("391035000000"), "USD")
    assert both == "$391.04 billion ($391,035,000,000)"
    # Nothing abbreviated: a parenthetical would read as a second number.
    assert format_with_exact(Decimal("6.08"), "USD/shares") == "$6.08 per share"
    assert format_with_exact(Decimal("1500"), "USD") == "$1,500"


def test_format_value_dispatches_on_the_recorded_unit():
    assert format_value(Decimal("8.15"), "percent") == "8.15%"
    assert format_value(Decimal("1.5"), "ratio") == "1.50x"
    assert format_value(Decimal("100"), "shares") == "100"
    assert format_value(Decimal("100"), "USD") == "$100.00"


def test_formatting_never_changes_the_magnitude():
    """K2 regression, stated as an invariant.

    v1's prompt told the model to "assume millions", which is a 1000x error on
    a filer reporting in thousands. Nothing in formatting may apply a scale of
    its own: the value arrives already scaled by the extractor.
    """
    netflix_revenue = Decimal("39000966000")
    rendered = format_money(netflix_revenue, "USD")
    assert rendered.startswith("$39.00 billion")
    assert "trillion" not in rendered
    assert "million" not in rendered
