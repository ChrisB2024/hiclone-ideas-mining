from __future__ import annotations

import pytest
from pydantic import ValidationError

from ideas_mining.enrich import Enrichment


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "vertical": "insurance",
        "pain_point": "Agents manually re-key quote data between carrier portals.",
        "who_has_it": "independent insurance agent",
        "current_workaround": "Copy and paste",
        "willingness_to_pay_signal": "implied",
        "buildable_with_llm": True,
        "relevance": 8,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "overrides",
    [
        {"vertical": "healthcare"},
        {"willingness_to_pay_signal": "strong"},
        {"relevance": -1},
        {"relevance": 11},
    ],
)
def test_enrichment_rejects_invalid_enum_and_range_values(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Enrichment.model_validate(_payload(**overrides))


def test_neither_requires_zero_relevance() -> None:
    with pytest.raises(ValidationError):
        Enrichment.model_validate(_payload(vertical="neither", relevance=3))
