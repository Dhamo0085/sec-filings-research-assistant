# Hand-drafted gold items

The numeric, computed, compare/trend and `as_of` items are generated from
`eval/gold/plan.py` against the facts store and SEC companyfacts, so their
expected values are not anyone's opinion. These two files are the part that
cannot be generated:

* `narrative.jsonl` — questions whose answer is prose. There is no oracle for
  "what does Apple say about supply-chain risk", so the expectation is not a
  string to match but the **section the answer must come from**
  (`expected_sections`), scored as section hit@k and MRR (spec section 11).
  Scoring retrieval rather than wording is deliberate: a judged-wording score
  would make this project's headline numbers depend on an LLM judge's taste.
* `abstain.jsonl` — questions the system must refuse, one per reason in
  `answering/outcome.py`'s `AbstainReason` enum wherever the corpus can produce
  that reason. Abstention is reported per reason, so "refused for the wrong
  reason" has to be distinguishable from "refused correctly".

Both are drafted here and **approved by the owner at the P4-02 gate**; until
then `verified_by` is `auto` for every item in these files, and no headline
metric may be published over them (spec section 11, D4).

`must_contain_numbers` on a narrative item lists figures that a correct answer
has to carry. It is used sparingly — only where the question asks for something
the filing states as a number — because a narrative answer is allowed to be
prose.
