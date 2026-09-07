import sqlite3
from pathlib import Path

from .test_device_config_contract_migration import _alembic


def test_voice_route_migration_preserves_old_roles_routes_and_costs(tmp_path: Path) -> None:
    database = tmp_path / "voice-routes.db"
    _alembic(database, "upgrade", "20260903_08")
    with sqlite3.connect(database) as connection:
        connection.execute("""
            INSERT INTO model_presets (
                id, display_name, asr_provider, asr_model, llm_provider, llm_model,
                tts_provider, tts_model, created_at, updated_at
            ) VALUES ('old-route', '旧方案', 'dashscope', 'old-asr', 'deepseek', 'old-llm',
                      'dashscope', 'old-tts', '2026-09-01', '2026-09-01')
        """)
        connection.execute("""
            INSERT INTO agents (
                id, owner_user_id, usage_profile_id, name, system_prompt,
                model_preset_id, voice_preset_id, llm_temperature, tts_speech_rate, tools_json,
                created_at, updated_at
            ) VALUES ('old-agent', 'old-owner', 'old-profile', '原角色', '原设定',
                      'old-route', 'old-voice', 0.85, 1.25, '{}', '2026-09-01', '2026-09-01')
        """)
        connection.execute("""
            INSERT INTO provider_usage (
                id, user_id, device_id, provider, model, operation, cost_micros, created_at
            ) VALUES ('old-usage', 'old-owner', 'old-device', 'deepseek', 'old-llm',
                      'llm', 123, '2026-09-01')
        """)
    _alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("""
            SELECT model_preset_id, voice_preset_id, llm_temperature, tts_speech_rate
            FROM agents WHERE id='old-agent'
        """).fetchone() == ("old-route", "old-voice", 0.85, 1.25)
        assert connection.execute("""
            SELECT route_kind, realtime_provider, realtime_model, llm_provider, llm_model
            FROM model_presets WHERE id='old-route'
        """).fetchone() == ("cascade", None, None, "deepseek", "old-llm")
        assert connection.execute("""
            SELECT cost_micros, cost_status, billing_event_key, usage_details_json
            FROM provider_usage WHERE id='old-usage'
        """).fetchone() == (123, "estimated", None, "{}")
    _alembic(database, "check")
    _alembic(database, "downgrade", "20260903_08")
    _alembic(database, "upgrade", "head")
    _alembic(database, "check")
