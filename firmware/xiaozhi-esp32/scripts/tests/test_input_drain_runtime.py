"""Compile the production stop/drain methods with host hardware doubles.

This executes C++ synchronization and packet ordering, not source-text assertions.
Use CXX='g++' or CXX='path/to/zig.exe c++'; requires a host C++17 compiler.
"""
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def method(path, signature):
    source = (ROOT / path).read_text(encoding="utf-8")
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def test_production_input_drain_and_stop_order(tmp_path):
    compiler = shlex.split(os.environ.get("CXX", "g++"))
    if not shutil.which(compiler[0]):
        pytest.fail("Host C++ compiler required; set CXX (this check must not silently skip)")
    harness = (Path(__file__).with_name("input_drain_runtime.cc")).read_text(encoding="utf-8")
    methods = "\n".join([
        method("main/audio/audio_service.cc", "bool AudioService::FinishVoiceInput()"),
        method("main/application.cc", "void Application::HandleStopListeningEvent()"),
        method("main/application.cc",
               "void Application::HandleVadChange(bool speaking, uint32_t generation)"),
        method("main/audio/engines/lite_audio_engine.cc",
               "void LiteAudioEngine::FinishVoiceProcessing()"),
        method("main/audio/engines/afe_audio_engine.cc",
               "void AfeAudioEngine::FinishVoiceProcessing()"),
    ])
    source = tmp_path / "drain.cc"
    source.write_text(harness.replace("// PRODUCTION_METHODS", methods), encoding="utf-8")
    binary = tmp_path / ("drain.exe" if os.name == "nt" else "drain")
    subprocess.run(
        [*compiler, "-std=c++17", "-pthread", str(source), "-o", str(binary)],
        check=True, timeout=180,
    )
    subprocess.run([str(binary)], check=True, timeout=15)

@pytest.mark.parametrize("board", ["HENSUN_CAM_PILOT_V1", "HENSUN_NOCAM_PILOT_V1"])
def test_half_duplex_board_cannot_wake_on_own_reply(tmp_path, board):
    source = (ROOT / "main/application.cc").read_text(encoding="utf-8")
    speaking = source.split("case kDeviceStateSpeaking:", 1)[1].split("case kDeviceStateWifiConfiguring:", 1)[0]
    branch = speaking[speaking.index("#if"):speaking.index("#endif") + len("#endif")]
    program = '''#include <cassert>
struct Audio { bool wake = true; void EnableWakeWordDetection(bool enabled) { wake = enabled; }
bool IsAfeWakeWord() { return true; } };
int main() { Audio audio_service_;
''' + branch + '\nassert(!audio_service_.wake); }\n'
    compiler = shlex.split(os.environ.get("CXX", "g++"))
    src = tmp_path / "speaking.cc"
    binary = tmp_path / "speaking.exe"
    src.write_text(program, encoding="utf-8")
    subprocess.run([*compiler, "-std=c++17", "-DCONFIG_BOARD_TYPE_" + board + "=1",
                    str(src), "-o", str(binary)], check=True, timeout=180)
    subprocess.run([str(binary)], check=True, timeout=15)
