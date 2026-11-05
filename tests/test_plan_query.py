"""Plan reads: coverage by date, gaps, ranges, key sessions and search."""

from datetime import date

import pytest
from plan_fixtures import (
    NEXT_END,
    NEXT_START,
    gym_content,
    query_for,
    save_block,
    save_gym,
    seed_content_row,
)

from training_ai.conversation_store import ConversationStore
from training_ai.plan_commands import PlanCommands
from training_ai.plan_models import EditOperations
from training_ai.plan_query import PlanNotFound


@pytest.fixture
def plans(tmp_path):
    store = ConversationStore(tmp_path / "plans.sqlite3")
    store.initialize()
    seed_content_row(store)
    commands = PlanCommands(store)
    gym = save_gym(commands)
    first = save_block(commands)
    second = save_block(commands, start=NEXT_START, end=NEXT_END, title="Fictional Block 4")
    return commands, query_for(commands), gym.plan_id, first.plan_id, second.plan_id


def test_programme_is_active_across_two_blocks_and_in_a_gap(plans):
    commands, query, gym, first, second = plans
    assert query.block_on(date(2030, 3, 20))["id"] == first
    assert query.block_on(date(2030, 4, 2))["id"] == second
    assert [p["id"] for p in query.programmes_on(date(2030, 3, 20))] == [gym]
    assert [p["id"] for p in query.programmes_on(date(2030, 4, 2))] == [gym]
    assert query.block_on(date(2030, 5, 1)) is None and query.programmes_on(date(2030, 5, 1)) == []
    ended, upcoming = query.neighbours(date(2030, 5, 1))
    assert ended["id"] == second and upcoming is None
    ended, upcoming = query.neighbours(date(2030, 3, 1))
    assert ended is None and upcoming["id"] == first


def test_off_season_guidance_without_a_block(plans):
    commands, query, *_ = plans
    off = commands.save_extraction(
        "programme",
        date(2030, 9, 1),
        date(2030, 10, 18),
        gym_content(
            title="Off-season guidance", workouts=[], recurring=[{"text": "Run 3 x 40 min a week"}]
        ),
        [],
        "m",
        "v1",
    )
    assert query.block_on(date(2030, 9, 10)) is None
    assert [p["title"] for p in query.programmes_on(date(2030, 9, 10))] == ["Off-season guidance"]
    plans_in_range = query.range_plans(date(2030, 9, 1), date(2030, 9, 7))
    assert [p["id"] for p in plans_in_range] == [off.plan_id]
    assert plans_in_range[0]["recurring"][0]["text"] == "Run 3 x 40 min a week"


def test_sessions_next_key_session_and_listing(plans):
    commands, query, gym, first, second = plans
    sessions = query.sessions(date(2030, 3, 4), date(2030, 3, 10))
    assert [s["date"] for s in sessions] == [
        "2030-03-05",
        "2030-03-06",
        "2030-03-07",
        "2030-03-09",
        "2030-03-10",
    ]
    assert query.next_key_session(date(2030, 3, 7))["title"] == "Long ride or alternatives"
    assert query.next_key_session(date(2030, 4, 30)) is None
    assert [p["title"] for p in query.list()] == [
        "Fictional Block 4",
        "Fictional Block 3",
        "Fictional Gym Programme",
    ]
    assert query.workout(first, "swim PROGRAM 2")["description"] == "4 x 400 m steady"
    assert query.workout(first, "Workout B") is None
    with pytest.raises(PlanNotFound):
        query.get("missing")


def test_search_prefers_newest_plans_and_caps_hits(plans):
    commands, query, gym, first, second = plans
    hits = query.search("swim")
    assert [h["plan_id"] for h in hits][:2] == [second, second]
    assert {h["kind"] for h in hits} == {"workout"}
    assert len(query.search("a", limit=3)) == 3
    assert query.search("sleep")[0]["snippet"].startswith("Sleep 8 hours")
    assert query.search("nothing-here") == []


def test_search_folds_non_ascii_and_treats_wildcards_as_text(plans):
    commands, query, gym, first, second = plans
    commands.edit(
        first,
        1,
        EditOperations.model_validate(
            {
                "operations": [
                    {
                        "op": "add_workout",
                        "workout": {"name": "MÄKI", "description": "4 min vedot, 4_min tauko"},
                    }
                ]
            }
        ),
    )
    assert [h["title"] for h in query.search("mäki")] == ["MÄKI"]
    assert [h["title"] for h in query.search("4_MIN")] == ["MÄKI"]
    assert query.search("v%t") == []
