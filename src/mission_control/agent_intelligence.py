from __future__ import annotations

from dataclasses import dataclass

RATING_BANDS = (
    (95, "Exceptional"),
    (90, "Excellent"),
    (80, "Strong"),
    (70, "Effective"),
    (60, "Needs Improvement"),
    (0, "At Risk"),
)

COMPLEXITY_FACTORS = {
    "C1": 0.85,
    "C2": 0.95,
    "C3": 1.00,
    "C4": 1.05,
    "C5": 1.10,
    "C6": 1.15,
}


@dataclass(frozen=True)
class AgentScoreInput:
    delivery: float
    quality: float
    reliability: float
    goal_advancement: float
    speed: float
    efficiency: float
    complexity_class: str = "C3"
    evidence_penalty: float = 0.0
    confidence: float = 100.0


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def rate_agent(data: AgentScoreInput) -> dict[str, object]:
    dimensions = {
        "delivery": _clamp(data.delivery),
        "quality": _clamp(data.quality),
        "reliability": _clamp(data.reliability),
        "goal_advancement": _clamp(data.goal_advancement),
        "speed": _clamp(data.speed),
        "efficiency": _clamp(data.efficiency),
    }
    base = (
        0.25 * dimensions["delivery"]
        + 0.20 * dimensions["quality"]
        + 0.20 * dimensions["reliability"]
        + 0.15 * dimensions["goal_advancement"]
        + 0.10 * dimensions["speed"]
        + 0.10 * dimensions["efficiency"]
    )
    factor = COMPLEXITY_FACTORS.get(data.complexity_class, 1.0)
    avi = _clamp(base * factor - max(0.0, data.evidence_penalty))
    band = next(name for floor, name in RATING_BANDS if avi >= floor)
    confidence = _clamp(data.confidence)
    return {
        "avi": round(avi, 1),
        "band": band,
        "provisional": confidence < 70.0,
        "confidence": round(confidence, 1),
        "complexity_class": data.complexity_class,
        "dimensions": {key: round(value, 1) for key, value in dimensions.items()},
    }
