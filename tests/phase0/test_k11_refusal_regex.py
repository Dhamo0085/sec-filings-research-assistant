"""
Phase 0 / Step 1.2 — K11: query._is_refusal regex quality.

Measurement only.  12+ strings: real refusals (should match) and legitimate
answers that happen to contain refusal-adjacent vocabulary (should NOT match).
"""
from __future__ import annotations

import csv
import json

from conftest import install_heavy_stubs, REPO_ROOT

install_heavy_stubs()

import query as Q  # noqa: E402

OUT_JSON = REPO_ROOT / "reports" / "phase0" / "k11_refusal_regex.json"
OUT_CSV = REPO_ROOT / "reports" / "phase0" / "k11_refusal_regex.csv"

# (text, should_be_detected_as_refusal, label)
CASES = [
    # ---- 6+ genuine refusals, varied phrasing -------------------------------
    ("The provided context does not mention Apple's segment revenue.", True, "refusal/does-not-mention"),
    ("This information cannot be found in the retrieved excerpts.", True, "refusal/cannot-be-found"),
    ("No relevant information was found in the filing.", True, "refusal/no-relevant-information"),
    ("The figure is not explicitly stated in the documents provided.", True, "refusal/not-explicitly-stated"),
    ("I'm unable to answer that from the 10-K excerpts supplied.", True, "refusal/unable-to-answer"),
    ("The context provided is insufficient to determine the operating margin.", True, "refusal/insufficient-context"),
    ("That detail does not appear anywhere in the supplied sections.", True, "refusal/does-not-appear"),
    ("Sorry, the excerpts don't contain the requested figure.", True, "refusal/dont-contain-contraction"),
    ("The answer is not available in the provided 10-K sections.", True, "refusal/not-available-in-the"),
    # ---- 6+ legitimate answers containing refusal-adjacent vocabulary -------
    ("Apple's 10-K does not mention cryptocurrency holdings, but total net sales were $391,035 million in FY2024.",
     False, "legit/contains-does-not-mention-plus-answer"),
    ("The filing states that research and development expense was $29,510 million.", False, "legit/word-state"),
    ("Management provide guidance only on a segment basis; segment revenue was $96,169 million.", False, "legit/word-provide"),
    ("Item 1A does mention climate risk among the principal risk factors disclosed.", False, "legit/word-mention"),
    ("Net income cannot be found by simply adding the segments; the consolidated figure is $93,736 million.",
     False, "legit/cannot-be-found-rhetorical"),
    ("The company does not discuss dividends in Item 7, but Item 5 reports $15.2 billion returned to shareholders.",
     False, "legit/does-not-discuss-plus-answer"),
    ("Operating income was $123,216 million, which is explicitly stated in the consolidated statements of operations.",
     False, "legit/explicitly-stated-positive"),
    ("Total assets at year end were $364,980 million as provided in the balance sheet.", False, "legit/word-provided"),
]


def test_refusal_regex_precision_recall(capsys):
    rows = []
    fp = fn = 0
    for text, expected, label in CASES:
        got = Q._is_refusal(text)
        ok = got == expected
        kind = "ok"
        if not ok and got and not expected:
            kind, fp = "FALSE_POSITIVE", fp + 1
        elif not ok and expected and not got:
            kind, fn = "FALSE_NEGATIVE", fn + 1
        rows.append(
            {"label": label, "expected_refusal": expected, "detected_refusal": got,
             "outcome": kind, "text": text}
        )

    n_ref = sum(1 for _, e, _ in CASES if e)
    n_leg = len(CASES) - n_ref
    summary = {
        "total_cases": len(CASES),
        "genuine_refusals": n_ref,
        "legitimate_answers": n_leg,
        "false_positives": fp,
        "false_negatives": fn,
        "recall_on_refusals": round((n_ref - fn) / n_ref, 3),
        "precision_note": "a false positive costs one extra retrieve+generate round trip (2 Groq calls)",
        "cases": rows,
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["label", "expected_refusal", "detected_refusal", "outcome", "text"])
        w.writeheader()
        w.writerows(rows)

    print(f"\nK11 cases={len(CASES)}  false_positives={fp}  false_negatives={fn}")
    for r in rows:
        if r["outcome"] != "ok":
            print(f"  {r['outcome']:15s} [{r['label']}] {r['text'][:90]}")
    # Measurement test: never fails the suite, the numbers are the finding.
    assert True
