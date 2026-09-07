import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts/prepare_hensun_wifi_security.py"
spec = importlib.util.spec_from_file_location("hensun_wifi_security", GENERATOR)
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)
VENDOR = ROOT / "managed_components/78__esp-wifi-connect"
CMAKE = os.environ.get("HENSUN_TEST_CMAKE") or shutil.which("cmake")
NINJA = os.environ.get("HENSUN_TEST_NINJA") or shutil.which("ninja")


class HensunWifiOverlayTests(unittest.TestCase):
    def fixture(self, directory):
        component = Path(directory) / "vendor"
        (component / "assets").mkdir(parents=True)
        (component / "idf_component.yml").write_text("version: 3.2.2\n", encoding="utf-8")
        (component / "wifi_configuration_ap.cc").write_text(
            overlay.HANDLER_ANCHOR + "\nif (!this_->ota_url_.empty()) {\n}",
            encoding="utf-8",
        )
        (component / "assets/wifi_configuration.html").write_text(
            "const config = {\n" + overlay.FORM_FIELD + "max_tx_power: 20\n};",
            encoding="utf-8",
        )
        return component

    def test_real_vendor_overlay_keeps_inputs_unchanged_and_rejects_before_nvs(self):
        if not VENDOR.is_dir():
            self.skipTest("installed esp-wifi-connect is unavailable")
        inputs = [
            VENDOR / "wifi_configuration_ap.cc",
            VENDOR / "assets/wifi_configuration.html",
            VENDOR / "idf_component.yml",
        ]
        before = [hashlib.sha256(path.read_bytes()).digest() for path in inputs]
        with tempfile.TemporaryDirectory() as directory:
            cpp, html = overlay.prepare(VENDOR, Path(directory))
            source = cpp.read_text(encoding="utf-8")
            handler = source.split('.uri = "/advanced/submit"', 1)[1]
            rejection = handler.index('cJSON_GetObjectItem(json, "ota_url")')
            self.assertLess(rejection, handler.index('nvs_open("wifi", NVS_READWRITE'))
            self.assertIn('httpd_resp_set_status(req, "403 Forbidden")', handler)
            self.assertIn('"{\\"success\\":false,', handler)
            self.assertIn("if (this_->show_ota_config_ && !this_->ota_url_.empty())", source)
            form = (
                html.read_text(encoding="utf-8").split("const config = {", 1)[1].split("};", 1)[0]
            )
            self.assertNotIn("ota_url", form)
            self.assertIn("max_tx_power", form)
            self.assertIn("sleep_mode", form)
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).digest() for path in inputs])

    def test_generation_is_repeatable_without_touching_unchanged_output(self):
        with tempfile.TemporaryDirectory() as directory:
            component = self.fixture(directory)
            output = Path(directory) / "generated"
            paths = overlay.prepare(component, output)
            before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]
            self.assertEqual(paths, overlay.prepare(component, output))
            self.assertEqual(
                before, [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]
            )

    def test_changed_version_fails_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            component = self.fixture(directory)
            (component / "idf_component.yml").write_text("version: 3.2.3\n", encoding="utf-8")
            output = Path(directory) / "generated"
            with self.assertRaisesRegex(ValueError, "requires esp-wifi-connect 3.2.2"):
                overlay.prepare(component, output)
            self.assertFalse(output.exists())

    def test_missing_or_duplicate_patch_anchors_fail_closed(self):
        for relative, anchor in (
            ("wifi_configuration_ap.cc", overlay.HANDLER_ANCHOR),
            ("wifi_configuration_ap.cc", "if (!this_->ota_url_.empty()) {"),
            ("assets/wifi_configuration.html", overlay.FORM_FIELD),
        ):
            for duplicate in (False, True):
                with self.subTest(relative=relative, anchor=anchor, duplicate=duplicate):
                    with tempfile.TemporaryDirectory() as directory:
                        component = self.fixture(directory)
                        path = component / relative
                        source = path.read_text(encoding="utf-8")
                        path.write_text(
                            source + anchor if duplicate else source.replace(anchor, ""),
                            encoding="utf-8",
                        )
                        output = Path(directory) / "generated"
                        with self.assertRaisesRegex(ValueError, "unexpected"):
                            overlay.prepare(component, output)
                        self.assertFalse(output.exists())

    @unittest.skipUnless(
        CMAKE and NINJA, "CMake/Ninja paths required for isolated source wiring check"
    )
    def test_cmake_replaces_both_vendor_inputs_only_for_hensun(self):
        for hensun in (True, False):
            with self.subTest(hensun=hensun), tempfile.TemporaryDirectory() as directory:
                component = self.fixture(directory)
                (component / "wifi_manager.cc").write_text("", encoding="utf-8")
                project = Path(directory) / "project"
                project.mkdir()
                cmake_source = f'''cmake_minimum_required(VERSION 3.20)
project(overlay_source_check LANGUAGES NONE)
set(CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1 {"ON" if hensun else "OFF"})
set(vendor "{component.as_posix()}")
add_library(wifi_component INTERFACE)
set_source_files_properties("${{CMAKE_BINARY_DIR}}/wifi_configuration.html.S"
    PROPERTIES GENERATED TRUE)
target_sources(wifi_component PRIVATE "${{vendor}}/wifi_configuration_ap.cc"
    "${{CMAKE_BINARY_DIR}}/wifi_configuration.html.S" "${{vendor}}/wifi_manager.cc")
function(idf_component_get_property output component property)
    if(property STREQUAL "COMPONENT_DIR")
        set(${{output}} "${{vendor}}" PARENT_SCOPE)
    else()
        set(${{output}} wifi_component PARENT_SCOPE)
    endif()
endfunction()
function(idf_build_get_property output property)
    set(${{output}} "{Path(sys.executable).as_posix()}" PARENT_SCOPE)
endfunction()
function(target_add_binary_data target file type)
    if(NOT ARGV3 STREQUAL "RENAME_TO" OR NOT ARGV4 STREQUAL "wifi_configuration.html")
        message(FATAL_ERROR "overlay must preserve the original embedded symbol")
    endif()
    file(WRITE "${{CMAKE_BINARY_DIR}}/embedded.txt" "${{file}}")
    add_custom_command(OUTPUT "${{CMAKE_BINARY_DIR}}/hensun_wifi_configuration.html.S"
        COMMAND "${{CMAKE_COMMAND}}" -E copy "${{file}}"
            "${{CMAKE_BINARY_DIR}}/hensun_wifi_configuration.html.S"
        DEPENDS "${{file}}")
    target_sources(${{target}} PRIVATE "${{file}}")
endfunction()
include("{(ROOT / "cmake/hensun_wifi_security.cmake").as_posix()}")
get_target_property(final_sources wifi_component SOURCES)
file(WRITE "${{CMAKE_BINARY_DIR}}/sources.txt" "${{final_sources}}")
'''
                (project / "CMakeLists.txt").write_text(cmake_source, encoding="utf-8")
                build = Path(directory) / "build"
                result = subprocess.run(
                    [
                        CMAKE,
                        "-G",
                        "Ninja",
                        "-S",
                        str(project),
                        "-B",
                        str(build),
                        f"-DCMAKE_MAKE_PROGRAM={NINJA}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                sources = (build / "sources.txt").read_text(encoding="utf-8")
                if hensun:
                    generated = subprocess.run(
                        [CMAKE, "--build", str(build), "--target", "hensun_wifi_embed"],
                        capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
                    self.assertTrue((build / "hensun_wifi_configuration.html.S").is_file())
                    self.assertNotIn(f"{component.as_posix()}/wifi_configuration_ap.cc", sources)
                    self.assertNotIn("wifi_configuration.html.S", sources)
                    self.assertIn("hensun_wifi_security/wifi_configuration_ap.cc", sources)
                    self.assertIn("hensun_wifi_security/hensun_wifi_configuration.html", sources)
                else:
                    self.assertIn(f"{component.as_posix()}/wifi_configuration_ap.cc", sources)
                    self.assertIn("wifi_configuration.html.S", sources)
                    self.assertFalse((build / "hensun_wifi_security").exists())


class HensunBootstrapSourceContracts(unittest.TestCase):
    def test_identity_guard_precedes_reading_secret_and_both_callers_pass_destination(self):
        source = (ROOT / "main/ota.cc").read_text(encoding="utf-8")
        setup = source.split("std::unique_ptr<Http> Ota::SetupHttp", 1)[1].split("return http;", 1)[
            0
        ]
        self.assertLess(
            setup.index("HensunSameBootstrapOrigin"), setup.index("GetHensunDeviceSecret()")
        )
        self.assertIn("return nullptr;", setup)
        self.assertEqual(source.count("auto http = SetupHttp(url);"), 2)
        self.assertEqual(source.count("if (!http) {\n        return ESP_ERR_INVALID_ARG;"), 2)
        saved = source.split("std::string Ota::GetCheckVersionUrl()", 1)[1].split("return url;", 1)[
            0
        ]
        self.assertIn("kHensunIdentityBuild && !HensunSameBootstrapOrigin", saved)
        self.assertIn("url = CONFIG_OTA_URL;", saved)

    def test_hensun_ui_and_overlay_are_gated_while_upstream_remains_available(self):
        source = (ROOT / "main/boards/common/wifi_board.cc").read_text(encoding="utf-8")
        guard = source.split("config.show_ota_config = false;", 1)[0].rsplit("#if", 1)[1]
        for board in ("HENSUN_CAM_PILOT_V1", "HENSUN_DESK_V1", "HENSUN_NOCAM_PILOT_V1"):
            self.assertIn(board, guard)
        self.assertIn("#else\n    config.show_ota_config = true;", source)
        top = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertLess(
            top.index("project(xiaozhi)"), top.index("include(cmake/hensun_wifi_security.cmake)")
        )

    def test_current_wifi_http_transport_does_not_follow_redirects(self):
        vendor = ROOT / "managed_components/78__esp-ml307"
        if not vendor.is_dir():
            self.skipTest("installed Wi-Fi HTTP transport is unavailable")
        factory = (vendor / "src/esp/esp_network.cc").read_text(encoding="utf-8")
        self.assertIn("std::make_unique<HttpClient>(this, connect_id)", factory)
        source = (vendor / "src/http_client.cc").read_text(encoding="utf-8")
        opened = source.split("bool HttpClient::Open(", 1)[1].split("void HttpClient::Close()", 1)[
            0
        ]
        self.assertEqual(opened.count("tcp_->Send(http_request)"), 1)
        self.assertNotIn("location", source.lower())
        self.assertNotIn("redirect", source.lower())
        ota = (ROOT / "main/ota.cc").read_text(encoding="utf-8")
        self.assertIn("if (status_code != 200)", ota)


if __name__ == "__main__":
    unittest.main()
