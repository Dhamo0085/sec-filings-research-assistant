"""The three answer-path ablation switches actually ablate (P4-04, P4-06).

A switch that is read but changes nothing is worse than no switch: the variant
runs, the table fills in, and the conclusion is "this guard makes no
difference". That happened once already — see
`test_turning_off_as_of_also_drops_a_date_written_in_the_question`.
"""

from __future__ import annotations

import pytest

from answering.outcome import Status
from query import Deps

pytestmark = pytest.mark.unit


class StubRoute:
    pass


def _deps(**flags) -> Deps:
    deps = Deps()
    for name, value in flags.items():
        setattr(deps, name, value)
    return deps


def test_a_flag_left_unset_follows_the_configuration():
    """`None` means "the configured value", so the shipped path is unchanged."""
    from config import settings

    deps = Deps()
    for name in ("enable_facts", "enable_asof", "enable_abstain_gate"):
        assert getattr(deps, name) is None
        assert deps.flag(name) == getattr(settings, name)


def test_an_explicit_flag_wins_over_the_configuration():
    assert _deps(enable_facts=False).flag("enable_facts") is False
    assert _deps(enable_facts=True).flag("enable_facts") is True


def test_flags_are_per_deps_not_global():
    """Two variants must be runnable in one process over the same gold set."""
    off, on = _deps(enable_facts=False), _deps(enable_facts=True)
    assert off.flag("enable_facts") is False
    assert on.flag("enable_facts") is True


@pytest.mark.live
def test_turning_off_as_of_also_drops_a_date_written_in_the_question():
    """The bug this file exists for.

    The gold questions carry the date in the sentence ("As of 2024-10-31, what
    was Apple's revenue..."), and the router parses it from there. The first V2
    run dropped only the `as_of` PARAMETER, so every point-in-time item behaved
    exactly as in V3 and the run would have been reported as "the guard makes
    no difference". The date now has to leave the Route as well.

    Marked `live` because it needs the real catalog and facts store; the
    structural half of the same property is covered by the offline tests above.
    """
    from query import ask

    question = "As of 2024-10-31, what was Apple's revenue for fiscal 2024?"

    scoped = ask(question, as_of="2024-10-31", deps=_deps())
    assert scoped.status is Status.ABSTAINED
    assert scoped.as_of == "2024-10-31"

    unscoped = ask(question, as_of="2024-10-31",
                   deps=_deps(enable_asof=False, enable_abstain_gate=False))
    assert unscoped.as_of is None, "the date survived in the Route"
    assert unscoped.status is Status.ANSWERED, (
        "with point-in-time scope off the system should answer from a filing "
        "that was not yet public — that is what V2 exists to demonstrate"
    )
