from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from modules.common import ROOT, fmt_ts, read_json, read_yaml


@dataclass
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
        data: dict[str, str] = {
            "title": self.title,
            "topic_thread_id": self.topic_thread_id,
        }
        for key in [
            "claims",
            "observations",
            "reasoning_steps",
            "assumptions",
            "conditions",
            "risks",
            "exceptions",
            "uncertainty",
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
        evidence_quotes = []
        for ev in self.fields.get("source_evidence", []) or []:
            if isinstance(ev, dict) and ev.get("quote"):
                evidence_quotes.append(str(ev["quote"]))
        data["source_evidence"] = "\n".join(evidence_quotes)
        return data

    def searchable_text(self) -> str:
        return "\n".join(self.field_texts().values())

    def citations(self) -> list[EvidenceCitation]:
        citations = []
        for ev in self.fields.get("source_evidence", []) or []:
            if not isinstance(ev, dict):
                continue
            citations.append(
                EvidenceCitation(
                    episode_id=self.episode_id,
                    record_id=self.record_id,
                    start=ev.get("start"),
                    end=ev.get("end"),
                )
            )
        return citations


class KnowledgeBase:
    def __init__(self, config_path: Path = ROOT / "config" / "data.yaml"):
        self.config = read_yaml(config_path)
        self.records = self._load_records()

    def _load_records(self) -> list[ReasoningRecord]:
        records: list[ReasoningRecord] = []
        for episode_id, paths in (self.config.get("episodes") or {}).items():
            level2 = read_json(ROOT / paths["level2"])
            for item in level2.get("reasoning_records", []):
                record_id = item.get("reasoning_record_id")
                if not record_id:
                    continue
                records.append(
                    ReasoningRecord(
                        episode_id=episode_id,
                        record_id=record_id,
                        topic_thread_id=item.get("topic_thread_id", ""),
                        title=item.get("title", ""),
                        fields=item,
                    )
                )
        return records

    def get_record(self, record_id: str) -> ReasoningRecord | None:
        for record in self.records:
            if record.record_id == record_id:
                return record
        return None
