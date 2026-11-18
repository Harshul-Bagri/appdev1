import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from flask import current_app

IST = timezone(timedelta(hours=5, minutes=30))


class FormError(ValueError):
    pass


def now():
    clock = current_app.config.get("NOW")
    if clock is not None:
        return clock()
    return datetime.now(IST).replace(tzinfo=None)


def stamp(value=None):
    if value is None:
        value = now()
    return value.isoformat(timespec="minutes")


def date_value(value, label):
    message = f"Enter a valid date and time for {label.lower()}."
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise FormError(message) from None
    if len(value) != 16 or not 1900 <= parsed.year <= 9998:
        raise FormError(message)
    return parsed


def field(form, key, label, limit=120, required=True):
    value = form.get(key, "").strip()
    if not value and required:
        raise FormError(f"Enter {label.lower()}.")
    if len(value) > limit:
        raise FormError(f"{label} must be at most {limit} characters.")
    return value


def email_value(form):
    email = field(form, "email", "Email", 254).lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+", email):
        raise FormError("Enter a valid email address.")
    return email


def number(form, key, label, minimum=1, maximum=10000):
    try:
        value = int(form.get(key, ""))
    except ValueError:
        raise FormError(f"Enter a whole number for {label.lower()}.") from None
    if not minimum <= value <= maximum:
        raise FormError(f"{label} must be between {minimum} and {maximum}.")
    return value


def score_value(form, rubric):
    maximum = rubric["max_marks"]
    try:
        value = Decimal(form.get(f"score_{rubric['id']}", ""))
        if value.is_finite() and 0 <= value <= maximum and value == value.quantize(Decimal("0.01")):
            return value
    except InvalidOperation:
        pass
    raise FormError(f"Enter marks between 0 and {maximum} for {rubric['criterion']} (up to two decimals).")


def like_pattern(value):
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def pretty_date(value):
    return datetime.fromisoformat(value).strftime("%d %b %Y, %H:%M")


def pretty_status(value):
    return value.replace("_", " ").capitalize()
