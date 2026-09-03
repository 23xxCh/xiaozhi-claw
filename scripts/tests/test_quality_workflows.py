import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_quality_workflow_checks_dialogue_assets_and_host_tests() -> None:
    source = (ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert "generate_dialogue_face_previews.py --check" in source
    assert "test_hensun_dialogue_faces.py" in source


def test_staging_web_build_uses_explicit_same_origin_api_base() -> None:
    compose = (ROOT / "deploy/docker-compose.server.yml").read_text(encoding="utf-8")
    api_client = (ROOT / "web/lib/api.ts").read_text(encoding="utf-8")

    assert 'NEXT_PUBLIC_CONTROL_API_URL: "/"' in compose
    assert '?? ""' in api_client
    assert '?? "http://127.0.0.1:8000"' not in api_client


def test_firmware_matrix_compiles_local_variant_as_ci_only() -> None:
    source = (ROOT / ".github/workflows/firmware-release.yml").read_text(
        encoding="utf-8"
    )

    assert "hensun-cam-selfhosted-landscape-local-v1" in source
    assert "https://api.hensun.invalid/v1/device/xiaozhi-bootstrap" in source
    assert "CI_ONLY_DO_NOT_FLASH.txt" in source
    assert "ci-only-${{ matrix.variant }}" in source


def test_local_landscape_variant_keeps_bounded_multiturn_enabled() -> None:
    config = json.loads(
        (
            ROOT
            / "firmware"
            / "xiaozhi-esp32"
            / "main"
            / "boards"
            / "hensun"
            / "hensun-cam-pilot-v1"
            / "config.json"
        ).read_text(encoding="utf-8")
    )
    local_build = next(
        build
        for build in config["builds"]
        if build["name"] == "hensun-cam-selfhosted-landscape-local-v1"
    )

    assert "CONFIG_HENSUN_ONE_SHOT_CONVERSATION=n" in local_build["sdkconfig_append"]
