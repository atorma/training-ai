"""Plan input models reject malformed content and accept the fictional brick example."""

import pytest
from plan_fixtures import block_content, part, session
from pydantic import ValidationError

from training_ai.plan_models import EditOperations, PlanContent


def test_brick_example_is_accepted_with_parts_and_options():
    content = block_content()
    brick = next(s for s in content.sessions if s.title == "Easy brick")
    assert [p.sport for p in brick.options[0].parts] == ["bike", "run"]
    alternatives = next(s for s in content.sessions if s.title == "Long ride or alternatives")
    assert len(alternatives.options) == 3 and len(alternatives.options[1].parts) == 2
    rest = next(s for s in content.sessions if s.title == "Rest or swim")
    assert rest.options[0].parts == []


@pytest.mark.parametrize(
    "overrides, message",
    [
        (
            {"sessions": [session("2030-03-05", "Run", [{"parts": [part("run", None, 60, 30)]}])]},
            "minimum duration",
        ),
        ({"weeks": [{"week_start": "2030-03-05"}]}, "Monday"),
        ({"weeks": [{"week_start": "2030-03-04", "hours_min": 9, "hours_max": 6}]}, "hours"),
        (
            {"guidance": [{"kind": "rule", "topic": "t", "text": "x", "event_date": "2030-03-04"}]},
            "upcoming",
        ),
        (
            {"workouts": [{"name": "A", "description": "x"}, {"name": "a", "description": "y"}]},
            "unique",
        ),
        ({"sessions": [session("2030-03-05", "Run", [])]}, "at least 1"),
        ({"open_questions": [{"question": "?", "session_index": 99}]}, "does not exist"),
        ({"sessions": [session("2030-3-5", "Run", [{"parts": []}])]}, "YYYY-MM-DD"),
        (
            {"sessions": [session("2030-03-05", "Run", [{"parts": []}])] * 201},
            "at most 200",
        ),
        ({"title": "x" * 201}, "at most 200"),
    ],
)
def test_invalid_content_is_rejected(overrides, message):
    with pytest.raises(ValidationError, match=message):
        block_content(**overrides)


def test_unknown_fields_and_operations_are_rejected():
    with pytest.raises(ValidationError, match="extra"):
        PlanContent.model_validate({"title": "x", "pages": 3})
    with pytest.raises(ValidationError):
        EditOperations.model_validate({"operations": [{"op": "explode", "plan_id": "p"}]})
    ops = EditOperations.model_validate(
        {"operations": [{"op": "move_session", "session_id": "s", "date": "2030-03-07"}]}
    )
    assert ops.operations[0].model_fields_set == {"op", "session_id", "date"}
