from __future__ import annotations

import pytest

from ideas_mining.filter import FilterResult, classify
from ideas_mining.score import compute_score


LONG_NEUTRAL_BODY = "This describes an ordinary workflow without a pain phrase. " * 4


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"body": "[removed]"}, "removed"),
        ({"body": "short"}, "too_short"),
        ({"score": -1}, "negative_score"),
        ({"author": None}, "deleted_author"),
    ],
)
def test_classify_reject_rules_are_specific_and_nonempty(
    overrides: dict[str, object],
    expected_reason: str,
) -> None:
    inputs: dict[str, object] = {
        "title": None,
        "body": LONG_NEUTRAL_BODY,
        "score": 1,
        "author": "agent-1",
        "vertical_hint": "insurance",
    }
    inputs.update(overrides)

    result = classify(**inputs)  # type: ignore[arg-type]

    assert result == FilterResult("rejected", expected_reason)
    assert result.reason


def test_removed_beats_too_short() -> None:
    result = classify(
        title=None,
        body="[removed]",
        score=1,
        author="agent-1",
        vertical_hint="insurance",
    )

    assert result.reason == "removed"


def test_hinted_source_passes_without_a_vertical_keyword() -> None:
    result = classify(
        title=None,
        body=("We manually reconcile these records every week. " * 4),
        score=1,
        author="agent-1",
        vertical_hint="insurance",
    )

    assert result.state == "passed"
    assert result.reason


def test_shared_source_requires_a_vertical_keyword() -> None:
    result = classify(
        title=None,
        body=("We manually reconcile these records every week. " * 4),
        score=1,
        author="founder-1",
        vertical_hint=None,
    )

    assert result == FilterResult("rejected", "no_vertical_keyword")


def test_shared_source_with_keyword_and_pain_signal_passes() -> None:
    result = classify(
        title=None,
        body=("Our insurance agency manually re-keys quote data each morning. " * 3),
        score=1,
        author="agent-1",
        vertical_hint=None,
    )

    assert result.state == "passed"
    assert result.reason


def _score(**overrides: float | int) -> float:
    inputs: dict[str, float | int] = {
        "distinct_authors": 5,
        "days_since_last_seen": 7.0,
        "explicit_ratio": 0.2,
        "implied_ratio": 0.2,
        "buildable_ratio": 0.8,
        "avg_relevance": 8.0,
    }
    inputs.update(overrides)
    return compute_score(**inputs)  # type: ignore[arg-type]


def test_buildability_is_a_multiplicative_veto() -> None:
    assert _score(buildable_ratio=0.0) == 0.0


def test_fresh_high_frequency_cluster_outranks_stale_low_frequency_cluster() -> None:
    fresh = _score(distinct_authors=12, days_since_last_seen=7.0)
    stale = _score(distinct_authors=3, days_since_last_seen=60.0)

    assert fresh > stale


def test_wtp_ratios_are_monotonic_without_changing_frequency() -> None:
    weaker = _score(distinct_authors=8, explicit_ratio=0.1, implied_ratio=0.2)
    stronger = _score(distinct_authors=8, explicit_ratio=0.5, implied_ratio=0.2)

    assert stronger > weaker
