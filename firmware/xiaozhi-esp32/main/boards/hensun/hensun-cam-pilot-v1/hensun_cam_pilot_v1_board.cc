#include "wifi_board.h"
#include "codecs/no_audio_codec.h"
#if CONFIG_USE_EMOTE_MESSAGE_STYLE
#include "hensun_emote_lab_display.h"
#else
#include "hensun_face_display.h"
#endif
#include "application.h"
#include "button.h"
#include "config.h"
#include "esp32_camera.h"
#include "hensun_panel.h"

#include <esp_log.h>

#define TAG "HensunCamPilotV1Board"

namespace {

#if CONFIG_USE_EMOTE_MESSAGE_STYLE
using HensunPilotDisplay = HensunEmoteLabDisplay;
#else
using HensunPilotDisplay = HensunFaceDisplay;
#endif

class HensunAudioCodecSimplex final : public NoAudioCodecSimplex {
public:
    using NoAudioCodecSimplex::NoAudioCodecSimplex;

    void SetDisplay(HensunPilotDisplay* display) {
        display_ = display;
    }

    void OutputData(std::vector<int16_t>& data) override {
        if (display_ != nullptr) {
            display_->SetSpeechPcm(data);
        }

        AudioCodec::OutputData(data);
    }

protected:
    int Read(int16_t* dest, int samples) override {
        size_t bytes_read = 0;
        constexpr uint32_t kReadTimeoutMs = 200;
        std::vector<int32_t> bit32_buffer(samples);
        if (i2s_channel_read(rx_handle_, bit32_buffer.data(),
                             samples * sizeof(int32_t), &bytes_read,
                             kReadTimeoutMs) != ESP_OK) {
            return 0;
        }

        samples = bytes_read / sizeof(int32_t);
        for (int index = 0; index < samples; ++index) {
            // The pilot board's 24-bit I2S microphone is left-aligned in the
            // 32-bit slot. Keep the upper 16 bits; the generic >>12 path adds
            // 16x gain and clips most of this microphone's samples.
            dest[index] = static_cast<int16_t>(bit32_buffer[index] >> 16);
        }
        return samples;
    }

private:
    HensunPilotDisplay* display_ = nullptr;
};

}  // namespace

class HensunCamPilotV1Board : public WifiBoard {
private:
    Button boot_button_;
    HensunPanel* panel_ = nullptr;
    HensunPilotDisplay* display_ = nullptr;
    Esp32Camera* camera_ = nullptr;

    void InitializeDisplay() {
        panel_ = new HensunPanel(DISPLAY_WIDTH, DISPLAY_HEIGHT);
#if CONFIG_USE_EMOTE_MESSAGE_STYLE
        display_ = new HensunEmoteLabDisplay(*panel_, DISPLAY_WIDTH, DISPLAY_HEIGHT);
#else
        display_ = new HensunFaceDisplay(panel_->io(), panel_->panel(),
            DISPLAY_WIDTH, DISPLAY_HEIGHT,
            DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y,
            DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY);
#endif
    }

    void InitializeButtons() {
        boot_button_.OnPressDown([]() {
            ESP_LOGI(TAG, "BOOT press down");
        });
        boot_button_.OnPressUp([]() {
            ESP_LOGI(TAG, "BOOT press up");
        });
        boot_button_.OnClick([this]() {
            auto& app = Application::GetInstance();
            const auto state = app.GetDeviceState();
            ESP_LOGI(TAG, "BOOT single click (state=%d)", state);
            if (state == kDeviceStateStarting ||
                state == kDeviceStateWifiConfiguring ||
                state == kDeviceStateAudioTesting) {
                ESP_LOGW(TAG, "Ignoring chat button while device is not ready (state=%d)", state);
                return;
            }
            if (state == kDeviceStateSpeaking) {
                display_->SetEmotion("interrupted");
            }
            app.ToggleChatState();
        });
        boot_button_.OnLongPress([this]() {
            ESP_LOGI(TAG, "BOOT long press");
#ifdef CONFIG_USE_EMOTE_MESSAGE_STYLE
            display_->StartShowcase();
#else
            EnterWifiConfigMode();
#endif
        });
        boot_button_.OnDoubleClick([this]() {
            ESP_LOGI(TAG, "BOOT double click");
            display_->StartShowcase();
        });
    }

    void InitializeCamera() {
        camera_config_t camera_config = {};
        camera_config.pin_d0 = CAMERA_PIN_D0;
        camera_config.pin_d1 = CAMERA_PIN_D1;
        camera_config.pin_d2 = CAMERA_PIN_D2;
        camera_config.pin_d3 = CAMERA_PIN_D3;
        camera_config.pin_d4 = CAMERA_PIN_D4;
        camera_config.pin_d5 = CAMERA_PIN_D5;
        camera_config.pin_d6 = CAMERA_PIN_D6;
        camera_config.pin_d7 = CAMERA_PIN_D7;
        camera_config.pin_xclk = CAMERA_PIN_XCLK;
        camera_config.pin_pclk = CAMERA_PIN_PCLK;
        camera_config.pin_vsync = CAMERA_PIN_VSYNC;
        camera_config.pin_href = CAMERA_PIN_HREF;
        camera_config.pin_sccb_sda = CAMERA_PIN_SIOD;
        camera_config.pin_sccb_scl = CAMERA_PIN_SIOC;
        camera_config.sccb_i2c_port = 0;
        camera_config.pin_pwdn = CAMERA_PIN_PWDN;
        camera_config.pin_reset = CAMERA_PIN_RESET;
        camera_config.xclk_freq_hz = CAMERA_XCLK_HZ;
        camera_config.pixel_format = PIXFORMAT_RGB565;
        camera_config.frame_size = FRAMESIZE_QVGA;
        camera_config.jpeg_quality = 12;
        camera_config.fb_count = 1;
        camera_config.fb_location = CAMERA_FB_IN_PSRAM;
        camera_config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

        // Cloud vision is disabled for the pilot. Keep the sensor off while the
        // face is idle so camera DMA/interrupts cannot starve the display; the
        // first explicit Capture() initializes it on demand.
        camera_ = new Esp32Camera(camera_config, true);
        camera_->SetHMirror(false);
        camera_->SetVFlip(true);
    }

public:
    HensunCamPilotV1Board() : boot_button_(BOOT_BUTTON_GPIO) {
        InitializeDisplay();
        InitializeButtons();
        InitializeCamera();
        GetBacklight()->RestoreBrightness();
        ESP_LOGI(TAG, "Battery and power-save functions are disabled in the pilot build");
    }

    AudioCodec* GetAudioCodec() override {
        static HensunAudioCodecSimplex audio_codec(
            AUDIO_INPUT_SAMPLE_RATE,
            AUDIO_OUTPUT_SAMPLE_RATE,
            AUDIO_I2S_SPK_GPIO_BCLK,
            AUDIO_I2S_SPK_GPIO_LRCK,
            AUDIO_I2S_SPK_GPIO_DOUT,
            AUDIO_I2S_MIC_GPIO_SCK,
            AUDIO_I2S_MIC_GPIO_WS,
            AUDIO_I2S_MIC_GPIO_DIN);
        audio_codec.SetDisplay(display_);
        return &audio_codec;
    }

    Display* GetDisplay() override {
        return display_;
    }

    Backlight* GetBacklight() override {
        static PwmBacklight backlight(
            DISPLAY_BACKLIGHT_PIN, DISPLAY_BACKLIGHT_OUTPUT_INVERT);
        return &backlight;
    }

    Camera* GetCamera() override {
        return camera_;
    }
};

DECLARE_BOARD(HensunCamPilotV1Board);
