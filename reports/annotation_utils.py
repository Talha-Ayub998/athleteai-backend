from collections import defaultdict
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
from django.utils import timezone

from reports.models import AnnotationEvent


def normalize_errors(raw_errors):
    if raw_errors is None:
        return []
    if isinstance(raw_errors, list):
        return [str(err) for err in raw_errors]
    return [str(raw_errors)]


def normalize_athlete_profile(user, payload):
    payload = payload or {}
    fallback_name = (getattr(user, "username", "") or "").strip() or user.email.split("@")[0]
    full_name = (getattr(user, "get_full_name", lambda: "")() or "").strip()
    return {
        "name": str(payload.get("name") or full_name or fallback_name).strip(),
        "email": str(payload.get("email") or user.email).strip(),
        "belt": str(payload.get("belt") or "Unknown").strip(),
        "gym": str(payload.get("gym") or "Unknown").strip(),
        "language": str(payload.get("language") or "English").strip(),
    }


def build_dated_filename(base_name: str, now_dt):
    stem = Path(base_name).stem.strip() or "Report"
    date_str = now_dt.strftime("%Y-%m-%d")
    return f"{stem}_{date_str}.xlsx"


def aggregate_stats_rows(events):
    counters = defaultdict(
        lambda: {
            "offense_attempted": 0,
            "offense_succeeded": 0,
            "defense_attempted": 0,
            "defense_succeeded": 0,
        }
    )
    match_numbers = set()

    for event in events:
        if event.event_type == AnnotationEvent.EVENT_NOTE:
            continue
        if event.player not in {AnnotationEvent.PLAYER_ME, AnnotationEvent.PLAYER_OPPONENT}:
            continue
        if not event.move_name:
            continue
        if event.outcome not in {AnnotationEvent.OUTCOME_SUCCESS, AnnotationEvent.OUTCOME_FAILED}:
            continue

        move_name = str(event.move_name).strip()
        key = (event.match_number, move_name)
        match_numbers.add(event.match_number)

        if event.player == AnnotationEvent.PLAYER_ME:
            counters[key]["offense_attempted"] += 1
            if event.outcome == AnnotationEvent.OUTCOME_SUCCESS:
                counters[key]["offense_succeeded"] += 1
        else:
            counters[key]["defense_attempted"] += 1
            if event.outcome == AnnotationEvent.OUTCOME_FAILED:
                counters[key]["defense_succeeded"] += 1

    return counters, sorted(match_numbers)


def build_workbook_bytes(athlete_profile, stats_counters, match_numbers, match_results_map):
    athlete_df = pd.DataFrame(
        [
            {
                "Name": athlete_profile["name"],
                "Email": athlete_profile["email"],
                "Belt": athlete_profile["belt"],
                "Gym": athlete_profile["gym"],
                "Language": athlete_profile["language"],
            }
        ]
    )
    input_validation_df = pd.DataFrame(
        [
            {
                "GeneratedBy": "annotation_session_api",
                "GeneratedAtUTC": datetime.utcnow().isoformat(timespec="seconds"),
            }
        ]
    )

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        input_validation_df.to_excel(writer, sheet_name="InputValidation", index=False)
        athlete_df.to_excel(writer, sheet_name="Athlete", index=False)

        for match_number in match_numbers:
            match_label = f"Match-{match_number}"
            stats_rows = []

            for (m_num, move_name), values in sorted(stats_counters.items(), key=lambda x: (x[0][0], x[0][1].lower())):
                if m_num != match_number:
                    continue
                stats_rows.append(
                    {
                        "move_name": move_name,
                        "offense_attempted": int(values["offense_attempted"]),
                        "offense_succeeded": int(values["offense_succeeded"]),
                        "defense_attempted": int(values["defense_attempted"]),
                        "defense_succeeded": int(values["defense_succeeded"]),
                        "match": match_label,
                    }
                )

            stats_df = pd.DataFrame(
                stats_rows,
                columns=[
                    "move_name",
                    "offense_attempted",
                    "offense_succeeded",
                    "defense_attempted",
                    "defense_succeeded",
                    "match",
                ],
            )
            stats_df.to_excel(writer, sheet_name=f"{match_label} Stats", index=False)

            result = match_results_map[match_number]
            result_df = pd.DataFrame(
                [
                    {
                        "Result": result.result,
                        "Match Type": result.match_type,
                        "Referee Decision": "Yes" if result.referee_decision else "No",
                        "Disqualified?": "Yes" if result.disqualified else "No",
                        "Opponent": result.opponent,
                    }
                ]
            )
            result_df.to_excel(writer, sheet_name=f"{match_label} Result", index=False)

    output.seek(0)
    return output.getvalue()


def missing_previous_match_results(session, target_match_number):
    if target_match_number <= 1:
        return []
    required_previous_matches = set(range(1, target_match_number))
    existing_results = set(
        session.match_results.filter(match_number__lt=target_match_number, match_number__gte=1)
        .values_list("match_number", flat=True)
        .distinct()
    )
    return sorted(required_previous_matches - existing_results)
