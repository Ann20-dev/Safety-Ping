from __future__ import annotations

from typing import Any, Callable

USSD_MENU = (
    "CON SafetyPing\n"
    "1. Check in\n"
    "2. Report incident\n"
    "3. Daily briefing\n"
    "4. Check out\n"
    "5. Emergency SOS"
)

INCIDENT_TYPE_PROMPT = (
    "CON Incident type\n"
    "1. Injury\n"
    "2. Near miss\n"
    "3. Unsafe equipment\n"
    "4. Harassment\n"
    "5. Other"
)

SEVERITY_PROMPT = (
    "CON Severity\n"
    "1. Low\n"
    "2. Medium\n"
    "3. High\n"
    "4. Critical"
)

CATEGORIES = {
    "1": "Injury",
    "2": "Near miss",
    "3": "Unsafe equipment",
    "4": "Harassment",
    "5": "Other",
}

SEVERITIES = {
    "1": "low",
    "2": "medium",
    "3": "high",
    "4": "critical",
}


def build_ussd_response(
    text: str,
    worker: dict[str, Any],
    briefings: dict[str, str],
    record_checkin: Callable[[str], Any],
    create_incident: Callable[[str, str, str], Any],
    emergency_alert: Callable[[], Any],
) -> str:
    parts = [part for part in text.split("*") if part]

    if not parts:
        return USSD_MENU

    if parts[0] == "1":
        record_checkin("check_in")
        return f"END Thanks {worker['name']}. Your check-in has been recorded."

    if parts[0] == "4":
        record_checkin("check_out")
        return f"END Thanks {worker['name']}. Your check-out has been recorded."

    if parts[0] == "3":
        return f"END {briefings.get(worker.get('language'), briefings.get('en', ''))}"

    if parts[0] == "2":
        if len(parts) == 1:
            return INCIDENT_TYPE_PROMPT

        if len(parts) == 2:
            return SEVERITY_PROMPT

        if len(parts) == 3:
            return "CON Briefly describe what happened"

        if len(parts) >= 4:
            create_incident(
                CATEGORIES.get(parts[1], "Other"),
                SEVERITIES.get(parts[2], "medium"),
                " ".join(parts[3:])[:240],
            )
            return "END Incident received. Your supervisor has been alerted."

    if parts[0] == "5":
        emergency_alert()
        return "END Emergency alert sent. Help is on the way."

    return "END Invalid option. Please try again."
