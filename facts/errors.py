"""Typed errors for the facts engine (D7: fail loudly, never silently guess).

The whole point of the facts path is that a number is either read correctly or
not produced. Every failure mode below is something that must stop a build
rather than become a plausible-looking wrong answer in an answer template.
"""

from __future__ import annotations


class FactsError(Exception):
    """Base class for every facts-engine failure."""


class ExtractionError(FactsError):
    """A filing could not be parsed into facts."""


class UnknownTransformError(ExtractionError):
    """An ``ix:nonFraction/@format`` this extractor does not implement.

    Deliberately fatal (spec P2-02: "handle all, **fail loudly on unknown**").
    An unimplemented transform is not a missing feature, it is a *wrong number*:
    ``ixt:num-comma-decimal`` read as dot-decimal turns 1.234.567 into 1.234,
    and ``ixt:fixed-zero`` read literally turns an em-dash into nothing at all.
    Skipping the fact would be just as bad, because the resolver would then pick
    a different candidate and report it as the answer.
    """

    def __init__(self, transform: str, concept: str = "", source_doc: str = "") -> None:
        self.transform = transform
        self.concept = concept
        self.source_doc = source_doc
        where = f" on {concept}" if concept else ""
        doc = f" in {source_doc}" if source_doc else ""
        super().__init__(
            f"unimplemented inline-XBRL transform {transform!r}{where}{doc}. "
            f"Add it to facts.extract.NUMERIC_TRANSFORMS (with a test) rather "
            f"than letting the fact through unconverted."
        )


class ValueParseError(ExtractionError):
    """A fact's text did not parse under its declared transform."""


class ContextError(ExtractionError):
    """A context is missing, malformed, or referenced by a fact that has none."""
