import json
from pathlib import Path

from scripts.voice_stability_acceptance import evaluate_acceptance, load_round


def _record(
    index: int,
    *,
    round_name: str = "round",
    latency_ms: int | None = 1800,
    outcome: str = "completed",
    fallback_operations: list[str] | None = None,
) -> dict[str, object]:
    return {
        "event": "voice_turn_outcome",
        "schema_version": 1,
        "serial": "HENSUN-NOCAM-TEST",
        "conversation_id": f"{round_name}-conversation-{index % 5}",
        "turn_id": f"{round_name}-turn-{index}",
        "reply_id": f"{round_name}-reply-{index}" if latency_ms is not None else None,
        "outcome": outcome,
        "error_code": None if outcome == "completed" else "ai-unavailable",
        "fallback_operations": fallback_operations or [],
        "device_speaker_started_ms": latency_ms,
    }


def _write_round(path: Path, records: list[dict[str, object]]) -> None:
    lines = [
        f"2026-09-03 INFO voice turn outcome {json.dumps(record, sort_keys=True)}"
        for record in records
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_two_complete_rounds_pass_latency_and_success_gates(tmp_path: Path) -> None:
    round_paths = [tmp_path / "round-1.log", tmp_path / "round-2.log"]
    for round_number, round_path in enumerate(round_paths, start=1):
        round_name = f"round-{round_number}"
        records = [_record(index, round_name=round_name) for index in range(49)]
        records.append(_record(49, round_name=round_name, latency_ms=None, outcome="failed"))
        records[0] = _record(
            0,
            round_name=round_name,
            latency_ms=2300,
            fallback_operations=["tts"],
        )
        _write_round(round_path, records)

    result = evaluate_acceptance([load_round(path) for path in round_paths])

    assert result["pass"] is True
    assert len(result["rounds"]) == 2
    assert all(item["turns"] == 50 for item in result["rounds"])
    assert all(item["successful_turns"] == 49 for item in result["rounds"])
    assert all(item["success_rate"] == 0.98 for item in result["rounds"])
    assert all(item["p50_ms"] == 1800 for item in result["rounds"])
    assert all(item["p90_ms"] == 1800 for item in result["rounds"])
    assert all(item["fallbacks"] == {"tts": 1} for item in result["rounds"])


def test_acceptance_rejects_bad_latency_missing_sessions_and_invalid_records(
    tmp_path: Path,
) -> None:
    round_path = tmp_path / "failed-round.log"
    records = [_record(index, latency_ms=2600) for index in range(48)]
    records.extend(
        [
            _record(48, latency_ms=None, outcome="failed"),
            _record(49, latency_ms=None, outcome="failed"),
        ]
    )
    for record in records:
        record["conversation_id"] = "only-one-session"
    _write_round(round_path, records)
    with round_path.open("a", encoding="utf-8") as handle:
        handle.write('INFO voice turn outcome {"event":"voice_turn_outcome"}\n')

    loaded = load_round(round_path)
    result = evaluate_acceptance([loaded, loaded])

    assert result["pass"] is False
    assert loaded.invalid_records == 1
    assert {
        "success-rate-below-98-percent",
        "p50-above-2000-ms",
        "p90-above-2500-ms",
        "fewer-than-5-sessions",
        "invalid-records-present",
    } <= set(result["rounds"][0]["failures"])


def test_acceptance_requires_exactly_two_rounds(tmp_path: Path) -> None:
    round_path = tmp_path / "one-round.log"
    _write_round(round_path, [_record(index) for index in range(50)])

    result = evaluate_acceptance([load_round(round_path)])

    assert result["pass"] is False
    assert result["failures"] == ["expected-2-rounds-got-1"]


def test_acceptance_rejects_reused_round_log(tmp_path: Path) -> None:
    round_path = tmp_path / "reused.log"
    _write_round(round_path, [_record(index) for index in range(50)])
    loaded = load_round(round_path)

    result = evaluate_acceptance([loaded, loaded])

    assert result["pass"] is False
    assert "duplicate-round-logs" in result["failures"]
    assert "duplicate-turn-ids-across-rounds" in result["failures"]
