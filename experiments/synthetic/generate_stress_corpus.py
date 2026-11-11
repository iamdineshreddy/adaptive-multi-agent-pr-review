"""Generate a clearly labelled SYNTHETIC stress-test corpus for the experiment harness.

This corpus exists to exercise adaptive mechanisms (budget truncation, critical
safety gates, redundancy suppression, disagreement handling, iteration) that
the real-world corpus does not activate. It is NOT research data: every record
carries ``synthetic: true`` and the provenance source is marked synthetic.
Results from it must never be reported as real-world observations.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "datasets" / "synthetic" / "stress_corpus.jsonl"

REVIEWERS = ["security", "quality", "performance", "architecture", "standards"]
SEVERITIES = ["info", "low", "medium", "high", "critical"]
CATEGORIES = {
    "security": "security/xss",
    "quality": "quality/maintainability",
    "performance": "performance/query",
    "architecture": "architecture/coupling",
    "standards": "standards/naming",
}

records: list[dict] = []
rng_seed = 42


def add(review_id, file_path, category, severity, comment, reviewer, confidence,
        round_no=1, memory=None, actionable=None, implemented_later=False,
        explicit_outcome=None, implementation=None, is_review_comment=True,
        related_to_feedback=True, line_start=None, relevance_grade=None):
    records.append({
        "review_id": review_id,
        "file_path": file_path,
        "line_start": line_start if line_start is not None else 10,
        "category": category,
        "severity": severity,
        "comment": comment,
        "reviewer": reviewer,
        "confidence": confidence,
        "round": round_no,
        "memory": memory or {},
        "actionable": actionable,
        "implemented_later": implemented_later,
        "explicit_outcome": explicit_outcome,
        "implementation": implementation,
        "is_review_comment": is_review_comment,
        "related_to_feedback": related_to_feedback,
        "relevance_grade": relevance_grade,
        "synthetic": True,
    })


# Build 5 reviews, each with a redundancy-heavy file to exceed budget of 10.
for r in range(5):
    rid = f"synthetic-review-{r}"
    # Redundancy group of 4 on the same file/category/bucket (lines 8-11)
    for i, rev in enumerate(REVIEWERS):
        add(rid, "src/app.py", "security/xss", "high" if i < 2 else "medium",
            f"XSS vector {i} in template rendering",
            rev, 0.9 if i < 3 else 0.4, line_start=8 + (i % 4),
            implemented_later=(i < 3), implementation="verbatim" if i < 2 else "partial",
            actionable=True, relevance_grade=3 if i < 2 else 1)
    # Critical security finding (mandatory gate)
    add(rid, "src/auth.py", "security/secrets", "critical",
        "Hardcoded production secret in source",
        "security", 0.95, line_start=20,
        implemented_later=True, implementation="verbatim", actionable=True,
        relevance_grade=3)
    # Low-confidence false positive in a large redundancy group (suppression gate)
    for i in range(4):
        add(rid, "src/util.py", "quality/maintainability", "low",
            f"Minor style nit {i}",
            REVIEWERS[i % len(REVIEWERS)], 0.2, line_start=30 + (i % 4),
            implemented_later=False, explicit_outcome="ignored",
            relevance_grade=0)
    # High-confidence protected finding
    add(rid, "src/db.py", "performance/query", "high",
        "N+1 query in user listing",
        "performance", 0.9, line_start=40,
        implemented_later=True, implementation="verbatim", actionable=True,
        relevance_grade=2)
    # Budget overflow: 6 more distinct findings to exceed cap of 10
    for i in range(6):
        add(rid, f"src/mod{i}.py", CATEGORIES[REVIEWERS[i % len(REVIEWERS)]],
            "medium", f"Finding {i} in module {i}",
            REVIEWERS[i % len(REVIEWERS)], 0.6, line_start=5 + i,
            implemented_later=(i % 2 == 0), implementation="partial" if i % 2 == 0 else None,
            explicit_outcome="dismissed" if i % 3 == 0 else None,
            relevance_grade=1 if i % 2 == 0 else 0)
    # Round 2 (iteration) findings
    add(rid, "src/app.py", "security/xss", "medium",
        "Residual XSS after first round",
        "security", 0.7, round_no=2, line_start=8,
        implemented_later=True, implementation="verbatim", actionable=True,
        relevance_grade=2)
    # Rejected / dismissed outcomes
    add(rid, "src/legacy.py", "architecture/coupling", "info",
        "Legacy coupling (dismissed by team)",
        "architecture", 0.5, line_start=1,
        implemented_later=False, explicit_outcome="dismissed",
        relevance_grade=0)
    add(rid, "src/naming.py", "standards/naming", "low",
        "Naming convention (rejected)",
        "standards", 0.4, line_start=2,
        implemented_later=False, explicit_outcome="rejected",
        relevance_grade=0)
    # Repository memory feedback for B4
    add(rid, "src/app.py", "security/xss", "high",
        "XSS with historical acceptance",
        "security", 0.85, line_start=12,
        memory={"accepted": 0.7, "rejected": 0.1, "total": 10},
        implemented_later=True, implementation="verbatim", actionable=True,
        relevance_grade=3)

# Write
OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", encoding="utf-8") as f:
    for rec in records:
        f.write(json.dumps(rec) + "\n")
print(f"wrote {len(records)} synthetic records to {OUT}")