"""Jalali (Persian / Solar Hijri) calendar conversion — stdlib only.

Strategy
--------
The *jalaali* (Borkowski) arithmetic is used to determine **Farvardin 1st**
(the Persian New Year) of a given Jalali year, which is always a day in
March of the corresponding Gregorian year. Everything else is plain
``datetime.date`` arithmetic:

* Jalali -> Gregorian : ``farvardin1(jy) + day_offset(jm, jd)``
* Gregorian -> Jalali : ``g - farvardin1(jy)``, then month arithmetic.

Leap years are derived from the anchors themselves (a year is leap when
Farvardin 1st of the next year is 366 days away), which is self-consistent
by construction. Verified against known anchors: 1399, 1403, 1408 are leap.

Public API
----------
    to_jalaali(gy, gm, gd) -> (jy, jm, jd)
    to_gregorian(jy, jm, jd) -> datetime.date
    format_jalali(date) -> '۱۲ مهر ۱۴۰۴'
    format_jalali_long(date) -> 'شنبه، ۱۲ مهر ۱۴۰۴'
    jalali_weekday_index(date) -> 0..6 (0 = Saturday)
    fa_digits(value) -> Persian numerals
"""
from __future__ import annotations

import contextvars
from datetime import date, datetime, timedelta

PERSIAN_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]

PERSIAN_WEEKDAYS = [
    "شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه",
]

_DIGIT_TABLE = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
# Persian and Arabic-Indic numerals, back to ASCII. Visitors type whichever
# their keyboard produces, and a count has to survive both.
_EN_DIGIT_TABLE = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# Limiting years for the Borkowski calendar cycles.
_BREAKS = [
    -61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210,
    1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178,
]

_MIN_JY = _BREAKS[0]
_MAX_JY = _BREAKS[-1] - 1


def _div(a: int, b: int) -> int:
    """Truncating division (matches the reference implementation)."""
    return int(a / b)


def _mod(a: int, b: int) -> int:
    return ((a % b) + b) % b


def _march_day(jy: int) -> int:
    """Day of March on which Farvardin 1st of ``jy`` falls (Borkowski)."""
    bl = len(_BREAKS)
    if jy < _MIN_JY or jy > _MAX_JY:
        raise ValueError(f"Jalaali year out of supported range: {jy}")

    gy = jy + 621
    leap_j = -14
    jp = _BREAKS[0]

    for i in range(1, bl):
        jm = _BREAKS[i]
        jump = jm - jp
        if jy < jm:
            break
        leap_j += _div(jump, 33) * 8 + _div(_mod(jump, 33), 4)
        jp = jm
    else:
        jump = _BREAKS[-1] - jp

    n = jy - jp
    leap_j += _div(n, 33) * 8 + _div(_mod(n, 33) + 3, 4)
    if _mod(jump, 33) == 4 and jump - n == 4:
        leap_j += 1

    leap_g = _div(gy, 4) - _div((_div(gy, 100) + 1) * 3, 4) - 150
    return 20 + leap_j - leap_g


def farvardin1(jy: int) -> date:
    """Gregorian date of Farvardin 1st of Jalali year ``jy``."""
    return date(jy + 621, 3, _march_day(jy))


def is_leap_jalali_year(jy: int) -> bool:
    """True when Esfand of ``jy`` has 30 days."""
    return (farvardin1(jy + 1) - farvardin1(jy)).days == 366


def _month_offset(jm: int, jd: int) -> int:
    """Days from Farvardin 1st to (jm, jd)."""
    return (jm - 1) * 31 - _div(jm, 7) * (jm - 7) + jd - 1


def _offset_to_month(offset: int, leap: bool) -> tuple[int, int]:
    """Inverse of :func:`_month_offset`."""
    if offset <= 185:                      # six 31-day months
        return offset // 31 + 1, offset % 31 + 1
    rest = offset - 186                    # five 30-day months, then Esfand
    jm = 7 + rest // 30
    jd = rest % 30 + 1
    if jm > 12:                            # overflow into Esfand 30
        return 12, 30 if leap else 29
    return jm, jd


def to_jalaali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g = date(gy, gm, gd)
    jy = gy - 621
    anchor = farvardin1(jy)
    if g < anchor:
        jy -= 1
        anchor = farvardin1(jy)
    offset = (g - anchor).days
    return (jy, *_offset_to_month(offset, is_leap_jalali_year(jy)))


def to_jalaali_date(g: date) -> tuple[int, int, int]:
    return to_jalaali(g.year, g.month, g.day)


def to_gregorian(jy: int, jm: int, jd: int) -> date:
    if not 1 <= jm <= 12:
        raise ValueError(f"Invalid Jalali month: {jm}")
    if jm <= 6:
        max_day = 31
    elif jm <= 11:
        max_day = 30
    else:  # Esfand: 30 in a leap year, otherwise 29
        max_day = 30 if is_leap_jalali_year(jy) else 29
    if not 1 <= jd <= max_day:
        raise ValueError(f"Invalid Jalali day: {jm}/{jd} (max {max_day})")
    return farvardin1(jy) + timedelta(days=_month_offset(jm, jd))


# The administrator asked for Latin numerals inside the admin panel: numbers
# there are read as addresses, phone numbers and amounts, not as Persian prose.
# A request-scoped switch (set once by the admin blueprint) turns every numeral
# conversion into a no-op for that request only — the public site keeps its
# Persian digits, and no template had to learn a second filter.
LATIN_DIGITS: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "latin_digits", default=False
)


def use_latin_digits(flag: bool = True) -> None:
    """Render numerals as ASCII for the rest of the current request/context."""
    LATIN_DIGITS.set(bool(flag))


def latin_digits() -> bool:
    return LATIN_DIGITS.get()


def fa_digits(value: int | str) -> str:
    if LATIN_DIGITS.get():
        return str(value)
    return str(value).translate(_DIGIT_TABLE)


def en_digits(value: int | str) -> str:
    """Inverse of :func:`fa_digits` (``۱۲۳`` → ``123``, ``١٢٣`` → ``123``)."""
    return str(value).translate(_EN_DIGIT_TABLE)


def jalali_weekday_index(g: date) -> int:
    """0 = Saturday … 6 = Friday (matches ``schedules.weekday``).

    Python: Monday=0 … Sunday=6. Persian week: Saturday=0 … Friday=6.
    """
    return (g.weekday() + 2) % 7


def format_jalali(d: date | datetime, *, with_weekday: bool = False) -> str:
    g = d.date() if isinstance(d, datetime) else d
    jy, jm, jd = to_jalaali_date(g)
    text = f"{fa_digits(jd)} {PERSIAN_MONTHS[jm - 1]} {fa_digits(jy)}"
    if with_weekday:
        text = f"{PERSIAN_WEEKDAYS[jalali_weekday_index(g)]}، {text}"
    return text
