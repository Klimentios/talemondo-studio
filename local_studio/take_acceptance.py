"""Shared admission rule for selected scene takes, independent of execution backend."""
from __future__ import annotations


def take_is_accepted(cut: dict) -> bool:
    job_id = cut.get("shotJobId")
    qc = cut.get("shotQc") or {}
    review = cut.get("takeReview") or {}
    return bool(job_id and not cut.get("productionSpecChanged")
                and cut.get("selectedTakeId", job_id) == job_id
                and cut.get("shotQcReview") == "accepted"
                and qc.get("job_id") == job_id
                and qc.get("decision") in {"accept", "adapt_next", "manual_review"}
                and (not review or (review.get("state") == "accepted"
                                   and review.get("job_id") == job_id)))
