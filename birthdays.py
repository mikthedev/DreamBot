from __future__ import annotations

import calendar
import random
import re
from dataclasses import dataclass
from datetime import date


BIRTHDAY_PATTERN = re.compile(
    r"^\s*(\d{1,2})[./\-](\d{1,2})(?:[./\-](\d{4}))?\s*$"
)


@dataclass(frozen=True)
class Birthday:
    month: int
    day: int
    year: int | None = None

    def display(self) -> str:
        if self.year:
            return f"{self.day:02d}.{self.month:02d}.{self.year}"
        return f"{self.day:02d}.{self.month:02d}"


def parse_birthday(text: str) -> Birthday | None:
    """Parse DD.MM or DD.MM.YYYY (also accepts - or /)."""
    match = BIRTHDAY_PATTERN.match(text.strip())
    if not match:
        return None

    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3)) if match.group(3) else None

    if month < 1 or month > 12:
        return None
    max_day = calendar.monthrange(year or 2024, month)[1]  # 2024 = leap year for Feb 29
    if day < 1 or day > max_day:
        return None
    if year is not None and (year < 1940 or year > date.today().year):
        return None
    return Birthday(month=month, day=day, year=year)


def is_birthday_today(bday: Birthday, today: date) -> bool:
    if bday.month == 2 and bday.day == 29 and not calendar.isleap(today.year):
        return today.month == 2 and today.day == 28
    return today.month == bday.month and today.day == bday.day


def celebration_message(mention: str, real_name: str | None) -> str:
    name = real_name or "friend"
    templates = [
        (
            f"Happy Birthday, {mention}! Dream Team is celebrating **{name}** today — "
            "wishing you an awesome year ahead!"
        ),
        (
            f"It's {mention}'s birthday! Happy Birthday, **{name}** — "
            "glad you're part of Dream Team."
        ),
        (
            f"Party time! Happy Birthday {mention} (**{name}**) — "
            "hope your day is as good as a perfect team win."
        ),
        (
            f"Dream Team shout-out: Happy Birthday, {mention}! "
            f"**{name}**, enjoy your day — you earned it."
        ),
    ]
    return random.choice(templates)
