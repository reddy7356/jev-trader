from __future__ import annotations

import json
import math
import random

import pytest

from jev_trader.calibration.metrics import (
    apply_platt,
    brier_score,
    expected_calibration_error,
    fit_platt,
    load_pairs,
    log_loss,
    reliability_curve,
)


def test_brier_perfect_predictions():
    assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == pytest.approx(0.0)


def test_brier_worst_case():
    assert brier_score([0.0, 1.0], [1, 0]) == pytest.approx(1.0)


def test_log_loss_improves_with_better_probabilities():
    good = log_loss([0.9, 0.1], [1, 0])
    bad = log_loss([0.6, 0.4], [1, 0])
    assert good < bad


def test_ece_zero_when_calibrated():
    probabilities = [0.5] * 100
    outcomes = [1, 0] * 50
    assert expected_calibration_error(probabilities, outcomes) == pytest.approx(0.0)


def test_ece_detects_miscalibration():
    probabilities = [0.9] * 100
    outcomes = [1] * 50 + [0] * 50
    assert expected_calibration_error(probabilities, outcomes) == pytest.approx(0.4)


def test_reliability_curve_bins():
    curve = reliability_curve([0.05, 0.15, 0.95], [0, 0, 1], bins=10)
    assert len(curve) == 10
    assert curve[0].count == 1
    assert curve[9].count == 1
    assert curve[9].empirical == 1.0


def test_platt_scaling_improves_miscalibrated_forecasts():
    rng = random.Random(11)
    probabilities = []
    outcomes = []
    for _ in range(4000):
        p = rng.uniform(0.01, 0.99)
        logit = math.log(p / (1.0 - p))
        true_p = 1.0 / (1.0 + math.exp(-(1.8 * logit)))
        probabilities.append(p)
        outcomes.append(1 if rng.random() < true_p else 0)

    before = brier_score(probabilities, outcomes)
    a, b = fit_platt(probabilities, outcomes)
    calibrated = [apply_platt(p, a, b) for p in probabilities]
    after = brier_score(calibrated, outcomes)
    assert after < before
    assert a > 1.0


def test_load_pairs_roundtrip(tmp_path):
    path = tmp_path / "cal.jsonl"
    records = [
        {
            "kind": "decision",
            "id": "a",
            "source": "jev",
            "judgments": {"direction_probabilities": {"up": 0.7, "down": 0.3}},
        },
        {
            "kind": "decision",
            "id": "b",
            "source": "fallback",
            "judgments": {"direction_probabilities": {"up": 0.2, "down": 0.8}},
        },
        {
            "kind": "decision",
            "id": "c",
            "source": "jev",
            "judgments": {"direction_probabilities": {"up": 0.5, "down": 0.5}},
        },
        {"kind": "outcome", "id": "a", "outcome": "up"},
        {"kind": "outcome", "id": "b", "outcome": "up"},
        {"kind": "outcome", "id": "c", "outcome": "neutral"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    probabilities, outcomes = load_pairs(path)
    assert probabilities == pytest.approx([0.7, 0.2])
    assert outcomes == [1, 1]

    jev_probabilities, jev_outcomes = load_pairs(path, source="jev")
    assert jev_probabilities == pytest.approx([0.7])
    assert jev_outcomes == [1]
