"""Evaluate two real-device voice rounds from privacy-safe gateway outcome logs."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOG_MARKER = "voice turn outcome "
REQUIRED_FIELDS = {
    "event",
    "schema_version",
    "serial",
    "conversation_id",
    "turn_id",
    "outcome",
    "error_code",
    "fallback_operations",
    "device_speaker_started_ms",
}


@dataclass(frozen=True, slots=True)
class LoadedRound:
    path: Path
    records: tuple[dict[str, Any], ...]
    invalid_records: int = 0


def _valid_record(value: object) -> bool:
    if not isinstance(value, dict) or not REQUIRED_FIELDS <= value.keys():
        return False
    if value.get("event") != "voice_turn_outcome" or value.get("schema_version") != 1:
        return False
    if not all(
        isinstance(value.get(field), str) and bool(value[field])
        for field in ("serial", "conversation_id", "turn_id", "outcome")
    ):
        return False
    latency = value.get("device_speaker_started_ms")
    if latency is not None and (not isinstance(latency, int) or latency < 0):
        return False
    fallbacks = value.get("fallback_operations")
    return isinstance(fallbacks, list) and all(isinstance(item, str) for item in fallbacks)


def load_round(path: Path) -> LoadedRound:
    records: list[dict[str, Any]] = []
    invalid_records = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if LOG_MARKER not in line:
            continue
        raw_record = line.split(LOG_MARKER, 1)[1].strip()
        try:
            record = json.loads(raw_record)
        except json.JSONDecodeError:
            invalid_records += 1
            continue
        if not _valid_record(record):
            invalid_records += 1
            continue
        records.append(record)
    return LoadedRound(path=path, records=tuple(records), invalid_records=invalid_records)


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]


def _evaluate_round(
    loaded: LoadedRound,
    *,
    expected_turns: int,
    minimum_sessions: int,
    minimum_success_rate: float,
    p50_limit_ms: int,
    p90_limit_ms: int,
) -> dict[str, Any]:
    records = list(loaded.records)
    successful = [
        record
        for record in records
        if record["outcome"] == "completed" and record["device_speaker_started_ms"] is not None
    ]
    latencies = [int(record["device_speaker_started_ms"]) for record in successful]
    p50_ms = _percentile(latencies, 0.50)
    p90_ms = _percentile(latencies, 0.90)
    success_rate = len(successful) / len(records) if records else 0.0
    sessions = {str(record["conversation_id"]) for record in records}
    turn_ids = [str(record["turn_id"]) for record in records]
    outcomes = Counter(str(record["outcome"]) for record in records)
    errors = Counter(
        str(record["error_code"]) for record in records if record.get("error_code") is not None
    )
    fallbacks = Counter(
        operation for record in records for operation in record.get("fallback_operations", [])
    )

    failures: list[str] = []
    if loaded.invalid_records:
        failures.append("invalid-records-present")
    if len(records) != expected_turns:
        failures.append(f"expected-{expected_turns}-turns-got-{len(records)}")
    if len(sessions) < minimum_sessions:
        failures.append(f"fewer-than-{minimum_sessions}-sessions")
    if len(set(turn_ids)) != len(turn_ids):
        failures.append("duplicate-turn-ids")
    if success_rate < minimum_success_rate:
        failures.append("success-rate-below-98-percent")
    if p50_ms is None:
        failures.append("missing-p50")
    elif p50_ms > p50_limit_ms:
        failures.append(f"p50-above-{p50_limit_ms}-ms")
    if p90_ms is None:
        failures.append("missing-p90")
    elif p90_ms > p90_limit_ms:
        failures.append(f"p90-above-{p90_limit_ms}-ms")

    return {
        "path": str(loaded.path),
        "pass": not failures,
        "turns": len(records),
        "successful_turns": len(successful),
        "success_rate": round(success_rate, 4),
        "sessions": len(sessions),
        "p50_ms": p50_ms,
        "p90_ms": p90_ms,
        "outcomes": dict(sorted(outcomes.items())),
        "errors": dict(sorted(errors.items())),
        "fallbacks": dict(sorted(fallbacks.items())),
        "invalid_records": loaded.invalid_records,
        "failures": failures,
    }


def evaluate_acceptance(
    rounds: list[LoadedRound],
    *,
    expected_rounds: int = 2,
    expected_turns: int = 50,
    minimum_sessions: int = 5,
    minimum_success_rate: float = 0.98,
    p50_limit_ms: int = 2000,
    p90_limit_ms: int = 2500,
) -> dict[str, Any]:
    failures: list[str] = []
    if len(rounds) != expected_rounds:
        failures.append(f"expected-{expected_rounds}-rounds-got-{len(rounds)}")
    if len({loaded.path.resolve() for loaded in rounds}) != len(rounds):
        failures.append("duplicate-round-logs")
    round_reports = [
        _evaluate_round(
            loaded,
            expected_turns=expected_turns,
            minimum_sessions=minimum_sessions,
            minimum_success_rate=minimum_success_rate,
            p50_limit_ms=p50_limit_ms,
            p90_limit_ms=p90_limit_ms,
        )
        for loaded in rounds
    ]
    turn_ids = [str(record["turn_id"]) for loaded in rounds for record in loaded.records]
    if len(set(turn_ids)) != len(turn_ids):
        failures.append("duplicate-turn-ids-across-rounds")
    return {
        "pass": not failures and all(report["pass"] for report in round_reports),
        "criteria": {
            "expected_rounds": expected_rounds,
            "turns_per_round": expected_turns,
            "minimum_sessions_per_round": minimum_sessions,
            "minimum_success_rate": minimum_success_rate,
            "p50_limit_ms": p50_limit_ms,
            "p90_limit_ms": p90_limit_ms,
        },
        "failures": failures,
        "rounds": round_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-log", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = evaluate_acceptance([load_round(path) for path in args.round_log])
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
