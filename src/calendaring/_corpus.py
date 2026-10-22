"""Objects as real calendars hold them, for the conformance suite's round trip (mode-free).

Each one carries what a backend is tempted to lose: time zones and their
VTIMEZONE, floating times, dates, alarms, a recurrence set with EXDATE,
RDATE and an override, escaped, multi-line and non-ASCII text, properties
with several values, and X- properties with parameters on every component
type.  A backend may lose only what its declaration says it does.
"""

from __future__ import annotations

__all__ = ["CORPUS"]


def _crlf(text: str) -> bytes:
    return text.replace("{OSLO}", OSLO.strip()).strip().replace("\n", "\r\n").encode() + b"\r\n"


OSLO = """
BEGIN:VTIMEZONE
TZID:Europe/Oslo
BEGIN:STANDARD
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
END:DAYLIGHT
END:VTIMEZONE
"""

CORPUS: dict[str, bytes] = {
    "event-series-in-a-time-zone": _crlf(r"""
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//calendaring//conformance corpus//EN
{OSLO}
BEGIN:VEVENT
UID:corpus-series@conformance.invalid
DTSTAMP:20261001T000000Z
DTSTART;TZID=Europe/Oslo:20261102T090000
DTEND;TZID=Europe/Oslo:20261102T100000
RRULE:FREQ=WEEKLY;COUNT=4
EXDATE;TZID=Europe/Oslo:20261109T090000
SUMMARY:Møte på kontoret
DESCRIPTION:Agenda:\n1. Budsjett\; tall\, og «sitater»\n2. Kaffe ☕
LOCATION:Rom 3\, 2. etasje
URL:https://meet.example.com/weekly
CATEGORIES:work,planning
ATTENDEE;CN=Åse;PARTSTAT=ACCEPTED:mailto:ase@example.com
ATTENDEE;CN=Bob:mailto:bob@example.com
X-CONFORMANCE-COLOUR;X-SHADE=dark:teal
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Møtet begynner snart
TRIGGER:-PT15M
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:corpus-series@conformance.invalid
DTSTAMP:20261001T000000Z
RECURRENCE-ID;TZID=Europe/Oslo:20261116T090000
DTSTART;TZID=Europe/Oslo:20261116T130000
DTEND;TZID=Europe/Oslo:20261116T140000
SUMMARY:Møte på kontoret (ettermiddag)
END:VEVENT
END:VCALENDAR
"""),
    "all-day-event-with-rdate": _crlf(r"""
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//calendaring//conformance corpus//EN
BEGIN:VEVENT
UID:corpus-all-day@conformance.invalid
DTSTAMP:20261001T000000Z
DTSTART;VALUE=DATE:20261105
DTEND;VALUE=DATE:20261106
RDATE;VALUE=DATE:20261112,20261119
SUMMARY:Heldagsseminar
END:VEVENT
END:VCALENDAR
"""),
    "floating-event": _crlf(r"""
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//calendaring//conformance corpus//EN
BEGIN:VEVENT
UID:corpus-floating@conformance.invalid
DTSTAMP:20261001T000000Z
DTSTART:20261103T070000
DTEND:20261103T073000
SUMMARY:Morning run, wherever I am
END:VEVENT
END:VCALENDAR
"""),
    "task-due-on-a-date": _crlf(r"""
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//calendaring//conformance corpus//EN
BEGIN:VTODO
UID:corpus-task@conformance.invalid
DTSTAMP:20261001T000000Z
DUE;VALUE=DATE:20261120
SUMMARY:Søknad\, del 2
DESCRIPTION:Første linje\nAndre linje\n\nEtter en tom linje
CATEGORIES:grant,writing
CATEGORIES:urgent
RELATED-TO;RELTYPE=PARENT:corpus-parent@conformance.invalid
X-CONFORMANCE-TASK;X-ORIGIN=corpus:kept
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Fristen er i morgen
TRIGGER;RELATED=END:-P1D
END:VALARM
END:VTODO
END:VCALENDAR
"""),
    "journal-entry": _crlf(r"""
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//calendaring//conformance corpus//EN
BEGIN:VJOURNAL
UID:corpus-journal@conformance.invalid
DTSTAMP:20261001T000000Z
DTSTART;VALUE=DATE:20261102
SUMMARY:Dagbok
DESCRIPTION:Det regnet.\nVi drakk kakao – og spiste vafler.
X-CONFORMANCE-JOURNAL;X-MOOD=calm:kept
END:VJOURNAL
END:VCALENDAR
"""),
}
