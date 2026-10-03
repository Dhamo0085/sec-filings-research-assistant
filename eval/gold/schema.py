"""Schema and validator for ``eval/gold/gold_v1.jsonl`` (P4-01, spec 6.6, T4-02).

Why a validator and not just a dataclass
----------------------------------------
Every headline number in Phase 4 is ``n/N`` over this file. A gold item that is
silently wrong does not fail anything — it quietly moves a percentage, and the
report states it with a confidence interval. So the properties the evaluation
depends on are checked explicitly, and each check says what it is protecting:

* **Provenance on every item.** ``source`` and ``verified_by`` are required, and
  ``verified_by`` is ordered ``owner`` > ``companyfacts`` > ``auto`` (D4).
  Headline metrics are published over the first two only (spec section 11), so
  an item without provenance cannot be counted and must not be silently
  treated as if it had the weakest kind.
* **Unique ids.** Results are joined to items by id. A duplicate id makes one
  item's result overwrite another's, which changes a score without changing a
  count — the hardest kind of error to notice.
* **`as_of` consistency with the catalog.** A look-ahead item is only a test if
  the filing it must not use was genuinely not public on that date. If the
  catalog says otherwise, the item proves nothing, and a scorer reporting zero
  look-ahead violations over such items would be reporting on nothing.
* **Abstain items name a reason.** Abstention precision and recall are reported
  *per reason* (spec section 11). An abstain item with no expected reason can
  only be scored as "refused something", which is the weaker claim this project
  exists to avoid.

The validator returns problems rather than raising, so one run lists everything
wrong with a generated file instead of stopping at the first item.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# ── the enumerations spec 6.6 fixes ──────────────────────────────────────────

#: Question categories, with the per-category target from spec section 11.
CATEGORY_TARGETS: Dict[str, int] = {
    "numeric": 25,
    "computed": 12,
    "compare_trend": 10,
    "narrative": 15,
    "as_of": 8,
    "abstain": 10,
}

#: ``expected.type`` values and the extra keys each one requires.
EXPECTED_REQUIRED_KEYS: Dict[str, Tuple[str, ...]] = {
    "numeric":  ("ticker", "metric", "period_end", "value", "unit"),
    "computed": ("ticker", "metric", "value", "unit"),
    "multi":    ("values",),
    "abstain":  ("abstain_reason",),
    "text":     ("ticker", "expected_sections"),
}

#: Strongest first. Headline metrics use ``owner`` and ``companyfacts`` items;
#: ``auto`` items are reported separately (spec section 11, D4).
VERIFICATION_STRENGTH: Tuple[str, ...] = ("owner", "companyfacts", "auto")

#: The reasons an abstain item may expect, from answering/outcome.py's enum.
#: Imported lazily in :func:`abstain_reasons` so this module stays importable
#: without the application package on the path (the CI mini-eval does that).
_FALLBACK_ABSTAIN_REASONS: Tuple[str, ...] = (
    "company_not_found", "no_filing_for_company", "period_not_covered",
    "period_not_filed_as_of", "future_period", "metric_not_supported",
    "metric_not_found_in_filing", "ambiguous_concept", "unsupported_period_type",
    "out_of_scope", "insufficient_evidence",
)

REQUIRED_TOP_LEVEL: Tuple[str, ...] = (
    "id", "category", "question", "as_of", "expected", "source", "verified_by",
)


def abstain_reasons() -> Tuple[str, ...]:
    """The live AbstainReason enum, or the pinned copy if it cannot be imported.

    The pinned copy is not a second source of truth: ``test_gold_schema.py``
    asserts the two are identical, so a reason added to the enum without being
    added here fails the suite rather than quietly making gold items invalid.
    """
    try:
        from answering.outcome import AbstainReason
    except Exception:
        return _FALLBACK_ABSTAIN_REASONS
    return tuple(r.value for r in AbstainReason)


# ── problems ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Problem:
    """One thing wrong with one item (or with the set as a whole)."""

    item_id: str
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.item_id or '<set>'}: {self.field}: {self.message}"


@dataclass
class CatalogDates:
    """The filing dates an ``as_of`` item is checked against.

    A plain mapping rather than a CatalogStore handle so the validator stays
    pure and testable offline: ``build_gold.py`` passes the real catalog in,
    and a unit test passes three rows.
    """

    #: (ticker, fiscal_label) -> ISO filing date of the original 10-K.
    filing_dates: Mapping[Tuple[str, int], str] = field(default_factory=dict)

    @classmethod
    def from_catalog(cls, store) -> "CatalogDates":
        dates: Dict[Tuple[str, int], str] = {}
        for filing in store.all_filings():
            if filing.is_amendment:
                continue
            key = (filing.ticker, filing.fiscal_label)
            # Keep the earliest: the date the information first became public
            # is what a look-ahead test is about.
            if key not in dates or filing.filing_date < dates[key]:
                dates[key] = filing.filing_date
        return cls(dates)


# ── the checks ────────────────────────────────────────────────────────────────

def _check_structure(item: Mapping, index: int) -> List[Problem]:
    item_id = str(item.get("id") or f"<item {index}>")
    problems: List[Problem] = []

    for key in REQUIRED_TOP_LEVEL:
        if key not in item:
            problems.append(Problem(item_id, key, "missing"))

    category = item.get("category")
    if category not in CATEGORY_TARGETS:
        problems.append(Problem(
            item_id, "category",
            f"{category!r} is not one of {sorted(CATEGORY_TARGETS)}",
        ))

    if not str(item.get("question") or "").strip():
        problems.append(Problem(item_id, "question", "empty"))

    verified_by = item.get("verified_by")
    if verified_by not in VERIFICATION_STRENGTH:
        problems.append(Problem(
            item_id, "verified_by",
            f"{verified_by!r} is not one of {list(VERIFICATION_STRENGTH)}",
        ))

    source = item.get("source")
    if not isinstance(source, Mapping) or not source:
        problems.append(Problem(item_id, "source", "missing or empty"))
    elif verified_by == "companyfacts" and not source.get("oracle"):
        # An item claiming oracle verification has to say which oracle, or the
        # claim cannot be re-checked.
        problems.append(Problem(
            item_id, "source.oracle",
            "verified_by is 'companyfacts' but no oracle is named",
        ))

    return problems


def _check_expected(item: Mapping) -> List[Problem]:
    item_id = str(item.get("id") or "<unknown>")
    expected = item.get("expected")
    if not isinstance(expected, Mapping):
        return [Problem(item_id, "expected", "missing or not an object")]

    kind = expected.get("type")
    if kind not in EXPECTED_REQUIRED_KEYS:
        return [Problem(
            item_id, "expected.type",
            f"{kind!r} is not one of {sorted(EXPECTED_REQUIRED_KEYS)}",
        )]

    problems = [
        Problem(item_id, f"expected.{key}", "missing")
        for key in EXPECTED_REQUIRED_KEYS[kind]
        if expected.get(key) in (None, "", [])
    ]

    if kind in ("numeric", "computed") and expected.get("value") not in (None, ""):
        try:
            Decimal(str(expected["value"]))
        except (InvalidOperation, ValueError):
            problems.append(Problem(
                item_id, "expected.value",
                f"{expected['value']!r} is not a number",
            ))
        # Values are carried as strings on purpose (spec 6.6): a JSON float
        # cannot hold 391035000000 and a cent at the same time, and a gold
        # value that lost precision on the way in cannot be scored exactly.
        if not isinstance(expected["value"], str):
            problems.append(Problem(
                item_id, "expected.value",
                "must be a string, so no precision is lost in JSON",
            ))

    if kind == "numeric" and "tolerance_rel" not in expected:
        problems.append(Problem(item_id, "expected.tolerance_rel", "missing"))
    if kind == "computed" and not ({"tolerance_abs", "tolerance_rel"} & set(expected)):
        problems.append(Problem(
            item_id, "expected.tolerance_abs",
            "a computed item needs tolerance_abs or tolerance_rel",
        ))

    if kind == "multi":
        values = expected.get("values")
        if not isinstance(values, Sequence) or isinstance(values, str) or len(values) < 2:
            problems.append(Problem(
                item_id, "expected.values",
                "a multi item needs at least two values",
            ))

    if kind == "abstain":
        reason = expected.get("abstain_reason")
        if reason and reason not in abstain_reasons():
            problems.append(Problem(
                item_id, "expected.abstain_reason",
                f"{reason!r} is not a reason answering/outcome.py defines",
            ))

    if kind == "text":
        sections = expected.get("expected_sections")
        if not isinstance(sections, Sequence) or isinstance(sections, str):
            problems.append(Problem(
                item_id, "expected.expected_sections", "must be a list",
            ))

    return problems


def _check_category_matches_expected(item: Mapping) -> List[Problem]:
    """The category and the expected type have to agree about what is asked.

    They are separate fields because the category is how results are grouped in
    the report and the type is how an answer is scored. They can disagree, and
    if they do the item is counted in one table and scored by another's rules.
    """
    item_id = str(item.get("id") or "<unknown>")
    expected = item.get("expected")
    if not isinstance(expected, Mapping):
        return []

    allowed = {
        "numeric": {"numeric"},
        "computed": {"computed"},
        "compare_trend": {"multi", "computed"},
        "narrative": {"text"},
        "as_of": {"numeric", "abstain"},
        "abstain": {"abstain"},
    }.get(str(item.get("category")))

    if allowed and expected.get("type") not in allowed:
        return [Problem(
            item_id, "expected.type",
            f"category {item.get('category')!r} expects one of "
            f"{sorted(allowed)}, got {expected.get('type')!r}",
        )]
    return []


def _check_as_of(item: Mapping, catalog: Optional[CatalogDates]) -> List[Problem]:
    """An ``as_of`` item must be a real point-in-time test, per the catalog.

    Two ways it can fail to be one, and both make the item worthless rather
    than merely wrong:

    * the item expects an abstention because the filing was not yet public, but
      the catalog says it WAS public on that date — the pipeline would be right
      to answer, and the item would score a correct answer as a failure;
    * the item expects an answer on an ``as_of`` date before the filing existed
      — the pipeline would be right to refuse.
    """
    item_id = str(item.get("id") or "<unknown>")
    as_of = item.get("as_of")
    if not as_of:
        return []

    expected = item.get("expected")
    if not isinstance(expected, Mapping):
        return []

    problems: List[Problem] = []
    ticker = expected.get("ticker")
    fiscal_label = expected.get("fiscal_label")
    if catalog is None or ticker is None or fiscal_label is None:
        if catalog is not None and item.get("category") == "as_of":
            problems.append(Problem(
                item_id, "expected.fiscal_label",
                "an as_of item needs ticker and fiscal_label so the date can "
                "be checked against the catalog",
            ))
        return problems

    filed = catalog.filing_dates.get((str(ticker), int(fiscal_label)))
    if filed is None:
        problems.append(Problem(
            item_id, "as_of",
            f"the catalog has no original 10-K for {ticker} FY{fiscal_label}, "
            "so this item cannot be a point-in-time test",
        ))
        return problems

    was_public = filed <= str(as_of)
    expects_refusal = expected.get("type") == "abstain"

    if expects_refusal and was_public:
        problems.append(Problem(
            item_id, "as_of",
            f"expects a refusal, but {ticker} FY{fiscal_label} was filed "
            f"{filed}, on or before as_of={as_of}",
        ))
    if not expects_refusal and not was_public:
        problems.append(Problem(
            item_id, "as_of",
            f"expects an answer, but {ticker} FY{fiscal_label} was not filed "
            f"until {filed}, after as_of={as_of}",
        ))
    return problems


def validate(
    items: Iterable[Mapping],
    catalog: Optional[CatalogDates] = None,
) -> List[Problem]:
    """Every problem with ``items``, in file order, set-level problems last."""
    items = list(items)
    problems: List[Problem] = []

    for index, item in enumerate(items):
        problems.extend(_check_structure(item, index))
        problems.extend(_check_expected(item))
        problems.extend(_check_category_matches_expected(item))
        problems.extend(_check_as_of(item, catalog))

    seen: Dict[str, int] = {}
    for item in items:
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        seen[item_id] = seen.get(item_id, 0) + 1
    for item_id, count in sorted(seen.items()):
        if count > 1:
            problems.append(Problem(
                item_id, "id",
                f"appears {count} times; results are joined to items by id",
            ))

    return problems


# ── reading and writing ───────────────────────────────────────────────────────

def read_jsonl(path: Path) -> List[Dict]:
    """Parse a JSONL file, reporting the line number of a bad line."""
    items: List[Dict] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: {exc}") from exc
    return items


def write_jsonl(path: Path, items: Sequence[Mapping]) -> None:
    """One compact JSON object per line, keys in a stable order.

    Stable key order matters: the file is committed, so a regenerated set
    should produce a diff only where a value actually changed.
    """
    order = {key: i for i, key in enumerate(
        ("id", "category", "question", "as_of", "expected", "source",
         "verified_by", "notes")
    )}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            ordered = dict(sorted(
                item.items(), key=lambda kv: (order.get(kv[0], 99), kv[0])
            ))
            handle.write(json.dumps(ordered, ensure_ascii=False) + "\n")


def category_counts(items: Iterable[Mapping]) -> Dict[str, int]:
    counts = dict.fromkeys(CATEGORY_TARGETS, 0)
    for item in items:
        category = str(item.get("category") or "")
        if category in counts:
            counts[category] += 1
    return counts
