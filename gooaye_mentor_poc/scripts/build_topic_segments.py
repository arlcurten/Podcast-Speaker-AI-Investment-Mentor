#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

from common import CONFIG, DATA, REPORTS, path_for_report, write_json, write_jsonl


MARKET_TYPES = {"market_analysis", "industry_company", "macro", "portfolio_positioning", "qa_investment"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword and keyword.lower() in text.lower()]


def keyword_score(hits: list[str], cap: int) -> float:
    return clamp(len(hits) / max(1, cap))


def classify_text(text: str, config: dict[str, Any]) -> dict[str, Any]:
    cap = int(config["classification"].get("keyword_score_cap", 12))
    content_keywords = config.get("content_keywords", {})
    scores: dict[str, float] = {}
    evidence: dict[str, list[str]] = {}
    for label, keywords in content_keywords.items():
        hits = keyword_hits(text, list(keywords or []))
        scores[label] = keyword_score(hits, cap)
        if hits:
            evidence[label] = hits[:12]
    if scores:
        content_type = max(scores, key=lambda label: (scores[label], label))
        if scores[content_type] == 0:
            content_type = "other"
    else:
        content_type = "other"
    reasoning_hits = keyword_hits(text, list(config.get("reasoning_keywords", [])))
    behavior_hits = keyword_hits(text, list(config.get("behavior_keywords", [])))
    market_hits: list[str] = []
    for label in MARKET_TYPES:
        market_hits.extend(evidence.get(label, []))
    market_relevance = max(max([scores.get(label, 0.0) for label in MARKET_TYPES] or [0.0]), keyword_score(sorted(set(market_hits)), cap))
    investment_reasoning_value = clamp(keyword_score(reasoning_hits, cap) * 0.75 + market_relevance * 0.35)
    behavioral_style_value = clamp(keyword_score(behavior_hits, cap) * 0.8 + scores.get("personal_chat", 0.0) * 0.2)
    strongest = max(scores.values()) if scores else 0.0
    confidence_base = float(config["classification"].get("confidence_base", 0.45))
    confidence_weight = float(config["classification"].get("confidence_keyword_weight", 0.05))
    classification_confidence = clamp(confidence_base + confidence_weight * len(set(sum(evidence.values(), []))) + strongest * 0.25)
    return {
        "content_type": content_type,
        "content_type_scores": {label: round(value, 4) for label, value in scores.items() if value > 0},
        "market_relevance": round(market_relevance, 4),
        "investment_reasoning_value": round(investment_reasoning_value, 4),
        "behavioral_style_value": round(behavioral_style_value, 4),
        "classification_confidence": round(classification_confidence, 4),
        "classification_method": "deterministic_keyword_scores",
        "classification_version": "topic-routing-v1",
        "evidence": {
            "content_keywords": evidence,
            "market_keywords": sorted(set(market_hits))[:16],
            "reasoning_keywords": reasoning_hits[:12],
            "behavior_keywords": behavior_hits[:12],
        },
    }


def should_start_new_topic(current: dict[str, Any], item: dict[str, Any], min_duration: float, max_duration: float) -> bool:
    if not current["items"]:
        return False
    current_duration = float(item["end"]) - float(current["start"])
    if current_duration > max_duration:
        return True
    current_type = current["last_content_type"]
    next_type = item["classification"]["content_type"]
    if next_type != current_type and current_duration >= min_duration:
        return True
    return False


def make_topic(topic_index: int, items: list[dict[str, Any]], config: dict[str, Any], episode_id: str) -> dict[str, Any]:
    text = " ".join(str(item["normalized_text_zh_tw"]).strip() for item in items if str(item["normalized_text_zh_tw"]).strip())
    source_merged_ids = [int(item["merged_id"]) for item in items]
    source_segment_ids: list[int] = []
    for item in items:
        source_segment_ids.extend(int(value) for value in item.get("source_segment_ids", []))
    classification = classify_text(text, config)
    confidences = [float(item["classification"]["classification_confidence"]) for item in items]
    segmentation_confidence = statistics.fmean(confidences) if confidences else classification["classification_confidence"]
    return {
        "topic_segment_id": f"{episode_id}-topic-{topic_index:03d}",
        "episode_id": episode_id,
        "start": float(items[0]["start"]),
        "end": float(items[-1]["end"]),
        "duration_seconds": round(float(items[-1]["end"]) - float(items[0]["start"]), 3),
        "text": text,
        "text_sha256": text_hash(text),
        "source_merged_ids": source_merged_ids,
        "source_segment_ids": source_segment_ids,
        "source_merged_count": len(source_merged_ids),
        "source_segment_count": len(source_segment_ids),
        "segmentation_method": "deterministic_keyword_continuity_v1",
        "segmentation_confidence": round(float(segmentation_confidence), 4),
        "classification": classification,
    }


def build_topics(merged_rows: list[dict[str, Any]], config: dict[str, Any], episode_id: str) -> list[dict[str, Any]]:
    classified_rows = []
    for row in merged_rows:
        text = str(row.get("normalized_text_zh_tw") or row.get("raw_text") or "")
        item = dict(row)
        item["classification"] = classify_text(text, config)
        classified_rows.append(item)

    topics: list[dict[str, Any]] = []
    current = {"items": [], "start": None, "last_content_type": None}
    max_duration = float(config["segmentation"].get("max_topic_duration_seconds", 240))
    min_duration = float(config["segmentation"].get("min_topic_duration_seconds", 75))
    for item in classified_rows:
        if current["items"] and should_start_new_topic(current, item, min_duration, max_duration):
            topics.append(make_topic(len(topics), current["items"], config, episode_id))
            current = {"items": [], "start": None, "last_content_type": None}
        if not current["items"]:
            current["start"] = float(item["start"])
        current["items"].append(item)
        current["last_content_type"] = item["classification"]["content_type"]
    if current["items"]:
        topics.append(make_topic(len(topics), current["items"], config, episode_id))
    return topics


def near_threshold(value: float, thresholds: list[float], margin: float) -> bool:
    return any(abs(value - threshold) <= margin for threshold in thresholds)


def route_topic(topic: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    routing = config["routing"]
    classification = topic["classification"]
    market = float(classification["market_relevance"])
    reasoning = float(classification["investment_reasoning_value"])
    behavior = float(classification["behavioral_style_value"])
    confidence = float(classification["classification_confidence"])
    routes: list[str] = []
    reasons: list[str] = []
    high = float(routing["market_relevance_high"])
    mid = float(routing["market_relevance_mid"])
    if market >= high:
        routes.append("investment_knowledge")
        reasons.append(f"market_relevance >= {high}")
    elif market >= mid:
        routes.extend(["review_needed", "general_archive"])
        reasons.append(f"market_relevance between {mid} and {high}")
    else:
        routes.append("general_archive")
        reasons.append(f"market_relevance < {mid}")
    reasoning_threshold = float(routing["investment_reasoning_threshold"])
    behavior_threshold = float(routing["behavioral_style_threshold"])
    if reasoning >= reasoning_threshold:
        routes.append("decision_reasoning")
        reasons.append(f"investment_reasoning_value >= {reasoning_threshold}")
    if behavior >= behavior_threshold:
        routes.append("speaker_behavior")
        reasons.append(f"behavioral_style_value >= {behavior_threshold}")
    low_confidence = float(routing["low_confidence_threshold"])
    margin = float(routing["near_threshold_margin"])
    near_any_threshold = (
        near_threshold(market, [mid, high], margin)
        or near_threshold(reasoning, [reasoning_threshold], margin)
        or near_threshold(behavior, [behavior_threshold], margin)
    )
    if confidence < low_confidence:
        routes.append("review_needed")
        reasons.append(f"classification_confidence < {low_confidence}")
    if near_any_threshold:
        routes.append("review_needed")
        reasons.append(f"score within {margin} of routing threshold")
    return {
        "topic_segment_id": topic["topic_segment_id"],
        "episode_id": topic["episode_id"],
        "routes": sorted(set(routes)),
        "routing_reasons": reasons,
        "routing_method": "deterministic_threshold_policy",
        "routing_version": "topic-routing-v1",
        "thresholds": routing,
        "near_threshold": near_any_threshold,
    }


def duration_by(items: list[dict[str, Any]], key_fn: Any) -> dict[str, float]:
    totals: defaultdict[str, float] = defaultdict(float)
    for item in items:
        duration = float(item["end"]) - float(item["start"])
        keys = key_fn(item)
        if isinstance(keys, str):
            keys = [keys]
        for key in keys:
            totals[str(key)] += duration
    return {key: round(value, 3) for key, value in sorted(totals.items())}


def possible_mapping_errors(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors = []
    previous_end = -math.inf
    for topic in topics:
        if float(topic["start"]) < previous_end:
            errors.append({"topic_segment_id": topic["topic_segment_id"], "type": "non_monotonic_topic_time"})
        if not topic["source_merged_ids"] or not topic["source_segment_ids"]:
            errors.append({"topic_segment_id": topic["topic_segment_id"], "type": "missing_source_mapping"})
        previous_end = float(topic["end"])
    return errors


def write_review_report(path: Path, topics: list[dict[str, Any]], routes: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    route_by_id = {row["topic_segment_id"]: row for row in routes}
    lines = [
        "# EP674 Topic Segmentation Review",
        "",
        "This is a deterministic POC output for human review. It is not a production index and does not assert transcript quality.",
        "",
        "## Summary",
        "",
        f"- Topic segments: {len(topics)}",
        f"- Total covered duration: {summary['total_topic_duration_seconds']:.3f} seconds",
        f"- Low-confidence segments: {len(summary['low_confidence_topic_segment_ids'])}",
        f"- Near-threshold segments: {len(summary['near_threshold_topic_segment_ids'])}",
        f"- Possible mapping errors: {len(summary['possible_mapping_errors'])}",
        "",
        "### Duration By Content Type",
        "",
    ]
    for label, duration in summary["duration_by_content_type"].items():
        lines.append(f"- {label}: {duration:.3f}s")
    lines.extend(["", "### Duration By Route", ""])
    for label, duration in summary["duration_by_route"].items():
        lines.append(f"- {label}: {duration:.3f}s")
    lines.extend(["", "## Segment Review Table", ""])
    lines.append("| ID | Time | Duration | Content type | Market | Reasoning | Behavior | Confidence | Routes | Evidence | Text excerpt |")
    lines.append("|---|---:|---:|---|---:|---:|---:|---:|---|---|---|")
    for topic in topics:
        cls = topic["classification"]
        route = route_by_id[topic["topic_segment_id"]]
        evidence = []
        for hits in cls["evidence"].get("content_keywords", {}).values():
            evidence.extend(hits)
        evidence.extend(cls["evidence"].get("reasoning_keywords", []))
        evidence.extend(cls["evidence"].get("behavior_keywords", []))
        excerpt = topic["text"].replace("|", "\\|")[:180]
        lines.append(
            f"| {topic['topic_segment_id']} | {topic['start']:.2f}-{topic['end']:.2f} | "
            f"{topic['duration_seconds']:.1f}s | {cls['content_type']} | {cls['market_relevance']:.2f} | "
            f"{cls['investment_reasoning_value']:.2f} | {cls['behavioral_style_value']:.2f} | "
            f"{cls['classification_confidence']:.2f} | {', '.join(route['routes'])} | "
            f"{', '.join(sorted(set(evidence))[:8]).replace('|', '\\|')} | {excerpt} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", default="EP674")
    parser.add_argument("--configuration", default="large-v3-turbo")
    parser.add_argument("--config", type=Path, default=CONFIG / "topic_routing.yaml")
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    base = DATA / "transcripts" / args.episode / args.configuration
    normalized_path = base / "normalized_merged_transcript_zh_tw.json"
    if not normalized_path.exists():
        raise SystemExit(f"Normalized merged transcript not found: {normalized_path}")
    normalized_payload = load_json(normalized_path)
    topics = build_topics(normalized_payload["segments"], config, args.episode)
    routes = [route_topic(topic, config) for topic in topics]
    classification_rows = [
        {
            "topic_segment_id": topic["topic_segment_id"],
            "episode_id": topic["episode_id"],
            **topic["classification"],
        }
        for topic in topics
    ]
    route_by_id = {route["topic_segment_id"]: route for route in routes}
    low_confidence = float(config["segmentation"].get("low_confidence_threshold", 0.55))
    summary = {
        "episode_id": args.episode,
        "configuration": args.configuration,
        "source_normalized_merged_transcript": path_for_report(normalized_path),
        "topic_segment_count": len(topics),
        "total_topic_duration_seconds": round(sum(float(topic["duration_seconds"]) for topic in topics), 3),
        "duration_by_content_type": duration_by(topics, lambda item: item["classification"]["content_type"]),
        "duration_by_route": duration_by(topics, lambda item: route_by_id[item["topic_segment_id"]]["routes"]),
        "low_confidence_topic_segment_ids": [
            topic["topic_segment_id"]
            for topic in topics
            if float(topic["classification"]["classification_confidence"]) < low_confidence
        ],
        "near_threshold_topic_segment_ids": [route["topic_segment_id"] for route in routes if route["near_threshold"]],
        "possible_mapping_errors": possible_mapping_errors(topics),
        "segmentation_method": "deterministic_keyword_continuity_v1",
        "classification_method": "deterministic_keyword_scores",
        "routing_method": "deterministic_threshold_policy",
        "config_path": path_for_report(args.config),
    }
    out_dir = DATA / "topic_segments" / args.episode
    write_json(out_dir / "topic_segments.json", {"summary": summary, "topic_segments": topics})
    write_jsonl(out_dir / "topic_segments.jsonl", topics)
    write_jsonl(out_dir / "classification.jsonl", classification_rows)
    write_jsonl(out_dir / "routing.jsonl", routes)
    write_json(DATA / "evaluation" / f"{args.episode}_topic_segmentation_summary.json", summary)
    write_review_report(REPORTS / f"{args.episode}_topic_segmentation_review.md", topics, routes, summary)
    print(json.dumps({
        "topic_segments": len(topics),
        "outputs": {
            "topic_segments": path_for_report(out_dir / "topic_segments.json"),
            "classification": path_for_report(out_dir / "classification.jsonl"),
            "routing": path_for_report(out_dir / "routing.jsonl"),
            "review_report": path_for_report(REPORTS / f"{args.episode}_topic_segmentation_review.md"),
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
