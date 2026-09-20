"""Build and validate a reviewable Fireworks ``addActivity`` request."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
import json
from pathlib import Path
from typing import Any, Mapping


SELECTABLE_CATEGORY_NAMES = (
    "Company Training",
    "Driver/Operator",
    "Officer Training",
    "Outside Department Training",
)

SELECTABLE_LOCATION_NAMES = (
    "Fire Station",
    "Classroom",
    "Outside Area",
)


@dataclass(frozen=True)
class FireworksCategory:
    name: str
    id: int
    status: str


@dataclass(frozen=True)
class FireworksLocation:
    name: str
    id: int
    upsize_ts: str


@dataclass(frozen=True)
class FireworksMappings:
    categories: tuple[FireworksCategory, ...]
    locations: tuple[FireworksLocation, ...]
    station_name: str
    station_id: int

    def category_named(self, name: str | None) -> FireworksCategory | None:
        return next((item for item in self.categories if item.name == name), None)

    def category_with_id(self, value: Any) -> FireworksCategory | None:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return next((item for item in self.categories if item.id == value), None)

    def location_named(self, name: str | None) -> FireworksLocation | None:
        return next((item for item in self.locations if item.name == name), None)

    def location_with_id(self, value: Any) -> FireworksLocation | None:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return next((item for item in self.locations if item.id == value), None)


def _positive_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def load_fireworks_mappings(path: Path) -> FireworksMappings:
    """Load department-specific Fireworks IDs from an external JSON file."""
    source = path.expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise ValueError("unsupported Fireworks mapping file")

    raw_categories = payload.get("categories")
    if not isinstance(raw_categories, list):
        raise ValueError("Fireworks mappings must contain a categories array")
    categories: list[FireworksCategory] = []
    seen_category_names: set[str] = set()
    seen_category_ids: set[int] = set()
    for index, item in enumerate(raw_categories):
        if not isinstance(item, Mapping):
            raise ValueError(f"categories[{index}] must be an object")
        name = str(item.get("name") or "").strip()
        if name not in SELECTABLE_CATEGORY_NAMES:
            continue
        category_id = _positive_integer(item.get("id"), f"categories[{index}].id")
        status = str(item.get("status") or "unknown").strip().casefold()
        if name in seen_category_names or category_id in seen_category_ids:
            raise ValueError(f"duplicate selectable Fireworks category {name or category_id}")
        seen_category_names.add(name)
        seen_category_ids.add(category_id)
        categories.append(FireworksCategory(name, category_id, status))
    missing_categories = set(SELECTABLE_CATEGORY_NAMES) - seen_category_names
    if missing_categories:
        raise ValueError(
            "Fireworks mappings are missing categories: "
            + ", ".join(sorted(missing_categories)))
    categories.sort(key=lambda item: SELECTABLE_CATEGORY_NAMES.index(item.name))

    raw_locations = payload.get("locations")
    if not isinstance(raw_locations, list):
        raise ValueError("Fireworks mappings must contain a locations array")
    locations: list[FireworksLocation] = []
    seen_location_names: set[str] = set()
    seen_location_ids: set[int] = set()
    for index, item in enumerate(raw_locations):
        if not isinstance(item, Mapping):
            raise ValueError(f"locations[{index}] must be an object")
        name = str(item.get("name") or "").strip()
        if name not in SELECTABLE_LOCATION_NAMES:
            continue
        location_id = _positive_integer(item.get("id"), f"locations[{index}].id")
        upsize_ts = str(item.get("upsize_ts") or "").strip()
        if not upsize_ts:
            raise ValueError(f"locations[{index}].upsize_ts is required")
        if name in seen_location_names or location_id in seen_location_ids:
            raise ValueError(f"duplicate selectable Fireworks location {name or location_id}")
        seen_location_names.add(name)
        seen_location_ids.add(location_id)
        locations.append(FireworksLocation(name, location_id, upsize_ts))
    missing_locations = set(SELECTABLE_LOCATION_NAMES) - seen_location_names
    if missing_locations:
        raise ValueError(
            "Fireworks mappings are missing locations: "
            + ", ".join(sorted(missing_locations)))
    locations.sort(key=lambda item: SELECTABLE_LOCATION_NAMES.index(item.name))

    raw_station = payload.get("station")
    if not isinstance(raw_station, Mapping):
        raise ValueError("Fireworks mappings must contain a station object")
    station_name = str(raw_station.get("name") or "").strip()
    station_id = _positive_integer(raw_station.get("id"), "station.id")
    if not station_name:
        raise ValueError("station.name is required")
    return FireworksMappings(
        tuple(categories), tuple(locations), station_name, station_id)


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


def _fireworks_datetimes(
        date_value: Any, start_value: Any, end_value: Any) -> tuple[str | None, str | None]:
    start_text = _fireworks_datetime(date_value, start_value)
    end_text = _fireworks_datetime(date_value, end_value)
    if start_text is None or end_text is None:
        return start_text, end_text
    start = datetime.strptime(start_text, "%Y-%m-%dT%H:%M:%S.000Z")
    end = datetime.strptime(end_text, "%Y-%m-%dT%H:%M:%S.000Z")
    if end < start:
        end += timedelta(days=1)
        end_text = end.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return start_text, end_text


def _event_values(record: Mapping[str, Any], machine_key: str, reviewed_key: str) -> tuple[str, ...]:
    event = record.get("event", {})
    if not isinstance(event, Mapping):
        return ()
    values = event.get(reviewed_key)
    if values is None:
        values = event.get(machine_key)
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(value) for value in values)


def suggested_category_name(record: Mapping[str, Any]) -> str | None:
    """Return one unambiguous category suggestion from reviewed training types."""
    mappings = {
        "company": "Company Training",
        "facilities": "Company Training",
        "haz_mat": "Company Training",
        "recruit": "Company Training",
        "driver": "Driver/Operator",
        "new_driver": "Driver/Operator",
        "officers": "Officer Training",
    }
    candidates = {
        mappings[value] for value in _event_values(
            record, "training_types", "reviewed_training_types")
        if value in mappings
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


def suggested_location_name(record: Mapping[str, Any]) -> str | None:
    """Return one unambiguous Fireworks location from reviewed facility marks."""
    facilities = _event_values(record, "facilities", "reviewed_facilities")
    if not facilities:
        return "Fire Station"
    mappings = {
        "classroom": "Classroom",
        "drill_ground": "Outside Area",
        "outside_area": "Outside Area",
    }
    candidates = {mappings[value] for value in facilities if value in mappings}
    return next(iter(candidates)) if len(candidates) == 1 else None


def selected_category_name(record: Mapping[str, Any]) -> str | None:
    selection = record.get("fireworks_selection", {})
    if isinstance(selection, Mapping) and selection.get("category") is not None:
        return str(selection["category"])
    return suggested_category_name(record)


def selected_location_name(record: Mapping[str, Any]) -> str | None:
    selection = record.get("fireworks_selection", {})
    if isinstance(selection, Mapping) and selection.get("location") is not None:
        return str(selection["location"])
    return suggested_location_name(record)


def fireworks_staff_ids(record: Mapping[str, Any], roster: Any) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """Resolve reviewed attendees and instructor to Fireworks IDs without guessing."""
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
    instructor = _field_value(record, "instructor")
    if instructor not in (None, ""):
        member = roster.member_for_name(str(instructor))
        if member is None or member.fireworks_staff_id is None:
            unresolved.append(f"Instructor: {instructor}")
        elif member.fireworks_staff_id not in resolved:
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
        record: Mapping[str, Any], staff_ids: tuple[int, ...] = (), *,
        category: FireworksCategory | None = None,
        location: FireworksLocation | None = None,
        station_id: int = 54) -> dict[str, Any]:
    """Return the addActivity payload shown during the second human review.

    ``staff_ids`` must contain Fireworks' internal staff identifiers. OCR attendee
    unit IDs are intentionally never copied into that field.
    """
    date_value = _field_value(record, "date")
    description = str(_field_value(record, "description") or "").strip()
    total_hours = _field_value(record, "total_hours")
    if total_hours in (None, ""):
        total_hours = record.get("event", {}).get("total_hours_calculated")
    start_dt, end_dt = _fireworks_datetimes(
        date_value, _field_value(record, "start_time"), _field_value(record, "end_time"))

    location_fields = {
        "moneln": None if location is None else location.id,
        "desc": None if location is None else location.name,
        "upsize_ts": None if location is None else location.upsize_ts,
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
    }

    return {
        "moneln": None,
        "assignTitle": description,
        "startDt": start_dt,
        "endDt": end_dt,
        "assignCat": None if category is None else category.id,
        "assignSubCat": "",
        "location": None if location is None else location.id,
        "locationstr": None if location is None else location.name,
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
        "station": station_id,
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
        "locationFlds": location_fields,
    }


def update_payload_selection(
        payload: dict[str, Any], mappings: FireworksMappings, *,
        category_name: str | None = None,
        location_name: str | None = None) -> dict[str, Any]:
    """Update only dropdown-controlled fields while preserving manual JSON edits."""
    if category_name is not None:
        category = mappings.category_named(category_name)
        if category is None:
            raise ValueError(f"unknown Fireworks category {category_name}")
        payload["assignCat"] = category.id
    if location_name is not None:
        location = mappings.location_named(location_name)
        if location is None:
            raise ValueError(f"unknown Fireworks location {location_name}")
        payload["location"] = location.id
        payload["locationstr"] = location.name
        location_fields = payload.get("locationFlds")
        if not isinstance(location_fields, dict):
            location_fields = {}
            payload["locationFlds"] = location_fields
        location_fields["moneln"] = location.id
        location_fields["desc"] = location.name
        location_fields["upsize_ts"] = location.upsize_ts
    payload["station"] = mappings.station_id
    return payload


def _request_datetime(value: Any, name: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str):
        errors.append(f"{name} is required")
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.000Z")
    except ValueError:
        errors.append(f"{name} must use YYYY-MM-DDTHH:MM:SS.000Z")
        return None


def validate_fireworks_payload(
        payload: Mapping[str, Any], mappings: FireworksMappings,
        unresolved_staff: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Return all reasons the visible request is unsafe to submit."""
    errors: list[str] = []
    if payload.get("moneln") is not None:
        errors.append("moneln must be null when creating a new activity")
    if not str(payload.get("assignTitle") or "").strip():
        errors.append("assignTitle is required")

    start = _request_datetime(payload.get("startDt"), "startDt", errors)
    end = _request_datetime(payload.get("endDt"), "endDt", errors)
    if start is not None and end is not None and end <= start:
        errors.append("endDt must be later than startDt")

    category = mappings.category_with_id(payload.get("assignCat"))
    if category is None:
        errors.append("assignCat must be one of the configured Fireworks categories")

    location = mappings.location_with_id(payload.get("location"))
    if location is None:
        errors.append("location must be one configured Fireworks location")
    else:
        if payload.get("locationstr") != location.name:
            errors.append("locationstr does not match location")
        fields = payload.get("locationFlds")
        if not isinstance(fields, Mapping):
            errors.append("locationFlds is required")
        elif (fields.get("moneln") != location.id
              or fields.get("desc") != location.name
              or fields.get("upsize_ts") != location.upsize_ts):
            errors.append("locationFlds does not match location")

    if payload.get("station") != mappings.station_id:
        errors.append(
            f"station must be {mappings.station_id} ({mappings.station_name})")

    try:
        total_hours = Decimal(str(payload.get("totalHours")))
        if not total_hours.is_finite() or total_hours <= 0:
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        errors.append("totalHours must be a positive number")

    staff = payload.get("staff")
    if not isinstance(staff, list) or not staff:
        errors.append("staff must contain at least one Fireworks Staff ID")
    elif any(isinstance(item, bool) or not isinstance(item, int) or item <= 0
             for item in staff):
        errors.append("staff must contain only positive numeric Fireworks Staff IDs")
    elif len(staff) != len(set(staff)):
        errors.append("staff contains duplicate Fireworks Staff IDs")

    if unresolved_staff:
        errors.append("unresolved Fireworks Staff ID: " + ", ".join(unresolved_staff))
    return tuple(errors)


def fireworks_payload_hash(payload: Mapping[str, Any]) -> str:
    """Return a stable digest for duplicate protection and audit receipts."""
    import hashlib
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
