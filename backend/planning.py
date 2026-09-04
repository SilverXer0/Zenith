"""Read-only task planning projections. No model or external service required."""

import re
from datetime import date, datetime, time, timedelta, timezone

from .database import Database
from .errors import ApiError


def planning_date(value: str | None) -> str:
    value = value or datetime.now(timezone.utc).date().isoformat()
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        date.fromisoformat(value)
    except ValueError:
        raise ApiError(400, "Briefing date must be a valid YYYY-MM-DD date.") from None
    return value


def weekly_start(value: str | None) -> str:
    try:
        return planning_date(value)
    except ApiError:
        raise ApiError(400, "Weekly plan start must be a valid YYYY-MM-DD date.") from None


def add_days(day: str, days: int) -> str:
    try:
        return (date.fromisoformat(day) + timedelta(days=days)).isoformat()
    except OverflowError:
        raise ApiError(400, "Planning date is outside the supported range.") from None


def summary_window(day: str, offset: int) -> tuple[str, str]:
    if not -840 <= offset <= 840:
        raise ApiError(400, "Summary timezone offset must be a valid number of minutes.")
    try:
        # Match JavaScript Date.getTimezoneOffset(): positive means west of UTC.
        start = datetime.combine(date.fromisoformat(day), time(), timezone.utc) + timedelta(minutes=offset)
        end = start + timedelta(days=1)
    except (ValueError, OverflowError):
        raise ApiError(400, "Summary date is outside the supported range.") from None
    return tuple(value.isoformat(timespec="milliseconds").replace("+00:00", "Z") for value in (start, end))


class Planning:
    def __init__(self, database: Database):
        self.database = database

    def briefing(self, user_id: str, day: str) -> dict:
        tasks = self.database.list_tasks(user_id)
        opened = [task for task in tasks if not task["completed"]]
        priority = {"high": 0, "medium": 1, "low": 2}

        def rank(task):
            due = task["dueDate"]
            due_rank = 0 if due and due < day else 1 if due == day else 2
            return due_rank, due or "9999", priority.get(task["priority"], 1)

        # Stable sorts preserve the Node tie-break: most recently updated first.
        recent = sorted(opened, key=lambda task: task["updatedAt"], reverse=True)
        focus = sorted(recent, key=rank)[:5]
        return {"date": day, "counts": {
            "open": len(opened), "overdue": sum(bool(task["dueDate"] and task["dueDate"] < day) for task in opened),
            "dueToday": sum(task["dueDate"] == day for task in opened),
        }, "focusTasks": focus}

    def unavailable_calendar(self, user_id: str) -> dict:
        # The account survives migration, but event reads belong to the Calendar port.
        return {"connected": self.database.calendar_connected(user_id), "available": False, "events": []}

    def morning(self, user_id: str, day: str) -> dict:
        opened = [task for task in self.database.list_tasks(user_id) if not task["completed"]]
        overdue = [task for task in opened if task["dueDate"] and task["dueDate"] < day]
        due_today = [task for task in opened if task["dueDate"] == day]
        horizon = add_days(day, 4)
        upcoming = sorted((task for task in opened if task["dueDate"] and day < task["dueDate"] < horizon),
                          key=lambda task: task["dueDate"])[:5]
        calendar = self.unavailable_calendar(user_id)
        parts = []
        if overdue:
            parts.append(f"{len(overdue)} overdue")
        if due_today:
            parts.append(f"{len(due_today)} due today")
        return {"date": day, "summary": " · ".join(parts) if parts else "No urgent items this morning.",
                "overdue": overdue, "dueToday": due_today, "upcoming": upcoming, "calendar": calendar}

    def weekly(self, user_id: str, start: str) -> dict:
        opened = [task for task in self.database.list_tasks(user_id) if not task["completed"]]
        end = add_days(start, 7)
        scheduled = [task for task in opened if task["dueDate"] and start <= task["dueDate"] < end]
        overdue = [task for task in opened if task["dueDate"] and task["dueDate"] < start]
        unscheduled = [task for task in opened if not task["dueDate"]]
        days = [{"date": day, "tasks": [task for task in scheduled if task["dueDate"] == day]}
                for day in (add_days(start, index) for index in range(7))]
        return {"start": start, "end": end,
                "counts": {"open": len(opened), "overdue": len(overdue), "scheduled": len(scheduled), "unscheduled": len(unscheduled)},
                "days": days, "unscheduled": unscheduled, "calendar": self.unavailable_calendar(user_id)}

    def daily_summary(self, user_id: str, day: str, offset: int) -> dict:
        start, end = summary_window(day, offset)
        tasks, completed = self.database.summary_data(user_id, start, end)
        created = [task for task in tasks if start <= task["createdAt"] < end]
        count = len(completed)
        summary = f"{count} task{'s' if count != 1 else ''} completed today." if count else "No tasks completed today."
        return {"date": day, "summary": summary,
                "counts": {"completed": count, "created": len(created), "open": sum(not task["completed"] for task in tasks)},
                "completedTasks": completed, "createdTasks": created}
