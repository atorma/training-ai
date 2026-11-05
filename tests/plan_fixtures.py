"""Fictional plan content and a real temporary store for plan tests."""

from __future__ import annotations

from datetime import date

from training_ai.plan_commands import PlanCommands, SourceFile
from training_ai.plan_models import PlanContent
from training_ai.plan_query import PlanQuery

BLOCK_START, BLOCK_END = date(2030, 3, 4), date(2030, 3, 31)
NEXT_START, NEXT_END = date(2030, 4, 1), date(2030, 4, 28)
GYM_START, GYM_END = date(2030, 3, 4), date(2030, 4, 28)
SHA = "b" * 64


def part(sport, workout=None, lo=None, hi=None, description=None):
    return {
        "sport": sport,
        "workout": workout,
        "duration_min_minutes": lo,
        "duration_max_minutes": hi,
        "description": description,
    }


def session(day, title, options, is_key=False, **extra):
    return {"date": day, "title": title, "is_key": is_key, "options": options, **extra}


def block_content(**overrides) -> PlanContent:
    """A fictional four-week block with bricks, alternatives, rest and workout links."""
    content = {
        "title": "Fictional Block 3",
        "phase": "base period 3",
        "focus": "Aerobic volume with two key sessions a week",
        "weeks": [
            {"week_start": "2030-03-04", "label": "base", "hours_min": 10, "hours_max": 12},
            {"week_start": "2030-03-11", "label": "volume", "hours_min": 12, "hours_max": 14},
            {"week_start": "2030-03-18", "label": "recovery", "hours_min": 6, "hours_max": 8},
            {"week_start": "2030-03-25", "label": "base", "hours_min": 10, "hours_max": 12},
        ],
        "sessions": [
            session("2030-03-05", "Swim program 1", [{"parts": [part("swim", "Swim program 1")]}]),
            session(
                "2030-03-07",
                "Run intervals",
                [{"parts": [part("run", None, 60, 75, "5 x 4 min at threshold")]}],
                is_key=True,
            ),
            session(
                "2030-03-06",
                "Gym",
                [{"parts": [part("strength", "Workout B", 45, 60)]}],
            ),
            session(
                "2030-03-09",
                "Easy brick",
                [
                    {
                        "parts": [
                            part("bike", None, 60, 75, "Easy endurance with accelerations"),
                            part("run", None, 10, 30, "Easy with 2 x 20 s at race pace"),
                        ]
                    }
                ],
            ),
            session(
                "2030-03-10",
                "Long ride or alternatives",
                [
                    {"parts": [part("bike", None, 120, 120, "Easy")]},
                    {"parts": [part("bike", None, 90, 90), part("run", None, 30, 30)]},
                    {"parts": [part("walk", None, 90, 120, "Pole walking")]},
                ],
                is_key=True,
                start_time="10:00",
                location="Fictional park",
            ),
            session("2030-03-12", "Rest or swim", [{"parts": []}, {"parts": [part("swim")]}]),
        ],
        "workouts": [
            {"name": "Swim program 1", "sport": "swim", "description": "8 x 100 m build"},
            {"name": "Swim program 2", "sport": "swim", "description": "4 x 400 m steady"},
            {
                "name": "Foot exercises",
                "sport": "strength",
                "description": "Toe curls and calf raises",
            },
        ],
        "recurring": [
            {"text": "Foot strengthening 2–3 times a week", "workout": "Foot exercises"},
            {"text": "Short mobility routine before every run"},
        ],
        "guidance": [
            {"kind": "rule", "topic": "sleep", "text": "Sleep 8 hours before key sessions"},
            {
                "kind": "upcoming",
                "topic": "race",
                "text": "Club 10 km road race",
                "event_date": "2030-03-23",
            },
        ],
        "open_questions": [{"question": "Which Sunday variant applies?", "session_index": 4}],
    }
    content.update(overrides)
    return PlanContent.model_validate(content)


def gym_content(**overrides) -> PlanContent:
    content = {
        "title": "Fictional Gym Programme",
        "workouts": [
            {"name": "Workout A", "sport": "strength", "description": "Squats and presses"},
            {"name": "Workout B", "sport": "strength", "description": "Deadlifts and rows"},
        ],
        "recurring": [{"text": "Two gym sessions a week, alternating A and B"}],
        "guidance": [{"kind": "advice", "topic": "load", "text": "Add weight when 3 x 8 is easy"}],
    }
    content.update(overrides)
    return PlanContent.model_validate(content)


def sources(name="block-3.pdf", sha=SHA):
    return [SourceFile(sha, name, "application/pdf")]


def seed_content_row(store, sha=SHA):
    with store.connection() as db:
        db.execute(
            "INSERT INTO attachment_contents (sha256, size_bytes, created_at) VALUES (?, 1, 'now') "
            "ON CONFLICT DO NOTHING",
            (sha,),
        )


def save_gym(commands: PlanCommands, start=GYM_START, end=GYM_END, **overrides):
    return commands.save_extraction(
        "programme", start, end, gym_content(**overrides), sources("gym.pdf"), "m", "v1"
    )


def save_block(commands: PlanCommands, start=BLOCK_START, end=BLOCK_END, **overrides):
    return commands.save_extraction(
        "block", start, end, block_content(**overrides), sources(), "m", "v1"
    )


def query_for(commands: PlanCommands) -> PlanQuery:
    return PlanQuery(commands.conversations)
