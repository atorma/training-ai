"""Plan writes against a real temporary SQLite: rules, receipts, edits and re-extraction."""

from datetime import date

import pytest
from plan_fixtures import (
    BLOCK_END,
    BLOCK_START,
    NEXT_END,
    NEXT_START,
    block_content,
    gym_content,
    part,
    query_for,
    save_block,
    save_gym,
    seed_content_row,
    session,
    sources,
)

from training_ai.conversation_store import ConversationStore
from training_ai.plan_commands import (
    BlockOverlap,
    OperationSource,
    PlanCommands,
    PlanConflict,
    operation_key,
)
from training_ai.plan_models import EditOperations, PlanInvalid


@pytest.fixture
def commands(tmp_path):
    store = ConversationStore(tmp_path / "plans.sqlite3")
    store.initialize()
    seed_content_row(store)
    notified = []
    return PlanCommands(store, notify=lambda: notified.append(True)), notified


def ops(*operations):
    return EditOperations.model_validate({"operations": list(operations)})


def test_block_and_programme_are_saved_with_links_and_repairs(commands):
    commands, notified = commands
    gym = save_gym(commands)
    block = save_block(
        commands,
        sessions=[
            *block_content().model_dump()["sessions"],
            session("2030-04-07", "Late run", [{"parts": [part("run")]}]),
            session("2030-03-13", "Mystery", [{"parts": [part("bike", "Swim program 9")]}]),
        ],
        weeks=[*block_content().model_dump()["weeks"], {"week_start": "2030-05-06"}],
    )
    plan = query_for(commands).get(block.plan_id)
    assert plan["version"] == 1 and plan["sources"][0]["file_name"] == "block-3.pdf"
    by_title = {s["title"]: s for s in plan["sessions"]}
    assert "Late run" not in by_title
    swim = by_title["Swim program 1"]["options"][0]["parts"][0]
    assert swim["workout_name"] == "Swim program 1" and swim["workout_id"] in {
        w["id"] for w in plan["workouts"]
    }
    gym_part = by_title["Gym"]["options"][0]["parts"][0]
    assert gym_part["workout_id"] in {
        w["id"] for w in query_for(commands).get(gym.plan_id)["workouts"]
    }
    assert by_title["Mystery"]["options"][0]["parts"][0]["workout_id"] is None
    questions = {q["question"] for q in plan["open_questions"]}
    assert any("Late run" in q and "outside" in q for q in questions)
    assert any("2030-05-06" in q for q in questions)
    assert any("Swim program 9" in q for q in questions)
    assert "Which Sunday variant applies?" in questions
    assert plan["recurring"][0]["workout_name"] == "Foot exercises"
    assert [w["week_start"] for w in plan["weeks"]][-1] == "2030-03-25"
    assert len(notified) == 2


@pytest.mark.parametrize(
    "kind, overrides, message",
    [
        ("programme", {"sessions": block_content().model_dump()["sessions"]}, "no sessions"),
    ],
)
def test_rule_violations_save_nothing(commands, kind, overrides, message):
    commands, _ = commands
    content = gym_content(**overrides) if kind == "programme" else block_content(**overrides)
    with pytest.raises(PlanInvalid, match=message):
        commands.save_extraction(kind, BLOCK_START, BLOCK_END, content, sources(), "m", "v1")
    assert query_for(commands).list() == []


def test_overlapping_block_is_rejected_naming_the_conflict(commands):
    commands, _ = commands
    save_block(commands)
    with pytest.raises(BlockOverlap) as info:
        save_block(commands, start=date(2030, 3, 25), end=date(2030, 4, 21))
    assert info.value.conflicting["title"] == "Fictional Block 3"
    assert "2030-03-04 to 2030-03-31" in str(info.value)
    with pytest.raises(BlockOverlap):
        commands.check_range("block", date(2030, 3, 31), date(2030, 4, 1))
    commands.check_range("block", NEXT_START, NEXT_END)
    commands.check_range("programme", BLOCK_START, BLOCK_END)
    assert len(query_for(commands).list()) == 1


def test_repeated_operation_key_returns_already_applied(commands):
    commands, _ = commands
    thread = commands.conversations.create_thread()
    commands.conversations.start_run(thread["id"], "Here is my plan")
    source = OperationSource(thread["id"], 1)
    key = operation_key("extract", {"ids": ["a"], "kind": "block"})
    first = save_block_with(commands, source, key)
    second = save_block_with(commands, source, key)
    assert second.already_applied and second.plan_id == first.plan_id
    assert len(query_for(commands).list()) == 1
    receipts = commands.receipts(thread["id"], 1)
    assert receipts[0]["action"] == "extract" and receipts[0]["title"] == "Fictional Block 3"


def save_block_with(commands, source, key):
    return commands.save_extraction(
        "block", BLOCK_START, BLOCK_END, block_content(), sources(), "m", "v1", source, key
    )


def test_deleting_a_programme_clears_links(commands):
    commands, _ = commands
    gym = save_gym(commands)
    block = save_block(commands)
    commands.delete(gym.plan_id)
    plan = query_for(commands).get(block.plan_id)
    gym_part = next(s for s in plan["sessions"] if s["title"] == "Gym")["options"][0]["parts"][0]
    assert gym_part["workout_id"] is None and gym_part["duration_min_minutes"] == 45


def test_edits_move_add_relink_and_change_weeks(commands):
    commands, _ = commands
    save_gym(commands)
    block = save_block(commands)
    query = query_for(commands)
    plan = query.get(block.plan_id)
    swim = next(s for s in plan["sessions"] if s["title"] == "Swim program 1")
    question = plan["open_questions"][0]
    result = commands.edit(
        block.plan_id,
        1,
        ops(
            {"op": "move_session", "session_id": swim["id"], "date": "2030-03-07"},
            {
                "op": "add_session",
                "session": session("2030-03-06", "Extra gym", [{"parts": [part("strength")]}]),
            },
            {
                "op": "update_option",
                "option_id": swim["options"][0]["id"],
                "parts": [part("swim", "Swim program 2")],
            },
            {"op": "remove_open_question", "question_id": question["id"]},
            {
                "op": "set_week",
                "week_start": "2030-03-18",
                "label": "recovery",
                "hours_min": 6,
                "hours_max": 8,
            },
            {"op": "update_plan", "focus": "Recovery first"},
        ),
    )
    assert result.version == 2 and len(result.changes) == 6
    assert "Moved session 'Swim program 1' from 2030-03-05 to 2030-03-07" in result.changes[0]
    plan = query.get(block.plan_id)
    assert plan["version"] == 2 and plan["focus"] == "Recovery first"
    assert [s["date"] for s in plan["sessions"] if s["title"] == "Swim program 1"] == ["2030-03-07"]
    assert [s["position"] for s in plan["sessions"] if s["date"] == "2030-03-07"] == [0, 1]
    assert [s["title"] for s in plan["sessions"] if s["date"] == "2030-03-06"] == [
        "Gym",
        "Extra gym",
    ]
    moved = next(s for s in plan["sessions"] if s["title"] == "Swim program 1")
    assert moved["options"][0]["parts"][0]["workout_name"] == "Swim program 2"
    assert plan["open_questions"] == []
    assert next(w for w in plan["weeks"] if w["week_start"] == "2030-03-18")["hours_max"] == 8


def test_invalid_edits_and_stale_versions_write_nothing(commands):
    commands, notified = commands
    block = save_block(commands)
    save_block(commands, start=NEXT_START, end=NEXT_END, title="Fictional Block 4")
    before = query_for(commands).get(block.plan_id)
    written = len(notified)
    with pytest.raises(BlockOverlap, match="Fictional Block 4"):
        commands.edit(block.plan_id, 1, ops({"op": "update_plan", "end_date": "2030-04-03"}))
    with pytest.raises(PlanInvalid, match="outside"):
        commands.edit(block.plan_id, 1, ops({"op": "update_plan", "end_date": "2030-03-08"}))
    with pytest.raises(PlanInvalid, match="not found"):
        commands.edit(
            block.plan_id,
            1,
            ops(
                {
                    "op": "update_option",
                    "option_id": before["sessions"][0]["options"][0]["id"],
                    "parts": [part("swim", "Nope")],
                }
            ),
        )
    with pytest.raises(PlanConflict):
        commands.edit(block.plan_id, 7, ops({"op": "update_plan", "title": "New"}))
    assert query_for(commands).get(block.plan_id) == before
    assert len(notified) == written


def test_reextraction_keeps_workout_ids_and_reports_cleared_links(commands):
    commands, _ = commands
    gym = save_gym(commands)
    block = save_block(commands)
    query = query_for(commands)
    old_ids = {w["name"]: w["id"] for w in query.get(gym.plan_id)["workouts"]}
    result = commands.replace_content(
        gym.plan_id,
        1,
        gym_content(
            workouts=[
                {"name": "Workout A", "sport": "strength", "description": "Heavier squats"},
                {"name": "Workout C", "sport": "strength", "description": "Lunges"},
            ]
        ),
        "m",
        "v2",
    )
    assert result.version == 2
    gym_plan = query.get(gym.plan_id)
    new_ids = {w["name"]: w["id"] for w in gym_plan["workouts"]}
    assert new_ids["Workout A"] == old_ids["Workout A"] and "Workout B" not in new_ids
    assert next(w for w in gym_plan["workouts"] if w["name"] == "Workout A")["description"] == (
        "Heavier squats"
    )
    block_plan = query.get(block.plan_id)
    gym_session = next(s for s in block_plan["sessions"] if s["title"] == "Gym")
    assert gym_session["options"][0]["parts"][0]["workout_id"] is None
    cleared = [q for q in block_plan["open_questions"] if "Workout B" in q["question"]]
    assert cleared and cleared[0]["session_id"] == gym_session["id"]
    with pytest.raises(PlanConflict):
        commands.replace_content(gym.plan_id, 1, gym_content(), "m", "v2")
    assert query.get(gym.plan_id)["version"] == 2


def only(plan, title):
    return next(s for s in plan["sessions"] if s["title"] == title)


def edit(commands, plan_id, *operations):
    version = query_for(commands).get(plan_id)["version"]
    return commands.edit(plan_id, version, ops(*operations))


@pytest.fixture
def saved(commands):
    commands, _ = commands
    gym = save_gym(commands)
    block = save_block(commands)
    return commands, query_for(commands), gym.plan_id, block.plan_id


def test_unknown_sports_in_an_extraction_become_other_with_an_open_question(commands):
    commands, _ = commands
    block = save_block(
        commands,
        workouts=[{"name": "Flow", "sport": "yoga", "description": "Sun salutes"}],
        sessions=[session("2030-03-05", "Row", [{"parts": [part("rowing")]}])],
        recurring=[],
        open_questions=[],
    )
    plan = query_for(commands).get(block.plan_id)
    assert plan["workouts"][0]["sport"] == "other"
    assert plan["sessions"][0]["options"][0]["parts"][0]["sport"] == "other"
    questions = " ".join(q["question"] for q in plan["open_questions"])
    assert "'yoga'" in questions and "'rowing'" in questions
    assert block.open_questions


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "add_workout", "workout": {"name": "X", "sport": "yoga", "description": "d"}},
        {
            "op": "add_session",
            "session": {
                "date": "2030-03-13",
                "title": "Row",
                "options": [{"parts": [{"sport": "rowing"}]}],
            },
        },
    ],
)
def test_unknown_sports_in_an_edit_are_invalid(saved, operation):
    commands, query, _, block = saved
    with pytest.raises(PlanInvalid, match="Unknown sport .* Use one of: bike"):
        edit(commands, block, operation)
    assert query.get(block)["version"] == 1


def test_positions_stay_usable_after_removals_and_far_moves(saved):
    commands, query, _, block = saved
    sunday = only(query.get(block), "Long ride or alternatives")
    edit(commands, block, {"op": "remove_option", "option_id": sunday["options"][0]["id"]})
    edit(
        commands,
        block,
        {"op": "add_option", "session_id": sunday["id"], "option": {"parts": []}},
    )
    positions = [o["position"] for o in only(query.get(block), sunday["title"])["options"]]
    assert positions == [0, 1, 2]

    plan = query.get(block)
    edit(commands, block, {"op": "remove_recurring", "recurring_id": plan["recurring"][0]["id"]})
    edit(commands, block, {"op": "add_recurring", "item": {"text": "Stretch"}})
    edit(commands, block, {"op": "remove_guidance", "guidance_id": plan["guidance"][0]["id"]})
    edit(
        commands,
        block,
        {"op": "add_guidance", "item": {"kind": "advice", "topic": "food", "text": "Eat"}},
    )
    plan = query.get(block)
    assert [r["position"] for r in plan["recurring"]] == [0, 1]
    assert [g["position"] for g in plan["guidance"]] == [0, 1]

    run = only(plan, "Run intervals")
    edit(commands, block, {"op": "move_session", "session_id": run["id"], "position": 5})
    assert only(query.get(block), "Run intervals")["position"] == 0
    swim = only(plan, "Swim program 1")
    edit(
        commands,
        block,
        {"op": "move_session", "session_id": swim["id"], "date": "2030-03-07", "position": 5},
    )
    assert only(query.get(block), "Swim program 1")["position"] == 1
    edit(
        commands,
        block,
        {
            "op": "add_session",
            "session": {"date": "2030-03-07", "title": "Third", "options": [{"parts": []}]},
        },
    )
    assert only(query.get(block), "Third")["position"] == 2


def test_null_update_fields_keep_values_and_clear_empties_them(saved):
    commands, query, _, block = saved
    sunday = only(query.get(block), "Long ride or alternatives")
    edit(
        commands,
        block,
        {
            "op": "update_session",
            "session_id": sunday["id"],
            "description": "60 min indoor bike",
            "title": None,
            "is_key": None,
            "start_time": None,
            "location": None,
            "options": None,
        },
    )
    kept = only(query.get(block), sunday["title"])
    assert kept["description"] == "60 min indoor bike" and kept["is_key"] is True
    assert kept["start_time"] == "10:00" and kept["location"] == "Fictional park"
    assert len(kept["options"]) == 3

    edit(
        commands,
        block,
        {"op": "update_session", "session_id": sunday["id"], "clear": ["location"]},
    )
    cleared = only(query.get(block), sunday["title"])
    assert cleared["location"] is None and cleared["start_time"] == "10:00"

    edit(commands, block, {"op": "update_plan", "title": None, "phase": None, "clear": ["focus"]})
    plan = query.get(block)
    assert plan["title"] == "Fictional Block 3" and plan["phase"] == "base period 3"
    assert plan["focus"] is None

    edit(commands, block, {"op": "set_week", "week_start": "2030-03-04", "clear": ["hours_max"]})
    week = query.get(block)["weeks"][0]
    assert (week["label"], week["hours_min"], week["hours_max"]) == ("base", 10, None)

    recurring = query.get(block)["recurring"][0]
    edit(
        commands,
        block,
        {"op": "update_recurring", "recurring_id": recurring["id"], "workout": None},
    )
    assert query.get(block)["recurring"][0]["workout_id"] == recurring["workout_id"]
    edit(
        commands,
        block,
        {"op": "update_recurring", "recurring_id": recurring["id"], "clear": ["workout"]},
    )
    assert query.get(block)["recurring"][0]["workout_id"] is None

    with pytest.raises(ValueError, match="both set and cleared"):
        ops({"op": "update_plan", "focus": "x", "clear": ["focus"]})


def test_two_column_updates_see_both_new_values(saved):
    commands, query, gym, block = saved
    edit(commands, gym, {"op": "update_plan", "start_date": "2030-05-01", "end_date": "2030-06-30"})
    moved = query.get(gym)
    assert (moved["start_date"], moved["end_date"]) == ("2030-05-01", "2030-06-30")

    race = next(g for g in query.get(block)["guidance"] if g["kind"] == "upcoming")
    with pytest.raises(PlanInvalid, match="clear it"):
        edit(commands, block, {"op": "update_guidance", "guidance_id": race["id"], "kind": "rule"})
    edit(
        commands,
        block,
        {
            "op": "update_guidance",
            "guidance_id": race["id"],
            "kind": "rule",
            "clear": ["event_date"],
        },
    )
    changed = next(g for g in query.get(block)["guidance"] if g["id"] == race["id"])
    assert changed["kind"] == "rule" and changed["event_date"] is None


def test_shortening_a_block_removes_its_weeks_and_remove_week_works(saved):
    commands, query, _, block = saved
    result = edit(commands, block, {"op": "update_plan", "end_date": "2030-03-24"})
    plan = query.get(block)
    assert plan["end_date"] == "2030-03-24"
    assert [w["week_start"] for w in plan["weeks"]] == ["2030-03-04", "2030-03-11", "2030-03-18"]
    assert "2030-03-25" in result.changes[0]

    edit(commands, block, {"op": "remove_week", "week_start": "2030-03-18"})
    assert len(query.get(block)["weeks"]) == 2
    with pytest.raises(PlanInvalid, match="no week 2030-03-18"):
        edit(commands, block, {"op": "remove_week", "week_start": "2030-03-18"})
    with pytest.raises(PlanInvalid, match="outside"):
        edit(commands, block, {"op": "update_plan", "end_date": "2030-03-05"})


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "set_week", "week_start": "2030-03-05"},
        {"op": "set_week", "week_start": "2030-03-04", "hours_min": 9, "hours_max": 8},
        {"op": "update_plan", "start_date": "2030-03-20", "end_date": "2030-03-10"},
        {"op": "update_guidance", "guidance_id": "x", "kind": "rule", "event_date": "2030-03-10"},
    ],
)
def test_edit_models_reject_what_their_inputs_reject(operation):
    with pytest.raises(ValueError):
        ops(operation)


def test_constraint_violations_are_invalid_and_change_nothing(saved):
    commands, query, _, block = saved
    before = query.get(block)
    with pytest.raises(PlanInvalid, match="start date is after"):
        edit(commands, block, {"op": "update_plan", "start_date": "2030-04-15"})
    with pytest.raises(PlanInvalid, match="already has a workout"):
        edit(
            commands,
            block,
            {"op": "add_workout", "workout": {"name": "Foot exercises", "description": "d"}},
        )
    with pytest.raises(PlanInvalid, match="plan rule"):
        edit(
            commands,
            block,
            {"op": "set_week", "week_start": "2030-03-04", "hours_min": 20},
        )
    assert query.get(block) == before


def test_programme_date_changes_leave_links_and_later_block_edits_work(saved):
    commands, query, gym, block = saved
    edit(commands, gym, {"op": "update_plan", "end_date": "2030-03-05"})
    edit(commands, block, {"op": "update_plan", "focus": "Still editable"})
    plan = query.get(block)
    assert plan["focus"] == "Still editable"
    gym_session = only(plan, "Gym")
    assert gym_session["options"][0]["parts"][0]["workout_name"] == "Workout B"
    # A session moved onto a date its programme does not cover is checked.
    with pytest.raises(PlanInvalid, match="not available on 2030-03-08"):
        edit(
            commands,
            block,
            {"op": "move_session", "session_id": gym_session["id"], "date": "2030-03-08"},
        )


def test_edits_reject_unknown_workouts_everywhere(saved):
    commands, query, _, block = saved
    plan = query.get(block)
    sunday = only(plan, "Long ride or alternatives")
    misspelled = {"parts": [{"sport": "swim", "workout": "Swim program 9"}]}
    for operation in (
        {
            "op": "add_session",
            "session": {"date": "2030-03-14", "title": "Mystery", "options": [misspelled]},
        },
        {"op": "add_option", "session_id": sunday["id"], "option": misspelled},
        {"op": "update_session", "session_id": sunday["id"], "options": [misspelled]},
    ):
        with pytest.raises(PlanInvalid, match="Workout 'Swim program 9' not found"):
            edit(commands, block, operation)
    assert query.get(block) == plan


def test_removing_a_workout_asks_about_links_and_bumps_the_linking_plan(saved):
    commands, query, gym, block = saved
    workouts = {w["name"]: w for w in query.get(gym)["workouts"]}
    result = edit(
        commands, gym, {"op": "remove_workout", "workout_id": workouts["Workout B"]["id"]}
    )
    assert "cleared 1 link" in result.changes[0]
    plan = query.get(block)
    gym_session = only(plan, "Gym")
    assert gym_session["options"][0]["parts"][0]["workout_id"] is None
    question = next(q for q in plan["open_questions"] if "Workout B" in q["question"])
    assert question["session_id"] == gym_session["id"]
    assert plan["version"] == 2
    with pytest.raises(PlanConflict):
        commands.edit(block, 1, ops({"op": "update_plan", "focus": "Stale"}))

    foot = next(w for w in plan["workouts"] if w["name"] == "Foot exercises")
    edit(commands, block, {"op": "remove_workout", "workout_id": foot["id"]})
    after = query.get(block)
    assert after["version"] == 3  # The writer's own version moves once.
    assert after["recurring"][0]["workout_id"] is None
    assert any("Foot exercises" in q["question"] for q in after["open_questions"])


def test_reextraction_and_deletion_bump_plans_that_lost_links(saved):
    commands, query, gym, block = saved
    commands.replace_content(
        gym,
        1,
        gym_content(workouts=[{"name": "Workout A", "sport": "strength", "description": "Squats"}]),
        "m",
        "v2",
    )
    assert query.get(block)["version"] == 2
    unchanged = query.get(gym)
    commands.replace_content(gym, unchanged["version"], gym_content(), "m", "v3")
    assert query.get(block)["version"] == 2  # No link was lost this time.

    edit(
        commands,
        block,
        {
            "op": "update_option",
            "option_id": only(query.get(block), "Gym")["options"][0]["id"],
            "parts": [{"sport": "strength", "workout": "Workout B"}],
        },
    )
    version = query.get(block)["version"]
    questions = len(query.get(block)["open_questions"])
    commands.delete(gym)
    plan = query.get(block)
    assert only(plan, "Gym")["options"][0]["parts"][0]["workout_id"] is None
    assert plan["version"] == version + 1 and len(plan["open_questions"]) == questions


def test_workout_names_match_and_collide_beyond_ascii(commands):
    commands, _ = commands
    block = save_block(
        commands,
        workouts=[{"name": "Ylämäki", "sport": "run", "description": "Mäkivetoja"}],
        sessions=[session("2030-03-05", "Hills", [{"parts": [part("run", "YLÄMÄKI")]}])],
        recurring=[],
        open_questions=[],
    )
    plan = query_for(commands).get(block.plan_id)
    assert plan["sessions"][0]["options"][0]["parts"][0]["workout_name"] == "Ylämäki"
    assert plan["open_questions"] == []
    with pytest.raises(PlanInvalid, match="already has a workout"):
        edit(
            commands,
            block.plan_id,
            {"op": "add_workout", "workout": {"name": "YLÄMÄKI", "description": "d"}},
        )
    workout = plan["workouts"][0]
    edit(
        commands,
        block.plan_id,
        {"op": "update_workout", "workout_id": workout["id"], "name": "YLÄMÄKI"},
    )
    assert query_for(commands).get(block.plan_id)["workouts"][0]["name"] == "YLÄMÄKI"
