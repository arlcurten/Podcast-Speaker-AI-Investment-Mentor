from __future__ import annotations

import math
import re
from collections import Counter

INVESTMENT_DOMAINS = {"investment", "market", "industry", "portfolio", "behavioral_investing"}
ALIASES = {
    "追高": "追價 進場 買入 估值 價格 entry valuation",
    "踏空": "錯過 機會成本 等待 missed upside wait",
    "風險": "部位 損失 風險報酬 risk exposure loss",
    "不確定": "觀察 假設 條件 uncertainty evidence condition",
    "操作條件": "進場 出場 資料 部位 decision condition evidence position",
}


def tokens(text: str) -> list[str]:
    text = text.casefold()
    result = re.findall(r"[a-z0-9]+", text)
    for part in re.findall(r"[\u3400-\u9fff]+", text):
        result.extend(part[i:i + 2] for i in range(max(1, len(part) - 1)))
    return result


def flatten(value) -> str:
    if isinstance(value, dict):
        return " ".join(flatten(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(flatten(v) for v in value)
    return str(value)


class KnowledgeIndex:
    def __init__(self, cases: dict, principles: dict, settings: dict):
        self.cases, self.principles, self.settings = cases, principles, settings
        self.fields = ("title", "claims", "reasoning_steps", "conditions", "assumptions", "risks", "exceptions",
                       "uncertainty", "contradictions", "preferred_actions", "decision_preferences", "speaker_behavior", "tags")
        self.texts = {rid: " ".join(flatten(case.get(f, [])) for f in self.fields) for rid, case in cases.items()}
        self.bags = {rid: Counter(tokens(text)) for rid, text in self.texts.items()}
        self.df = Counter(t for bag in self.bags.values() for t in bag)
        self.mean_length = sum(sum(b.values()) for b in self.bags.values()) / max(len(cases), 1)

    def principle_index(self) -> list[dict]:
        return [{"principle_id": pid, "name": p["name"], "domains": p["domains"],
                 "decision_context": p["decision_context"], "core_preference": p["core_preference"],
                 "runtime_mode": p["runtime_mode_recommendation"]}
                for pid, p in self.principles.items()]

    def search(self, query: str, domains: list[str], limit: int | None = None) -> list[dict]:
        expanded = query + " " + " ".join(v for k, v in ALIASES.items() if k in query)
        terms = set(tokens(expanded))
        results = []
        for rid, case in self.cases.items():
            bag = self.bags[rid]
            raw = 0.0
            for term in terms:
                tf = bag[term]
                if tf:
                    idf = math.log(1 + (len(self.cases) - self.df[term] + 0.5) / (self.df[term] + 0.5))
                    raw += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * sum(bag.values()) / max(self.mean_length, 1)))
            lexical = raw / (raw + self.settings["bm25_scale"]) if raw else 0
            # Local term-frequency cosine is explainable, not neural embeddings.
            dot = sum(bag[t] for t in terms)
            cosine = dot / math.sqrt(max(1, len(terms)) * max(1, sum(v*v for v in bag.values())))
            metadata = len(terms & set(tokens(flatten(case["tags"] + case["domains"])))) / max(len(terms), 1)
            score = lexical * self.settings["lexical_weight"] + cosine * self.settings["cosine_weight"] + metadata * self.settings["metadata_weight"]
            query_investment = bool(set(domains) & INVESTMENT_DOMAINS)
            mismatch = bool(domains) and not set(domains) & set(case["domains"])
            if query_investment and not set(case["domains"]) & INVESTMENT_DOMAINS:
                continue
            if mismatch and not query_investment:
                score *= self.settings["domain_mismatch_multiplier"]
            if score < self.settings["minimum_score"]:
                continue
            results.append({"record_id": rid, "episode_id": case["episode_id"], "score": score,
                            "lexical_score": lexical, "cosine_score": cosine, "metadata_score": metadata,
                            "matched_fields": [f for f in self.fields if terms & set(tokens(flatten(case.get(f, []))))],
                            "roles": ["direct"], "role_reasons": ["Query relevance; role not final semantic classification"]})
        results.sort(key=lambda r: (-r["score"], r["record_id"]))
        return results[:limit or self.settings["candidate_limit"]]

    def select(self, query: str, analysis: dict) -> dict:
        candidates = {}
        for text in [query, *analysis["search_terms"][:self.settings["search_query_limit"]]]:
            for hit in self.search(text, analysis["target_domains"]):
                if hit["record_id"] not in candidates or hit["score"] > candidates[hit["record_id"]]["score"]:
                    candidates[hit["record_id"]] = hit
        selected_principles, rejected = [], []
        for pid in analysis["principle_ids"][:self.settings["principle_limit"]]:
            p = self.principles.get(pid)
            if not p or not p["eligible_for_online_reasoning"] or p["runtime_mode_recommendation"] != "principle_assisted":
                rejected.append({"principle_id": pid, "reason": "Unavailable or not eligible"}); continue
            if not set(p["domains"]) & set(analysis["target_domains"]):
                rejected.append({"principle_id": pid, "reason": "Domain mismatch"}); continue
            links = [(rid, "supporting", "Principle-linked evidence") for rid in p["supporting_records"]]
            links += [(r["record_id"], "potential_conflict", r["explanation"]) for r in p["contradictions"]]
            links += [(r["record_id"], "exception", r["explanation"]) for r in p["counterexamples"]]
            # Never retain a principle while silently dropping its contrary side.
            if len({rid for rid, _, _ in links}) > self.settings["case_limit"]:
                rejected.append({"principle_id": pid, "reason": "Evidence bundle exceeds context limit"}); continue
            selected_principles.append(p)
            for rid, role, reason in links:
                hit = candidates.setdefault(rid, {"record_id": rid, "episode_id": self.cases[rid]["episode_id"],
                                                  "score": 0, "lexical_score": 0, "cosine_score": 0,
                                                  "metadata_score": 0, "matched_fields": [], "roles": [], "role_reasons": []})
                hit["roles"].append(role); hit["role_reasons"].append(reason)
        for hit in candidates.values():
            case = self.cases[hit["record_id"]]
            if case["contradictions"] and "potential_conflict" not in hit["roles"]:
                hit["roles"].append("potential_conflict")
                hit["role_reasons"].append("Case contradiction field; not proof of a conflict with another case")
            if case["exceptions"] and "exception" not in hit["roles"]:
                hit["roles"].append("exception")
                hit["role_reasons"].append("Case contains exception conditions; not necessarily triggered")
        ordered = sorted(candidates.values(), key=lambda h: (-int("potential_conflict" in h["roles"]), -h["score"], h["record_id"]))
        selected, episode_counts, signatures = [], Counter(), set()
        required = {r for p in selected_principles for r in p["supporting_records"]}
        required |= {r["record_id"] for p in selected_principles for r in p["counterexamples"] + p["contradictions"]}
        if len(required) > self.settings["case_limit"]:
            rejected.extend({"principle_id": p["principle_id"], "reason": "Combined evidence exceeds context budget"} for p in selected_principles)
            selected_principles, required = [], set()
        ordered.sort(key=lambda h: h["record_id"] not in required)
        for hit in ordered:
            case = self.cases[hit["record_id"]]
            signature = (case["episode_id"], tuple(case["merged_ids"]),
                         tuple(sorted(tokens(" ".join(flatten(case.get(f, [])) for f in self.fields)))))
            linked = hit["record_id"] in required
            if not linked and (signature in signatures or episode_counts[case["episode_id"]] >= self.settings["max_per_episode"]):
                continue
            if len(selected) >= self.settings["case_limit"]:
                break
            selected.append(hit); signatures.add(signature); episode_counts[case["episode_id"]] += 1
        return {"cases": [{**self.cases[h["record_id"]], "retrieval": h} for h in selected],
                "principles": selected_principles, "rejected_principles": rejected,
                "candidate_count": len(candidates)}
