"""Generate the Hensun-only esp-wifi-connect 3.2.2 source overlay.

Keep vendor files untouched. Component version or anchor drift must stop CMake,
so a dependency refresh cannot silently remove the provisioning boundary.
"""

from __future__ import annotations

import argparse
from pathlib import Path

UPSTREAM_VERSION = "3.2.2"
HANDLER_ANCHOR = """            // 获取当前对象
            auto *this_ = static_cast<WifiConfigurationAp *>(req->user_ctx);

            // 打开NVS"""
HANDLER_GUARD = """            // Hensun service endpoints are factory configuration.
            if (cJSON_GetObjectItem(json, "ota_url") != nullptr) {
                cJSON_Delete(json);
                httpd_resp_set_status(req, "403 Forbidden");
                httpd_resp_set_type(req, "application/json");
                httpd_resp_send(req,
                    "{\\"success\\":false,\\"error\\":\\"Service address is managed by Hensun\\"}",
                    HTTPD_RESP_USE_STRLEN);
                return ESP_OK;
            }

"""
FORM_FIELD = "                ota_url: document.getElementById('ota_url').value,\n"


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if source.count(old) != 1:
        raise ValueError(f"esp-wifi-connect {UPSTREAM_VERSION}: unexpected {label} anchor")
    return source.replace(old, new, 1)


def prepare(component_dir: Path, output_dir: Path) -> tuple[Path, Path]:
    manifest = (component_dir / "idf_component.yml").read_text(encoding="utf-8")
    versions = [
        line.removeprefix("version:").strip()
        for line in manifest.splitlines()
        if line.startswith("version:")
    ]
    if versions != [UPSTREAM_VERSION]:
        raise ValueError("Hensun Wi-Fi security overlay requires esp-wifi-connect 3.2.2")

    source = (component_dir / "wifi_configuration_ap.cc").read_text(encoding="utf-8")
    source = replace_once(source, HANDLER_ANCHOR, HANDLER_GUARD + HANDLER_ANCHOR, "advanced submit")
    source = replace_once(
        source,
        "if (!this_->ota_url_.empty()) {",
        "if (this_->show_ota_config_ && !this_->ota_url_.empty()) {",
        "advanced config",
    )
    page = (component_dir / "assets/wifi_configuration.html").read_text(encoding="utf-8")
    page = replace_once(page, FORM_FIELD, "", "advanced form")

    output_dir.mkdir(parents=True, exist_ok=True)
    generated = (
        output_dir / "wifi_configuration_ap.cc",
        output_dir / "hensun_wifi_configuration.html",
    )
    for path, content in zip(generated, (source, page), strict=True):
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8", newline="\n")
    return generated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.component_dir, args.output_dir)


if __name__ == "__main__":
    main()
