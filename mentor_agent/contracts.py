from knowledge.schemas import obj, array, enum, TEXT, STRINGS, DOMAIN_TAGS

ROUTE = enum("principle_assisted", "case_only", "insufficient_evidence")
ANALYSIS = obj(query_type=enum("historical", "conceptual", "scenario", "current", "non_investment"),
               scenario_facts=STRINGS, decision_being_asked=TEXT, target_domains=DOMAIN_TAGS,
               missing_information=STRINGS, current_data_required={"type": "boolean"},
               search_terms=array(TEXT, 1), principle_ids=STRINGS)
PAIR = obj(record_ids=array(TEXT, 2), topic=TEXT, shared_conditions=TEXT,
           differing_preferences=TEXT, explanation=TEXT, effect_on_conclusion=TEXT)
ASSESSMENT = obj(selected_route=ROUTE, applicable_principles=STRINGS,
                 rejected_principles=array(obj(principle_id=TEXT, reason=TEXT)),
                 supporting_cases=STRINGS, conflicting_evidence=array(PAIR),
                 context_differences=array(PAIR), exceptions_triggered=array(obj(record_ids=array(TEXT, 1), condition=TEXT, effect=TEXT)),
                 unresolved_conflicts=array(obj(record_ids=array(TEXT, 2), issue=TEXT, missing_information=STRINGS,
                                                 severity=enum("minor", "major"))),
                 missing_information=STRINGS, current_data_required={"type": "boolean"},
                 confidence=enum("low", "medium"), conditional_conclusion=TEXT, route_reasons=STRINGS)
ANSWER = obj(conditional_answer=TEXT,
             historical_evidence=array(obj(statement=TEXT, record_ids=array(TEXT, 1))),
             interpretation=TEXT, conditions_and_risks=STRINGS, conflicts_and_exceptions=STRINGS,
             missing_information=STRINGS, current_data_limitation=TEXT)
