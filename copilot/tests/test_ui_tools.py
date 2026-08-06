"""The UI patch contract.

These guard the boundary that makes the copilot safe to give to a user: it can
only change the interface through a ViewState patch, that patch travels the
same reducer a click does, and anything malformed is refused with a reason the
model can act on rather than being half-applied.
"""

from __future__ import annotations

import pytest

from dxb_copilot.ui_tools import (
    SET_VIEW_STATE,
    UI_TOOL_NAMES,
    PatchRejected,
    extract_patch,
)


def test_tool_is_named_and_schema_is_closed():
    assert SET_VIEW_STATE["name"] in UI_TOOL_NAMES
    schema = SET_VIEW_STATE["input_schema"]
    # additionalProperties=False so a model inventing a field gets a validation
    # error from the SDK rather than having it silently ignored.
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "view",
        "listing",
        "dashboard",
        "map",
        "explanation",
    }


def test_extract_splits_patch_from_explanation():
    patch, explanation = extract_patch(
        {
            "view": "dashboard",
            "dashboard": {"entityIds": [274, 292]},
            "explanation": "Plotted the two areas you asked about.",
        }
    )
    assert patch == {"view": "dashboard", "dashboard": {"entityIds": [274, 292]}}
    assert explanation == "Plotted the two areas you asked about."


def test_explanation_never_reaches_the_reducer():
    # The client validates ViewState strictly; an unknown `explanation` key
    # would fail the whole patch, so it must be stripped here.
    patch, _ = extract_patch({"view": "map", "explanation": "Switched to the map."})
    assert "explanation" not in patch


def test_missing_explanation_is_fine():
    patch, explanation = extract_patch({"view": "listing"})
    assert patch == {"view": "listing"}
    assert explanation is None


def test_empty_patch_is_rejected():
    with pytest.raises(PatchRejected, match="empty"):
        extract_patch({"explanation": "I changed nothing at all."})


def test_unknown_top_level_key_is_rejected():
    # Refused rather than filtered: a model asking for something we do not
    # support should be told so, not silently given a no-op.
    with pytest.raises(PatchRejected, match="Unknown top-level keys"):
        extract_patch({"view": "map", "database": {"drop": True}})


def test_non_object_input_is_rejected():
    with pytest.raises(PatchRejected):
        extract_patch(["view", "map"])  # type: ignore[arg-type]


def test_schema_documents_the_required_fact_filters():
    # Transactions and rents 422 without a scoping filter. If the model does
    # not know that, its first patch reliably produces a broken table.
    described = SET_VIEW_STATE["input_schema"]["properties"]["listing"]["properties"][
        "filters"
    ]["description"]
    assert "date_from" in described
    assert "start_date_from" in described
