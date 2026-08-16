import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class HensunStandbyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = (ROOT / "main/application.cc").read_text(encoding="utf-8")
        cls.application_header = (ROOT / "main/application.h").read_text(
            encoding="utf-8"
        )
        cls.board = (
            ROOT
            / "main/boards/hensun/hensun-cam-pilot-v1/hensun_cam_pilot_v1_board.cc"
        ).read_text(encoding="utf-8")
        cls.protocol_header = (ROOT / "main/protocols/protocol.h").read_text(
            encoding="utf-8"
        )
        cls.protocol_source = (ROOT / "main/protocols/protocol.cc").read_text(
            encoding="utf-8"
        )

    def test_idle_window_enters_soft_standby_only_before_user_speech(self) -> None:
        self.assertIn("conversation_idle_timeout_seconds_", self.application_header)
        self.assertIn("conversation_idle_timeout_armed_", self.application_header)
        self.assertIn("listening_idle_ticks_", self.application_header)
        self.assertIn("listening_capture_active_", self.application_header)
        self.assertIn('EnterStandby("idle-timeout")', self.application)
        self.assertIn("!vad_speech_detected_", self.application)
        self.assertIn(
            "conversation_idle_timeout_armed_ && !vad_speech_detected_",
            self.application,
        )
        self.assertIn(
            "conversation_idle_timeout_armed_ = true;",
            self.application,
        )
        self.assertIn(
            "conversation_idle_timeout_armed_ = false;",
            self.application,
        )
        self.assertIn("kInitialListeningTimeoutSeconds", self.application)

        start = re.search(
            r"void Application::StartListeningAudio\(\) \{(.*?)\n\}",
            self.application,
            re.DOTALL,
        )
        self.assertIsNotNone(start)
        self.assertIn("listening_idle_ticks_ = 0", start.group(1))
        self.assertIn("listening_capture_active_ = true", start.group(1))

    def test_boot_click_wakes_idle_and_stops_active_chat(self) -> None:
        click = re.search(
            r"boot_button_\.OnClick\(\[this\]\(\) \{(.*?)\n\s*\}\);",
            self.board,
            re.DOTALL,
        )
        self.assertIsNotNone(click)
        body = click.group(1)
        self.assertIn("state == kDeviceStateIdle", body)
        self.assertIn("app.ToggleChatState()", body)
        self.assertIn('app.EnterStandby("button")', body)

    def test_standby_acknowledges_before_closing_the_channel(self) -> None:
        self.assertIn("EnterStandby", self.application_header)
        self.assertIn("MAIN_EVENT_ENTER_STANDBY", self.application_header)
        self.assertIn("SendDeviceState", self.protocol_header)
        self.assertIn("SendDeviceCommandAck", self.protocol_header)
        self.assertIn('"device_state"', self.protocol_source)
        self.assertIn('"device_command_ack"', self.protocol_source)

        handler = re.search(
            r"void Application::HandleEnterStandbyEvent\(\) \{(.*?)\n\}",
            self.application,
            re.DOTALL,
        )
        self.assertIsNotNone(handler)
        body = handler.group(1)
        self.assertLess(body.index("SendDeviceState"), body.index("CloseAudioChannel"))
        self.assertLess(body.index("SendDeviceCommandAck"), body.index("CloseAudioChannel"))
        self.assertIn('display->SetEmotion("sleep")', body)

    def test_active_runtime_states_are_reported_by_the_device(self) -> None:
        self.assertIn('protocol_->SendDeviceState("listening")', self.application)
        self.assertIn('protocol_->SendDeviceState("speaking")', self.application)


if __name__ == "__main__":
    unittest.main()
