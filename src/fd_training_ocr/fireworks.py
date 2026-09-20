"""Build a reviewable Fireworks addActivity request from an OCR record."""

from __future__ import annotations

from datetime import datetime
from html import escape
import json
from typing import Any, Mapping


def _field_value(record: Mapping[str, Any], name: str) -> Any:
    field = record.get("fields", {}).get(name, {})
    if not isinstance(field, Mapping):
        return None
    for key in ("reviewed_value", "resolved_value", "normalized", "raw"):
        if field.get(key) is not None:
            return field[key]
    return None


def _fireworks_datetime(date_value: Any, time_value: Any) -> str | None:
    if date_value is None or time_value is None:
        return None
    try:
        value = datetime.strptime(f"{date_value} {time_value}", "%m/%d/%y %H:%M")
    except (TypeError, ValueError):
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def fireworks_staff_ids(record: Mapping[str, Any], roster: Any) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """Resolve reviewed attendees to configured Fireworks IDs without guessing."""
    resolved: list[int] = []
    unresolved: list[str] = []
    for attendee in record.get("attendees", ()):
        if not isinstance(attendee, Mapping):
            continue
        unit_id, name = attendee.get("unit_id"), attendee.get("print_name")
        member = roster.member_for_unit(str(unit_id)) if unit_id else None
        if member is None and name:
            member = roster.member_for_name(str(name))
        if member is None or member.fireworks_staff_id is None:
            unresolved.append(str(name or unit_id or f"row {attendee.get('row', '?')}"))
            continue
        if member.fireworks_staff_id not in resolved:
            resolved.append(member.fireworks_staff_id)
    return tuple(resolved), tuple(unresolved)


def save_fireworks_request_edit(record: dict[str, Any], text: str,
                                reviewed_at: str) -> tuple[bool, str | None]:
    """Persist valid request JSON or retain invalid text as a recoverable draft."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        record["fireworks_request_draft"] = text
        return False, f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
    if not isinstance(payload, dict):
        record["fireworks_request_draft"] = text
        return False, "Formatted Request must be a JSON object"
    staff = payload.get("staff")
    if (not isinstance(staff, list)
            or any(isinstance(item, bool) or not isinstance(item, int) for item in staff)):
        record["fireworks_request_draft"] = text
        return False, "Formatted Request staff must be an array of numeric Fireworks IDs"
    record["fireworks_request_review"] = {
        "payload": payload,
        "reviewed_at": reviewed_at,
    }
    record.pop("fireworks_request_draft", None)
    return True, None


def displayed_fireworks_request(
        record: Mapping[str, Any], generated: Mapping[str, Any]) -> tuple[str, str]:
    """Return persisted editor text and its source: draft, reviewed, or generated."""
    draft = record.get("fireworks_request_draft")
    if isinstance(draft, str):
        return draft, "draft"
    review = record.get("fireworks_request_review")
    if isinstance(review, Mapping) and isinstance(review.get("payload"), Mapping):
        return json.dumps(review["payload"], indent=2, ensure_ascii=False), "reviewed"
    return json.dumps(dict(generated), indent=2, ensure_ascii=False), "generated"


def formatted_fireworks_request(
        record: Mapping[str, Any], staff_ids: tuple[int, ...] = ()) -> dict[str, Any]:
    """Return the addActivity payload shown during the second human review.

    ``staff_ids`` must contain Fireworks' internal staff identifiers. OCR attendee
    unit IDs are intentionally never copied into that field.
    """
    date_value = _field_value(record, "date")
    description = str(_field_value(record, "description") or "").strip()
    total_hours = _field_value(record, "total_hours")
    if total_hours in (None, ""):
        total_hours = record.get("event", {}).get("total_hours_calculated")

    return {
        "moneln": None,
        "assignTitle": description,
        "startDt": _fireworks_datetime(date_value, _field_value(record, "start_time")),
        "endDt": _fireworks_datetime(date_value, _field_value(record, "end_time")),
        "assignCat": 2000005,
        "assignSubCat": "",
        "location": 3,
        "locationstr": "Fire Station",
        "showInCal": 1,
        "reminder": 0,
        "reminderDays": None,
        "reminderDaysBeforeStart": None,
        "selfTaught": 0,
        "requireVal": 0,
        "digitalSign": 0,
        "typeOfAssignHours": 0,
        "totalHours": None if total_hours in (None, "") else str(total_hours),
        "minHours": None,
        "maxHours": None,
        "publishType": None,
        "assignInst": f"<p>{escape(description)}</p>",
        "allowSelfReg": 0,
        "maxParticipant": 0,
        "orderImp": 0,
        "resourcesmandatory": 0,
        "mutualAid": 0,
        "scormId": None,
        "scormTitle": None,
        "scormLength": None,
        "isScorm": 0,
        "repeat1": None,
        "endRepeatDate": "",
        "sendupdts": 0,
        "repeatmoneav": None,
        "station": None,
        "Shift": None,
        "AppID": None,
        "attendance": None,
        "online": 0,
        "isAllDay": 0,
        "expiredReq": 0,
        "isfacility": 0,
        "pubEdType": None,
        "carSeatManufactureDate": None,
        "carSeatManufacturer": None,
        "carSeatType": None,
        "homeAddress": None,
        "lockingClipUsed": None,
        "nameOfParent": None,
        "notes": None,
        "recalledOrGreaterThan10YearsOld": None,
        "attendanceAdult": None,
        "attendanceSenior": None,
        "attendanceChild": None,
        "ageCarSeatChild": None,
        "locationName": None,
        "batteriesChanged": None,
        "detectorsInstalled": None,
        "CO_Alarms_Installed": None,
        "homeownerName": None,
        "phoneNumber": None,
        "address": None,
        "alertAdmin": 0,
        "alertTo": None,
        "reqGroup": None,
        "reqRank": None,
        "everyX": None,
        "certRepCode": 0,
        "blockAfterEndDt": 0,
        "staff": list(staff_ids),
        "volunteer": [],
        "staffing": [],
        "removeVolunteer": [],
        "removeStaffing": [],
        "EquipmentUsedLive": [],
        "locationFlds": {
            "moneln": 3,
            "desc": "Fire Station",
            "upsize_ts": "AAAAAA79++Q=",
            "FDID": None,
            "archives": None,
            "state": None,
            "street": None,
            "unit": None,
            "cityCode": None,
            "cityDesc": None,
            "zipCode": None,
            "number": None,
            "lat": None,
            "lng": None,
            "locationName": None,
            "oneEventLocation": None,
            "countyID": None,
        },
    }
