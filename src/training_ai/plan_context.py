"""The current-plan pointer: a short deterministic summary of one day, built without a model."""

from __future__ import annotations

from datetime import date

from training_ai.plan_models import Session
from training_ai.plan_query import PlanQuery, day_range

POINTER_LIMIT = 1500


def _duration(part) -> str:
    lo, hi = part["duration_min_minutes"], part["duration_max_minutes"]
    if lo is None and hi is None:
        return ""
    if lo == hi or hi is None:
        return f" {lo} min"
    if lo is None:
        return f" {hi} min"
    return f" {lo}-{hi} min"


def _hours(value) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def session_line(session: Session) -> str:
    """Title, key flag and each option's parts as 'sport duration', options separated by ' / '."""
    options = []
    for option in session["options"]:
        if not option["parts"]:
            options.append("rest")
            continue
        parts = " + ".join(f"{p['sport']}{_duration(p)}" for p in option["parts"])
        options.append(f"{parts} ({option['condition']})" if option["condition"] else parts)
    text = session["title"] + (" [KEY]" if session["is_key"] else "")
    if session["start_time"]:
        text += f" at {session['start_time']}"
    return f"{text}: " + " / ".join(options)


def plan_pointer(query: PlanQuery, today: date) -> str:
    lines = [f"Plan pointer for {today.isoformat()} ({today.strftime('%A')}):"]
    block = query.block_on(today)
    if block:
        weeks = query.get(block["id"])["weeks"]
        monday = today.toordinal() - today.weekday()
        current = next(
            (w for w in weeks if date.fromisoformat(w["week_start"]).toordinal() == monday), None
        )
        number = sum(1 for w in weeks if date.fromisoformat(w["week_start"]).toordinal() <= monday)
        header = f"Block: {block['title']}"
        if block["phase"]:
            header += f" ({block['phase']})"
        header += f", {block['start_date']} to {block['end_date']}"
        if weeks:
            header += f", week {number} of {len(weeks)}"
        if current:
            if current["label"]:
                header += f" ({current['label']})"
            if current["hours_min"] is not None or current["hours_max"] is not None:
                header += f", hours {_hours(current['hours_min'])}-{_hours(current['hours_max'])}"
        lines.append(header + ".")
        sessions = [s for s in query.sessions(today, today) if s["plan_id"] == block["id"]]
        if sessions:
            lines.append("Today: " + "; ".join(session_line(s) for s in sessions) + ".")
        else:
            lines.append("Today: no session planned.")
    else:
        ended, upcoming = query.neighbours(today)
        text = "No block covers today."
        if ended:
            text += f" Last block '{ended['title']}' ended {ended['end_date']}."
        if upcoming:
            text += f" Next block '{upcoming['title']}' starts {upcoming['start_date']}."
        lines.append(text)
    next_key = query.next_key_session(today)
    if next_key:
        lines.append(f"Next key session: {next_key['date']} {next_key['title']}.")
    programmes = query.programmes_on(today)
    lines.append(
        "Active programmes: "
        + (", ".join(p["title"] for p in programmes) if programmes else "none")
        + "."
    )
    text = "\n".join(lines)
    if len(text) > POINTER_LIMIT:
        text = text[: POINTER_LIMIT - 1] + "…"
    return text


def compact_days(query: PlanQuery, start: date, end: date) -> list[dict]:
    """Session titles, sports, durations and key flags per day, for the summary's week ahead."""
    by_day: dict[str, list[str]] = {}
    for session in query.sessions(start, end):
        by_day.setdefault(session["date"], []).append(session_line(session))
    return [
        {"date": day.isoformat(), "sessions": by_day.get(day.isoformat(), [])}
        for day in day_range(start, end)
    ]
