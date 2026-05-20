"""Shared pattern detector result type."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PatternResult:
    pattern: str
    status: str
    pivot: float
    target: float
    stop_loss: float
    confidence: float
    explanation: str
    timeframe: str
    bars_in_pattern: int
    quality_score: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "status": self.status,
            "pivot": self.pivot,
            "target": self.target,
            "stop_loss": self.stop_loss,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "timeframe": self.timeframe,
            "bars_in_pattern": self.bars_in_pattern,
            "quality_score": self.quality_score,
            "extra": self.extra,
        }
