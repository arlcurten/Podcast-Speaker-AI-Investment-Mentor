from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import fmt_ts, read_json, read_yaml, resolve_path


@dataclass(frozen=True)
class EvidenceCitation:
    episode_id: str
    record_id: str
    start: float | None
    end: float | None

    def label(self) -> str:
        return f"{self.episode_id}, {fmt_ts(self.start)}-{fmt_ts(self.end)}, record {self.record_id}"


@dataclass
class ReasoningRecord:
    episode_id: str
    record_id: str
    topic_thread_id: str
    title: str
    fields: dict[str, Any]

    def field_texts(self) -> dict[str, str]:
        data: dict[str, str] = {"title": self.title, "topic_thread_id": self.topic_thread_id}
        for key in [
            "claims",
            "observations",
            "reasoning_steps",
            "assumptions",
            "conditions",
            "risks",
            "exceptions",
            "uncertainty",
            "contradictions",
            "decisions_or_preferences",
            "actions_or_avoided_actions",
            "view_change_conditions",
            "speaker_behavior",
            "teaching_or_explanation_style",
            "tags",
        ]:
            value = self.fields.get(key)
            if isinstance(value, list):
                data[key] = "\n".join(str(x) for x in value)
            elif value:
                data[key] = str(value)
        quotes = []
        for ev in self.fields.get("source_evidence", []) or []:
            if isinstance(ev, dict) and ev.get("quote"):
                quotes.append(str(ev["quote"]))
        data["source_evidence"] = "\n".join(quotes)
        return data

    def searchable_text(self) -> str:
        return "\n".join(self.field_texts().values())

    def citations(self) -> list[EvidenceCitation]:
        citations = []
        for ev in self.fields.get("source_evidence", []) or []:
            if not isinstance(ev, dict):
                continue
            citations.append(EvidenceCitation(self.episode_id, self.record_id, ev.get("start"), ev.get("end")))
        return citations


@dataclass
class Principle:
    principle_id: str
    name: str
    fields: dict[str, Any]

    @property
    def runtime_mode(self) -> str:
        return str(self.fields.get("runtime_mode_recommendation") or "")

    @property
    def genericness_status(self) -> str:
        return str(self.fields.get("genericness_status") or "")

    @property
    def supporting_records(self) -> list[str]:
        return [str(x) for x in self.fields.get("supporting_records", []) if x]

    def searchable_text(self) -> str:
        f = self.fields
        parts: list[str] = [
            self.name,
            str(f.get("decision_context") or ""),
            str(f.get("core_preference") or ""),
            _join(f.get("reasoning_steps")),
            _join(f.get("preferred_actions")),
            _join(f.get("conditions")),
            _join(f.get("exceptions")),
            _join(f.get("contradictions")),
            _join(f.get("distinctive_behavior")),
            _join(f.get("domains")),
        ]
        tradeoff = f.get("decision_tradeoff") or {}
        if isinstance(tradeoff, dict):
            parts.extend(str(tradeoff.get(k) or "") for k in ["option_a", "option_b", "preferred_option", "reason_for_preference", "cost_of_preference"])
            parts.append(_join(tradeoff.get("switch_conditions")))
        return "\n".join(x for x in parts if x)


class CaseStore:
    def __init__(self, config_path: Path):
        self.config = read_yaml(config_path)
        self.records = self._load_records()
        self.by_id = {record.record_id: record for record in self.records}

    def _load_records(self) -> list[ReasoningRecord]:
        records: list[ReasoningRecord] = []
        for episode_id, paths in (self.config.get("episodes") or {}).items():
            level2 = read_json(resolve_path(paths["level2"]))
            for item in level2.get("reasoning_records", []):
                record_id = item.get("reasoning_record_id")
                if not record_id:
                    continue
                records.append(
                    ReasoningRecord(
                        episode_id=episode_id,
                        record_id=str(record_id),
                        topic_thread_id=str(item.get("topic_thread_id") or ""),
                        title=str(item.get("title") or ""),
                        fields=item,
                    )
                )
        return records

    def get(self, record_id: str) -> ReasoningRecord | None:
        return self.by_id.get(record_id)

    def validate(self) -> dict[str, Any]:
        errors: list[str] = []
        seen: set[str] = set()
        for record in self.records:
            if record.record_id in seen:
                errors.append(f"duplicate record_id: {record.record_id}")
            seen.add(record.record_id)
            if not record.episode_id or not record.citations():
                errors.append(f"record missing citation evidence: {record.record_id}")
        return {"ok": not errors, "record_count": len(self.records), "errors": errors}


class PrincipleStore:
    def __init__(self, config_path: Path):
        self.config = read_yaml(config_path)
        self.principles = self._load_principles()
        self.by_id = {principle.principle_id: principle for principle in self.principles}

    def _load_principles(self) -> list[Principle]:
        path = resolve_path(self.config["principles"]["level4"])
        data = read_json(path)
        principles: list[Principle] = []
        for item in data.get("principles", []):
            principle_id = item.get("principle_id")
            if principle_id:
                principles.append(Principle(str(principle_id), str(item.get("name") or ""), item))
        return principles

    def eligible(self) -> list[Principle]:
        routing = self.config.get("routing") or {}
        allowed_modes = set(routing.get("allowed_principle_runtime_modes") or ["principle_assisted"])
        disallowed_genericness = set(routing.get("disallowed_genericness") or ["too_generic"])
        eligible: list[Principle] = []
        for principle in self.principles:
            if principle.runtime_mode not in allowed_modes:
                continue
            if principle.genericness_status in disallowed_genericness:
                continue
            if not principle.supporting_records:
                continue
            eligible.append(principle)
        return eligible

    def validate(self, case_store: CaseStore) -> dict[str, Any]:
        errors: list[str] = []
        for principle in self.principles:
            if not principle.principle_id:
                errors.append("principle missing ID")
            for record_id in principle.supporting_records:
                if not case_store.get(record_id):
                    errors.append(f"{principle.principle_id} references missing record {record_id}")
            if principle.runtime_mode == "principle_guided":
                errors.append(f"{principle.principle_id} uses unsupported runtime mode principle_guided")
        return {
            "ok": not errors,
            "principle_count": len(self.principles),
            "eligible_principle_count": len(self.eligible()),
            "errors": errors,
        }


def _join(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(x) for x in value)
    return str(value or "")

