"""Resolve applicable rejection rules once, before storing or running an audit."""

import math
import re


def confirmed(mapping):
    return mapping.get("mapping_status") in {"CONFIRMED", "MANUAL"}


def country_matches(value, country):
    def normalize(label):
        key = "".join(char for char in (label or "").casefold() if char.isalnum())
        return "us" if key in {"us", "usa", "unitedstates", "unitedstatesofamerica"} else key

    return bool(value and country) and normalize(str(value)) == normalize(country)


def timer_matches_question(column, question):
    # Accept the export conventions Q1_Timer_Page Submit and PageTimer_Q1.
    timer = r"(?:(?:page|question|screen)[ _.-]*timer|timer|page[ _.-]*submit|seconds[ _.-]*spent)"
    qid = re.escape(question)
    return bool(re.fullmatch(
        rf"(?:{timer}[ _.-]+{qid}|{qid}[ _.-]+{timer}(?:[ _.-]+page[ _.-]*submit)?)", column, re.I
    ))


def resolve_page_rules(rules, mappings, timer_columns):
    if not rules.get("page_timers_enabled", True):
        return {}
    resolved = {}
    manual = rules.get("page_timer_rules") or {}
    global_cutoff = rules.get("page_timer_threshold_seconds")
    action = "REJECT" if rules.get("reject_page_timers") else "REVIEW_REQUIRED"
    for column in timer_columns:
        question_rules = [m for m in mappings if m.get("page_timer_min_seconds")
                          and timer_matches_question(column, m["question_number"])]
        if column in manual:
            threshold, source, scope = manual[column], "Manual configuration", {"column": column}
        elif question_rules:
            if len(question_rules) != 1:
                continue
            mapping = question_rules[0]
            if not confirmed(mapping):
                continue
            threshold = mapping["page_timer_min_seconds"]
            source = "Questionnaire Specification"
            scope = {"column": column, "question_number": mapping["question_number"]}
        elif global_cutoff is not None:
            threshold = global_cutoff
            source = rules.get("page_timer_source") or "Configured page threshold"
            scope = {"column": column, "all_pages": True}
        else:
            continue
        if math.isfinite(threshold) and threshold > 0:
            resolved[column] = dict(threshold=threshold, source=source, units="seconds", scope=scope, action=action)
    return resolved


def termination_values(options):
    values = set()
    for option in options:
        match = re.fullmatch(r"(.+?)\s*\[(?:terminate(?:\s*/\s*screen out)?|screen out|disqualify|ineligible)\]", option.strip(), re.I)
        if not match:
            continue
        label = match[1].strip().casefold()
        code = re.fullmatch(r"(\d+|[a-z])[.)]\s*(.+)", label)
        values.add(label)
        if code:
            values.update((code[1], code[1] + ".", code[2]))
    return sorted(values)


def resolve_eligibility_rules(mappings, country_column, manual, *, enabled=True):
    if not enabled:
        return []
    by_question = {m["question_number"]: m for m in mappings
                   if confirmed(m) and len(m.get("mapped_columns", [])) == 1}
    resolved = []
    manual_by_question = {rule["question_number"]: rule for rule in manual}
    for question, mapping in by_question.items():
        if question in manual_by_question:
            config = manual_by_question[question]
            source = "Manual configuration"
            values = config.get("termination_values", [])
        elif mapping.get("eligibility_source") == "Questionnaire Specification":
            config = mapping
            source = "Questionnaire Specification"
            values = termination_values(mapping.get("options", []))
        else:
            continue
        scope = {"question_number": question, "column": mapping["mapped_columns"][0]}
        if values:
            resolved.append(dict(min_age=None, max_age=None, termination_values=values,
                                 source=source, units="answer", scope=scope.copy(), action="REJECT"))
        countries = config.get("age_countries") or []
        if countries:
            if not country_column:
                # The geographical condition is unresolved, so the age rule stays inactive.
                config = {**config, "min_age": None, "max_age": None}
            else:
                scope.update(country_column=country_column, countries=countries)
        if config.get("min_age") is not None or config.get("max_age") is not None:
            resolved.append(dict(
                min_age=config.get("min_age"), max_age=config.get("max_age"),
                termination_values=[], source=source, units="years", scope=scope, action="REJECT",
            ))
    return resolved
