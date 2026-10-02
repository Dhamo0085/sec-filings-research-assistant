"""The metric registry: aliases in, candidate concepts out (P2-05, spec 6.3).

    from facts.concepts import load_registry
    registry = load_registry()
    registry.resolve_alias("total net sales")        # -> "revenue"
    metric = registry.metric("revenue")
    metric.candidates_for("BLK")                     # sector list
    metric.override_for("BLK")                       # the tie-breaker + evidence

The one thing to keep in mind reading this module: **the candidate order is not
the policy.** Spec 6.4 rule 3 is override first, then exactly-one-value, then
``ambiguous``. The ordered lists here are the search space, nothing more.
``facts/resolve.py`` owns the policy; this module only answers "which concepts
could possibly mean revenue for this filer".

Two validations are enforced at load time rather than discovered later:

* an override without ``evidence`` is rejected. An override is this project
  overruling the general rule for one filer, and the only thing separating that
  from a guess is a written reason naming the filing it came from;
* an alias claimed by two metrics is rejected. Silent precedence between
  "revenue" and "total revenue" would make routing depend on dict ordering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from facts.errors import FactsError

_REGISTRY_FILE = Path(__file__).resolve().parent / "concepts.yaml"

DEFAULT_SECTOR = "default"
VALID_STATEMENTS = ("income", "balance", "cash_flow")
VALID_PERIOD_TYPES = ("duration", "instant")
VALID_UNIT_KINDS = ("money", "per_share", "shares", "ratio")


class RegistryError(FactsError):
    """The registry file is malformed. Fatal at import: a half-valid registry
    would mean some metrics resolve and others silently do not."""


@dataclass(frozen=True)
class Override:
    ticker: str
    concept: str
    evidence: str
    fiscal_from: Optional[int] = None
    fiscal_to: Optional[int] = None

    def applies_to(self, fiscal_label: Optional[int]) -> bool:
        """Overrides may be scoped to a fiscal range (spec 6.3 "optionally per
        fiscal range"), because a filer can change how it tags."""
        if fiscal_label is None:
            return True
        if self.fiscal_from is not None and fiscal_label < self.fiscal_from:
            return False
        return not (self.fiscal_to is not None and fiscal_label > self.fiscal_to)


@dataclass(frozen=True)
class Metric:
    name: str
    label: str
    statement: str
    period_type: str
    unit_kind: str
    aliases: Tuple[str, ...]
    # Per sector, an ordered tuple of TIERS, each a tuple of concepts. Rule 3
    # applies within a tier; a later tier is consulted only when every concept
    # in the earlier ones is absent. A flat YAML list loads as a single tier,
    # which is the no-preference case.
    candidate_tiers: Dict[str, Tuple[Tuple[str, ...], ...]]
    overrides: Dict[str, Tuple[Override, ...]] = field(default_factory=dict)
    # Why the tiers are ordered the way they are. Required whenever there is
    # more than one tier: a preference without a stated reason is the "silent
    # pick" that spec 6.4 rule 3 exists to prevent.
    preference: str = ""

    def tiers_for(self, sector: str = DEFAULT_SECTOR) -> Tuple[Tuple[str, ...], ...]:
        """The tiered search space for this metric and sector.

        Falls back to ``default`` for an unknown sector, which is what should
        happen for a filer we have never seen (P3-06 on-demand ingestion): a
        broader search plus the ambiguity rule abstains rather than guessing.
        """
        return self.candidate_tiers.get(sector) or self.candidate_tiers[DEFAULT_SECTOR]

    def candidates_for(self, sector: str = DEFAULT_SECTOR) -> Tuple[str, ...]:
        """Every concept in every tier, flattened — for reporting and citations."""
        return tuple(c for tier in self.tiers_for(sector) for c in tier)

    def override_for(self, ticker: str,
                     fiscal_label: Optional[int] = None) -> Optional[Override]:
        for override in self.overrides.get(ticker.upper(), ()):
            if override.applies_to(fiscal_label):
                return override
        return None

    def all_candidates(self) -> Tuple[str, ...]:
        """Every concept any sector might use, for coverage reporting."""
        seen: List[str] = []
        for tiers in self.candidate_tiers.values():
            for tier in tiers:
                for concept in tier:
                    if concept not in seen:
                        seen.append(concept)
        return tuple(seen)


@dataclass(frozen=True)
class Registry:
    version: int
    metrics: Dict[str, Metric]
    sector_by_ticker: Dict[str, str]
    alias_to_metric: Dict[str, str]

    def metric(self, name: str) -> Metric:
        try:
            return self.metrics[name]
        except KeyError:
            raise KeyError(
                f"unknown metric {name!r}; known: {sorted(self.metrics)}"
            ) from None

    def sector_for(self, ticker: str) -> str:
        return self.sector_by_ticker.get(ticker.upper(), DEFAULT_SECTOR)

    def resolve_alias(self, text: str) -> Optional[str]:
        """Metric name for a phrase, or None.

        Matching is exact on a normalised form (lowercased, punctuation and
        extra spaces removed) rather than fuzzy. A near-miss must reach
        ``metric_not_supported`` and abstain, not land on whichever metric
        happened to be closest: answering a question about gross margin with
        revenue is worse than not answering it.
        """
        return self.alias_to_metric.get(_normalise_alias(text))

    def candidates_for(self, metric: str, ticker: str) -> Tuple[str, ...]:
        return self.metric(metric).candidates_for(self.sector_for(ticker))

    def tiers_for(self, metric: str, ticker: str) -> Tuple[Tuple[str, ...], ...]:
        return self.metric(metric).tiers_for(self.sector_for(ticker))

    def metric_names(self) -> List[str]:
        return sorted(self.metrics)


def _normalise_alias(text: str) -> str:
    cleaned = "".join(
        ch if (ch.isalnum() or ch.isspace()) else " " for ch in (text or "").lower()
    )
    return " ".join(cleaned.split())


def _normalise_tiers(metric_name: str, sector: str,
                     raw) -> Tuple[Tuple[str, ...], ...]:
    """Accept a flat list (one tier) or a list of lists (several).

    The flat form stays valid because most metrics have no preference to
    express, and forcing ``[[x], [y]]`` everywhere would make the tiered ones
    harder to spot rather than easier.
    """
    if not raw:
        raise RegistryError(f"{metric_name}.candidates.{sector} is empty")
    if all(isinstance(entry, str) for entry in raw):
        return (tuple(raw),)
    tiers: List[Tuple[str, ...]] = []
    for entry in raw:
        if isinstance(entry, str):
            raise RegistryError(
                f"{metric_name}.candidates.{sector} mixes a bare concept with "
                f"tier lists; make every entry a list or none of them")
        if not entry:
            raise RegistryError(
                f"{metric_name}.candidates.{sector} has an empty tier")
        tiers.append(tuple(entry))
    return tuple(tiers)


def _parse_overrides(metric_name: str, raw: Dict) -> Dict[str, Tuple[Override, ...]]:
    out: Dict[str, List[Override]] = {}
    for ticker, spec in (raw or {}).items():
        entries = spec if isinstance(spec, list) else [spec]
        for entry in entries:
            if not isinstance(entry, dict):
                raise RegistryError(
                    f"{metric_name}.overrides.{ticker} must be a mapping")
            concept = entry.get("concept")
            evidence = (entry.get("evidence") or "").strip()
            if not concept:
                raise RegistryError(
                    f"{metric_name}.overrides.{ticker} has no `concept`")
            if not evidence:
                # Deliberately fatal. An override is this project overruling
                # spec 6.4's general rule for one filer; without a written
                # reason naming the filing, nobody later can tell it from a
                # guess, and nobody can check it.
                raise RegistryError(
                    f"{metric_name}.overrides.{ticker} has no `evidence`. "
                    f"An override without provenance is a guess; cite the "
                    f"accession and the statement line it came from."
                )
            out.setdefault(ticker.upper(), []).append(Override(
                ticker=ticker.upper(),
                concept=concept,
                evidence=evidence,
                fiscal_from=entry.get("fiscal_from"),
                fiscal_to=entry.get("fiscal_to"),
            ))
    return {k: tuple(v) for k, v in out.items()}


def load_registry(path: Optional[Path] = None) -> Registry:
    path = Path(path or _REGISTRY_FILE)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise RegistryError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path} is not valid YAML: {exc}") from exc

    metrics_raw = raw.get("metrics") or {}
    if not metrics_raw:
        raise RegistryError(f"{path} defines no metrics")

    sector_by_ticker: Dict[str, str] = {}
    for sector, tickers in (raw.get("sectors") or {}).items():
        for ticker in tickers or []:
            sector_by_ticker[str(ticker).upper()] = sector

    metrics: Dict[str, Metric] = {}
    alias_to_metric: Dict[str, str] = {}
    for name, spec in metrics_raw.items():
        candidates_raw = spec.get("candidates") or {}
        if DEFAULT_SECTOR not in candidates_raw:
            raise RegistryError(
                f"metric {name!r} has no `default` candidate list; an unseen "
                f"filer would have nothing to search")
        statement = spec.get("statement")
        if statement not in VALID_STATEMENTS:
            raise RegistryError(f"metric {name!r}: statement {statement!r} not "
                                f"in {VALID_STATEMENTS}")
        period_type = spec.get("period_type")
        if period_type not in VALID_PERIOD_TYPES:
            raise RegistryError(f"metric {name!r}: period_type {period_type!r} "
                                f"not in {VALID_PERIOD_TYPES}")
        unit_kind = spec.get("unit_kind", "money")
        if unit_kind not in VALID_UNIT_KINDS:
            raise RegistryError(f"metric {name!r}: unit_kind {unit_kind!r} not "
                                f"in {VALID_UNIT_KINDS}")

        aliases = [_normalise_alias(a) for a in (spec.get("aliases") or [])]
        aliases.append(_normalise_alias(name))
        for alias in aliases:
            if not alias:
                continue
            owner = alias_to_metric.get(alias)
            if owner is not None and owner != name:
                # Silent precedence here would make routing depend on the
                # order keys happen to appear in the YAML.
                raise RegistryError(
                    f"alias {alias!r} is claimed by both {owner!r} and "
                    f"{name!r}; an alias must name exactly one metric")
            alias_to_metric[alias] = name

        tiers = {k: _normalise_tiers(name, k, v)
                 for k, v in candidates_raw.items()}
        preference = (spec.get("preference") or "").strip()
        if any(len(v) > 1 for v in tiers.values()) and not preference:
            raise RegistryError(
                f"metric {name!r} has tiered candidates but no `preference` "
                f"explaining the order. A preference without a stated reason is "
                f"the silent pick that spec 6.4 rule 3 forbids.")

        metrics[name] = Metric(
            name=name,
            label=spec.get("label") or name,
            statement=statement,
            period_type=period_type,
            unit_kind=unit_kind,
            aliases=tuple(dict.fromkeys(aliases)),
            candidate_tiers=tiers,
            overrides=_parse_overrides(name, spec.get("overrides")),
            preference=preference,
        )

    unknown_sectors = {
        sector for metric in metrics.values() for sector in metric.candidate_tiers
    } - set(sector_by_ticker.values()) - {DEFAULT_SECTOR}
    if unknown_sectors:
        raise RegistryError(
            f"candidate list(s) for sector(s) {sorted(unknown_sectors)} that no "
            f"ticker belongs to; either add tickers or remove the list")

    return Registry(
        version=int(raw.get("version", 1)),
        metrics=metrics,
        sector_by_ticker=sector_by_ticker,
        alias_to_metric=alias_to_metric,
    )
