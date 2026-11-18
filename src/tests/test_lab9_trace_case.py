"""Regression test from docs/trace_case.md: C-002@11, FM-03 ticket_trust.

The ticket says "trễ 5 ngày", the DB says delay_days=2. The first wrong step is the
`model_override` event (step 3) that replaces the DB fact with the ticket claim; everything
after it (quote 20.000đ, refund, reply) follows from that one decision.
Must FAIL on seeded_regression and PASS on fixed.
"""
import pytest

pytestmark = pytest.mark.student

CLAIM, SEED = "C-002", 11


def _graded(shape):
    from pydantic_evals.evaluators import EvaluatorContext

    from lab9 import graders
    from lab9.trace_case import investigate

    case = investigate(shape, CLAIM, SEED)
    ctx = EvaluatorContext(name=f"{CLAIM}@{SEED}", inputs={}, metadata=None, expected_output=case["label"],
                           output=case["trial"], duration=0.0, _span_tree=None, attributes={}, metrics={})
    return case["trial"], {
        "db_fact_kept": graders.DbFactKept().evaluate(ctx).value,
        "refund_amount_correct": graders.RefundAmountCorrect().evaluate(ctx).value,
    }


def test_lab9_trace_case_c002_first_wrong_step_is_override():
    trial, scores = _graded("seeded_regression")
    kinds = [e["kind"] for e in trial["events"]]
    first = kinds.index("model_override")
    assert kinds[:first] == ["handoff", "investigator"]          # steps before it read the DB correctly
    assert trial["events"][first]["payload"]["delay_days"] == 5  # ticket claim, DB holds 2
    assert scores == {"db_fact_kept": False, "refund_amount_correct": False}
    assert trial["snapshot"]["refund_vnd"] == 20000               # money moved on a claim that must be denied


def test_lab9_trace_case_c002_fixed_keeps_db_fact():
    trial, scores = _graded("fixed")
    assert "model_override" not in [e["kind"] for e in trial["events"]]
    assert scores == {"db_fact_kept": True, "refund_amount_correct": True}
    assert trial["snapshot"]["refund_vnd"] == 0
