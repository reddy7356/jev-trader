from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True, slots=True)
class ReliabilityBin:
    lower: float
    upper: float
    count: int
    mean_predicted: float
    empirical: float


def _as_arrays(
    probabilities: Sequence[float], outcomes: Sequence[int]
) -> tuple[np.ndarray, np.ndarray]:
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    if p.shape != y.shape:
        raise ValueError("probabilities and outcomes must have the same shape")
    if p.size == 0:
        raise ValueError("no samples")
    return p, y


def brier_score(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    p, y = _as_arrays(probabilities, outcomes)
    return float(np.mean((p - y) ** 2))


def log_loss(
    probabilities: Sequence[float], outcomes: Sequence[int], eps: float = 1e-12
) -> float:
    p, y = _as_arrays(probabilities, outcomes)
    p = np.clip(p, eps, 1.0 - eps)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def reliability_curve(
    probabilities: Sequence[float], outcomes: Sequence[int], bins: int = 10
) -> list[ReliabilityBin]:
    p, y = _as_arrays(probabilities, outcomes)
    edges = np.linspace(0.0, 1.0, bins + 1)
    curve: list[ReliabilityBin] = []
    for i in range(bins):
        lower, upper = float(edges[i]), float(edges[i + 1])
        mask = (p >= lower) & (p <= upper if i == bins - 1 else p < upper)
        count = int(np.sum(mask))
        if count == 0:
            curve.append(ReliabilityBin(lower, upper, 0, 0.0, 0.0))
            continue
        curve.append(
            ReliabilityBin(
                lower=lower,
                upper=upper,
                count=count,
                mean_predicted=float(np.mean(p[mask])),
                empirical=float(np.mean(y[mask])),
            )
        )
    return curve


def expected_calibration_error(
    probabilities: Sequence[float], outcomes: Sequence[int], bins: int = 10
) -> float:
    p, y = _as_arrays(probabilities, outcomes)
    curve = reliability_curve(p, y, bins)
    total = len(p)
    ece = 0.0
    for entry in curve:
        if entry.count == 0:
            continue
        ece += (entry.count / total) * abs(entry.empirical - entry.mean_predicted)
    return float(ece)


def _platt_objective(
    a: float, b: float, x: np.ndarray, y: np.ndarray, regularization: float
) -> float:
    z = np.clip(a * x + b, -500.0, 500.0)
    loss = np.sum(np.logaddexp(0.0, z) - y * z)
    return float(loss + 0.5 * regularization * (a * a + b * b))


def fit_platt(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    iterations: int = 200,
    tol: float = 1e-8,
    regularization: float = 1e-2,
) -> tuple[float, float]:
    """Fit Platt scaling on logits with regularized, backtracking Newton.

    The ridge penalty keeps coefficients finite when the data is nearly
    separable, which is common for confident, miscalibrated forecasts, and the
    line search prevents the overshoot pure Newton shows on logistic loss.
    Returns (a, b) for q = sigmoid(a * logit(p) + b).
    """
    p, y = _as_arrays(probabilities, outcomes)
    clipped = np.clip(p, 1e-6, 1.0 - 1e-6)
    x = np.log(clipped / (1.0 - clipped))
    a, b = 1.0, 0.0
    objective = _platt_objective(a, b, x, y, regularization)
    for _ in range(iterations):
        z = np.clip(a * x + b, -500.0, 500.0)
        q = 1.0 / (1.0 + np.exp(-z))
        gradient = np.array(
            [np.sum((q - y) * x) + regularization * a, np.sum(q - y) + regularization * b]
        )
        weights = q * (1.0 - q)
        hessian = np.array(
            [
                [np.sum(weights * x * x) + regularization, np.sum(weights * x)],
                [np.sum(weights * x), np.sum(weights) + regularization],
            ]
        )
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            break
        if float(np.max(np.abs(step))) < tol:
            break

        scale = 1.0
        candidate_a, candidate_b = a, b
        candidate_objective = objective
        for _ in range(40):
            candidate_a = a - scale * float(step[0])
            candidate_b = b - scale * float(step[1])
            candidate_objective = _platt_objective(
                candidate_a, candidate_b, x, y, regularization
            )
            if candidate_objective <= objective:
                break
            scale *= 0.5
        if candidate_objective > objective:
            break
        a, b = candidate_a, candidate_b
        objective = candidate_objective
    return a, b


def apply_platt(probability: float, a: float, b: float) -> float:
    clipped = min(max(probability, 1e-6), 1.0 - 1e-6)
    logit = math.log(clipped / (1.0 - clipped))
    z = a * logit + b
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def load_pairs(
    path: Path, source: str | None = None
) -> tuple[list[float], list[int]]:
    """Load logged triples as (P(up), realized up) pairs.

    Neutral outcomes are excluded; the question is P(up) vs realized direction.
    """
    decisions: dict[str, tuple[float, str]] = {}
    outcomes: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("kind") == "decision":
                judgments = record.get("judgments")
                if not judgments:
                    continue
                probabilities = judgments.get("direction_probabilities") or {}
                decisions[record["id"]] = (
                    float(probabilities.get("up", 0.5)),
                    record.get("source", ""),
                )
            elif record.get("kind") == "outcome":
                outcomes[record["id"]] = record.get("outcome", "neutral")

    probabilities: list[float] = []
    labels: list[int] = []
    for decision_id, (p_up, record_source) in decisions.items():
        if source is not None and record_source != source:
            continue
        outcome = outcomes.get(decision_id)
        if outcome == "up":
            probabilities.append(p_up)
            labels.append(1)
        elif outcome == "down":
            probabilities.append(p_up)
            labels.append(0)
    return probabilities, labels
