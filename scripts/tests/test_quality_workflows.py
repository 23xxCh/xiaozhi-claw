from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_quality_workflow_checks_dialogue_assets_and_host_tests() -> None:
    source = (ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert "generate_dialogue_face_previews.py --check" in source
    assert "test_hensun_dialogue_faces.py" in source


def test_firmware_matrix_compiles_local_variant_as_ci_only() -> None:
    source = (ROOT / ".github/workflows/firmware-release.yml").read_text(
        encoding="utf-8"
    )

    assert "hensun-cam-selfhosted-landscape-local-v1" in source
    assert "https://api.hensun.invalid/v1/device/xiaozhi-bootstrap" in source
    assert "CI_ONLY_DO_NOT_FLASH.txt" in source
    assert "ci-only-${{ matrix.variant }}" in source
