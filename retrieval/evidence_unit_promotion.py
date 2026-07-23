"""Promotion status for guarded evidence-unit retrieval.

The calibrated evidence-unit path is allowed only after its held-out mixed-qrels
promotion gate passes. Keeping this as a small loader prevents evaluation
results from living only in docs while live code silently drifts.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Union


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACCEPTANCE_PATH = (
    PROJECT_ROOT
    / "data"
    / "exports"
    / "document_vs_evidence_units_company_ir_holdout_v1"
    / "promotion_gate_mixed_v1"
    / "evidence_unit_promotion_mixed_v1_acceptance.csv"
)


@dataclass(frozen=True)
class EvidenceUnitPromotionStatus:
    accepted: bool
    decision: str
    reason: str
    human_label_count: int
    min_human_labels: int
    delta_ndcg_at_10: float
    delta_precision_at_10: float
    calibrated_judged_rate_at_10: float
    artifact_path: str

    @property
    def status_label(self) -> str:
        return "accepted" if self.accepted else self.decision or "missing_acceptance_artifact"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"status_label": self.status_label}


def _to_int(value: object) -> int:
    try:
        return int(float(str(value or "").strip()))
    except ValueError:
        return 0


def _to_float(value: object) -> float:
    try:
        return float(str(value or "").strip())
    except ValueError:
        return 0.0


def _missing_status(path: Union[str, Path]) -> EvidenceUnitPromotionStatus:
    return EvidenceUnitPromotionStatus(
        accepted=False,
        decision="missing_acceptance_artifact",
        reason="acceptance_report_not_found",
        human_label_count=0,
        min_human_labels=0,
        delta_ndcg_at_10=0.0,
        delta_precision_at_10=0.0,
        calibrated_judged_rate_at_10=0.0,
        artifact_path=str(path),
    )


@lru_cache(maxsize=8)
def load_evidence_unit_promotion_status(path: str = str(DEFAULT_ACCEPTANCE_PATH)) -> EvidenceUnitPromotionStatus:
    artifact = Path(path)
    if not artifact.exists():
        return _missing_status(artifact)

    with artifact.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return EvidenceUnitPromotionStatus(
            accepted=False,
            decision="empty_acceptance_artifact",
            reason="acceptance_report_has_no_rows",
            human_label_count=0,
            min_human_labels=0,
            delta_ndcg_at_10=0.0,
            delta_precision_at_10=0.0,
            calibrated_judged_rate_at_10=0.0,
            artifact_path=str(artifact),
        )

    row = rows[0]
    decision = str(row.get("decision", "")).strip()
    reason = str(row.get("reason", "")).strip()
    human_count = _to_int(row.get("human_label_count"))
    min_human = _to_int(row.get("min_human_labels"))
    delta_ndcg = _to_float(row.get("delta_ndcg_at_10"))
    delta_precision = _to_float(row.get("delta_precision_at_10"))
    judged_rate = _to_float(row.get("calibrated_judged_rate_at_10"))
    accepted = (
        decision == "accept_guarded_promotion"
        and human_count >= min_human
        and delta_ndcg > 0
        and delta_precision >= -0.02
        and judged_rate >= 1.0
    )
    return EvidenceUnitPromotionStatus(
        accepted=accepted,
        decision=decision or "unknown_decision",
        reason=reason,
        human_label_count=human_count,
        min_human_labels=min_human,
        delta_ndcg_at_10=delta_ndcg,
        delta_precision_at_10=delta_precision,
        calibrated_judged_rate_at_10=judged_rate,
        artifact_path=str(artifact),
    )
