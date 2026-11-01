"""Deterministic scoring.  This module deliberately never asks an AI to decide state."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from typing import Any

PASS = Fraction(4, 5)


def _fraction(value: Any) -> Fraction:
    if value is None:
        return Fraction(1)
    value = Fraction(str(value))
    if value <= 0:
        raise ValueError("요구사항 가중치는 0보다 커야 합니다.")
    return value


def half_up(value: Fraction) -> str:
    """One decimal for display only; callers must use raw for decisions."""
    decimal = Decimal(value.numerator) / Decimal(value.denominator)
    return str(decimal.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def evaluate_requirement(requirement: dict[str, Any]) -> dict[str, Any]:
    checks = requirement.get("checks") or []
    if not checks:
        raise ValueError("채점 기준에 확인 항목이 없습니다.")
    statuses = [c.get("status") for c in checks]
    if any(s not in {"met", "not_met", "unknown"} for s in statuses):
        raise ValueError("확인 항목 상태가 올바르지 않습니다.")
    ids = [c.get("id") for c in checks]
    if any(not identifier for identifier in ids) or len(set(ids)) != len(ids):
        raise ValueError("확인 항목 ID는 비어 있지 않고 서로 달라야 합니다.")
    unknown = any(s == "unknown" for s in statuses)
    met = sum(s == "met" for s in statuses)
    raw = Fraction(met, len(checks))
    essential_ok = all(c.get("status") == "met" for c in checks if c.get("essential"))
    held = unknown
    passed = None if held else raw >= PASS and essential_ok
    return {
        "id": requirement["id"], "detailId": requirement.get("detailId"),
        "behavior": requirement.get("behavior"), "conditions": requirement.get("conditions"),
        "level": requirement.get("level"), "met": met, "total": len(checks),
        # Keep the exact ratio JSON-safe.  Decisions above were made with Fraction;
        # no binary float is persisted or returned as an authority value.
        "raw": f"{raw.numerator}/{raw.denominator}", "rawText": f"{raw.numerator}/{raw.denominator}",
        "display": half_up(raw), "held": held, "passed": passed,
        "essentialOk": essential_ok, "checks": checks,
    }


def evaluate_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    """Return address-level evidence without any persistence or judgement inference."""
    results = [evaluate_requirement(r) for r in attempt.get("requirements", [])]
    if not results:
        raise ValueError("평가할 요구사항이 없습니다.")
    weights = [_fraction(r.get("weight")) for r in attempt["requirements"]]
    # Unknown evidence makes only that address pending. Overall result stays pending too,
    # so a partial photograph cannot accidentally be shown as a completed submission.
    known = [r for r in results if not r["held"]]
    weighted = sum((Fraction(r["raw"]) * w for r, w in zip(results, weights)), Fraction())
    denominator = sum(weights, Fraction())
    raw = weighted / denominator
    held = any(r["held"] for r in results)
    return {
        "requirements": results, "raw": f"{raw.numerator}/{raw.denominator}", "rawText": f"{raw.numerator}/{raw.denominator}",
        "display": half_up(raw), "held": held,
        "passed": None if held else all(r["passed"] for r in results),
        "knownRequirements": len(known), "totalRequirements": len(results),
    }
