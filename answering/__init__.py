"""Answer construction: outcomes, abstention, and the answer templates.

``outcome.py`` holds the contract every path returns (spec 6.5). The facts and
text paths and the abstention gate all produce an ``Outcome`` and nothing else,
so the guarantees G1-G4 are checked in one place rather than per call site.
"""
