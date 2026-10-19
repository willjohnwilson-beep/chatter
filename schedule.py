"""Things that happen on the world's clock: "a food drop every Wednesday", "a vote every other day".

The clock is the scene's `fiction_time`, free text the watcher moves forward as the story does. Whether an event is due is decided
here, in code, by reading two time stamps: nothing asks a model what day it is. An event fires when the clock has crossed a day it
falls on between the start and the end of a turn, so it never fires on a jump back, and at most once for a day it has already seen.

`when` is plain text so it can be hand-edited. Read:
  daily / every day                          every story day
  every other day / every 3 days             counted from the day the story first reached it (not that day itself)
  every Wednesday / Wednesdays / weekly on Monday and Friday   (a bare weekday means every one of them)
  15 October / 15 October 2026               once, on that date
Anything else is not understood (`parse_when` returns None), and the editor says so.
"""
import re
from datetime import date

import memory

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WORD = re.compile(r"[a-z]+")
_COUNTS = {"other": 2, "second": 2, "third": 3, "fourth": 4, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
_DAILY = re.compile(r"\b(daily|every day|each day|every single day)\b")
_EVERY_N = re.compile(r"\bevery (other|second|third|fourth|\d+|two|three|four|five|six|seven) days?\b")
_NOT_WEEKLY = re.compile(r"\b(other|second|third|fourth|alternate|fortnight|fortnightly|biweekly|month|monthly)\b")
CAP_DAYS = 14   # a long jump is only looked at for its last two weeks: enough for any weekly event, never a flood


def _weekday(word: str) -> int | None:
    base = word[:-1] if word.endswith("s") and word[:-1] in WEEKDAYS else word
    return WEEKDAYS.index(base) if base in WEEKDAYS else None


def weekdays_in(text: str | None) -> list[int]:
    """Weekdays named in the text (full names, singular or plural), as 0 (Monday) to 6, sorted. Short forms such as
    'sun' are not read: in a time stamp they are as likely to be the sun."""
    found = {_weekday(w) for w in _WORD.findall((text or "").lower())}
    return sorted(d for d in found if d is not None)


def weekday_of(text: str | None) -> int | None:
    """The first weekday a time stamp names, or None."""
    for w in _WORD.findall((text or "").lower()):
        if _weekday(w) is not None:
            return _weekday(w)
    return None


def parse_when(text: str | None) -> dict | None:
    """What a `when` means, or None if it can't be read. See the module docstring for what is understood."""
    s = " ".join((text or "").lower().replace("-", " ").split())
    if not s:
        return None
    if _DAILY.search(s):
        return {"kind": "every", "n": 1}
    m = _EVERY_N.search(s)
    if m:
        word = m.group(1)
        n = int(word) if word.isdigit() else _COUNTS[word]
        if n >= 1:
            return {"kind": "every", "n": n}
    sd = memory.story_day(s)
    if sd:
        return {"kind": "date", "month": sd[0], "day": sd[1], "year": sd[2]}
    days = weekdays_in(s)
    if days and not _NOT_WEEKLY.search(s):
        return {"kind": "weekdays", "days": days}
    return None


def read_clock(t: str | None) -> dict | None:
    """The day a time stamp names: {"ord": day number or None, "wd": weekday 0-6 or None, "year": bool}.
    None when it names neither a date nor a weekday (a bare 'late evening'). A weekday written in the stamp wins over
    the one the date works out to, since the story said it; a year-less date is placed in a fixed year, so it can still
    be compared with another year-less date."""
    if not t:
        return None
    sd = memory.story_day(t)
    wd = weekday_of(t)
    ordinal = None
    if sd:
        try:
            ordinal = date(sd[2] or 2001, sd[0], sd[1]).toordinal()
        except ValueError:
            ordinal = None
    if wd is None and ordinal is not None and sd and sd[2]:
        wd = date.fromordinal(ordinal).weekday()
    if ordinal is None and wd is None:
        return None
    return {"ord": ordinal, "wd": wd, "year": bool(sd and sd[2])}


def days_crossed(before: str | None, after: str | None) -> list[dict]:
    """The days the clock moved through from `before` to `after`, oldest first, each {"ord", "wd"} (either may be None when
    the stamps don't give it). Empty when it did not move to a later day, went backwards, or either stamp has no day in it.
    With dates on both it counts days; with only weekdays it counts forward round the week (so 'Tuesday' to 'Thursday' is
    Wednesday and Thursday, and one weekday to the same one is no time at all)."""
    a, b = read_clock(before), read_clock(after)
    if not a or not b:
        return []
    if a["ord"] is not None and b["ord"] is not None:
        delta = b["ord"] - a["ord"]
        if delta < 0 and not (a["year"] and b["year"]):
            delta += 365  # December into January with no year written
        if delta <= 0 or delta > 366:
            return []
        out = []
        for k in range(max(1, delta - CAP_DAYS + 1), delta + 1):
            if a["wd"] is not None:
                wd = (a["wd"] + k) % 7
            else:
                wd = (b["wd"] - (delta - k)) % 7 if b["wd"] is not None else None
            out.append({"ord": a["ord"] + k, "wd": wd})
        return out
    if a["wd"] is not None and b["wd"] is not None:
        return [{"ord": None, "wd": (a["wd"] + k) % 7} for k in range(1, (b["wd"] - a["wd"]) % 7 + 1)]
    return []


def matching_day(when: str | None, days: list[dict], since: str | None = None, after_ord: int | None = None) -> dict | None:
    """The latest of `days` that `when` falls on, or None. `since` is the stamp an 'every N days' count starts from;
    `after_ord` skips days up to and including one already fired."""
    rule = parse_when(when)
    if rule is None:
        return None
    anchor = (read_clock(since) or {}).get("ord")
    hit = None
    for d in days:
        if after_ord is not None and d["ord"] is not None and d["ord"] <= after_ord:
            continue
        if rule["kind"] == "weekdays":
            ok = d["wd"] in rule["days"]
        elif rule["kind"] == "every":
            if rule["n"] == 1:
                ok = True
            else:
                ok = d["ord"] is not None and anchor is not None and d["ord"] > anchor and (d["ord"] - anchor) % rule["n"] == 0
        else:
            if d["ord"] is None:
                continue
            day = date.fromordinal(d["ord"])
            ok = (day.month, day.day) == (rule["month"], rule["day"]) and (rule["year"] is None or rule["year"] == day.year)
        if ok:
            hit = d
    return hit
