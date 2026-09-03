#include "wifi_board.h"
#include "codecs/no_audio_codec.h"
#include "device_ws_contract_generated.h"
#if CONFIG_USE_EMOTE_MESSAGE_STYLE
#include "hensun_emote_lab_display.h"
#else
#include "hensun_face_display.h"
#endif
#include "application.h"
#include "button.h"
#include "config.h"
#include "esp32_camera.h"

#include <driver/spi_common.h>
#include <esp_lcd_panel_io.h>
#include <esp_lcd_panel_ops.h>
#include <esp_lcd_panel_vendor.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <memory>
#include <stdexcept>
#include <string>

#include <algorithm>

#define TAG "HensunCamPilotV1Board"

namespace {

static_assert(hensun::device_ws::kProtocolVersion == 1);

#if CONFIG_USE_EMOTE_MESSAGE_STYLE
using HensunPilotDisplay = HensunEmoteLabDisplay;
#else
using HensunPilotDisplay = HensunFaceDisplay;
#endif

constexpr size_t kSpeechPcmSampleStride = 8;
constexpr uint32_t kSpeechNoiseFloor = 180;
constexpr uint32_t kSpeechReferenceAmplitude = 5000;
constexpr int kHensunMicInputGain = 1;

class HensunAudioCodecSimplex final : public NoAudioCodecSimplex {
public:
    using NoAudioCodecSimplex::NoAudioCodecSimplex;

    void SetDisplay(HensunPilotDisplay* display) {
        display_ = display;
    }

    void OutputData(std::vector<int16_t>& data) override {
        uint64_t amplitude_sum = 0;
        size_t sample_count = 0;
        for (size_t index = 0; index < data.size(); index += kSpeechPcmSampleStride) {
            const int32_t sample = data[index];
            amplitude_sum += static_cast<uint32_t>(sample < 0 ? -sample : sample);
            ++sample_count;
        }

        uint8_t level = 0;
        if (sample_count > 0) {
            const uint32_t mean_amplitude = static_cast<uint32_t>(amplitude_sum / sample_count);
            if (mean_amplitude > kSpeechNoiseFloor) {
                const uint32_t scaled =
                    (mean_amplitude - kSpeechNoiseFloor) * 100 /
                    (kSpeechReferenceAmplitude - kSpeechNoiseFloor);
                level = static_cast<uint8_t>(std::min<uint32_t>(100, scaled));
            }
        }
        if (display_ != nullptr) {
            display_->SetSpeechLevel(level);
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
        uint64_t abs_sum = 0;
        int32_t peak = 0;
        for (int index = 0; index < samples; ++index) {
            // The pilot board's 24-bit I2S microphone is left-aligned in the
            // 32-bit slot. Keep the upper 16 bits, then apply a small
            // board-local gain for wake-word sensitivity. The generic >>12
            // path adds 16x gain and clips most of this microphone's samples.
            int32_t value = (bit32_buffer[index] >> 16) * kHensunMicInputGain;
            if (value > INT16_MAX) {
                value = INT16_MAX;
            } else if (value < -INT16_MAX) {
                value = -INT16_MAX;
            }
            dest[index] = static_cast<int16_t>(value);
            const int32_t abs_value = value < 0 ? -value : value;
            abs_sum += static_cast<uint32_t>(abs_value);
            peak = std::max(peak, abs_value);
        }
        static int64_t last_level_log_us = 0;
        const int64_t now_us = esp_timer_get_time();
        if (samples > 0 && now_us - last_level_log_us >= 2 * 1000 * 1000) {
            last_level_log_us = now_us;
            ESP_LOGI(TAG,
                "Mic input level: avg_abs=%lu peak=%ld samples=%d gain=%d "
                "gpio0=%d gpio3=%d gpio14=%d gpio46=%d gpio48=%d",
                static_cast<unsigned long>(abs_sum / samples),
                static_cast<long>(peak), samples, kHensunMicInputGain,
                gpio_get_level(GPIO_NUM_0),
                gpio_get_level(GPIO_NUM_3),
                gpio_get_level(GPIO_NUM_14),
                gpio_get_level(GPIO_NUM_46),
                gpio_get_level(GPIO_NUM_48));
        }
        return samples;
    }

private:
    HensunPilotDisplay* display_ = nullptr;
};

// The ESP32 camera driver starts continuous DMA capture in its constructor.
// Keeping it active while the product is only listening produces VSYNC buffer
// overruns and competes with the realtime audio path. Preserve camera support,
// but defer that work until a photo is explicitly requested.
class HensunLazyCamera final : public Camera {
public:
    explicit HensunLazyCamera(const camera_config_t& config) : config_(config) {}

    void SetExplainUrl(const std::string& url, const std::string& token) override {
        explain_url_ = url;
        explain_token_ = token;
        if (camera_ != nullptr) {
            camera_->SetExplainUrl(url, token);
        }
    }

    bool Capture() override {
        return EnsureCamera() && camera_->Capture();
    }

    bool SetHMirror(bool enabled) override {
        hmirror_ = enabled;
        return camera_ == nullptr || camera_->SetHMirror(enabled);
    }

    bool SetVFlip(bool enabled) override {
        vflip_ = enabled;
        return camera_ == nullptr || camera_->SetVFlip(enabled);
    }

    bool SetSwapBytes(bool enabled) override {
        swap_bytes_ = enabled;
        return camera_ == nullptr || camera_->SetSwapBytes(enabled);
    }

    std::string Explain(const std::string& question) override {
        if (camera_ == nullptr) {
            throw std::runtime_error("No camera frame captured");
        }
        return camera_->Explain(question);
    }

private:
    bool EnsureCamera() {
        if (camera_ != nullptr) {
            return true;
        }

        ESP_LOGI("HensunCamera", "Starting camera for an explicit capture request");
        camera_ = std::make_unique<Esp32Camera>(config_);
        camera_->SetHMirror(hmirror_);
        camera_->SetVFlip(vflip_);
        camera_->SetSwapBytes(swap_bytes_);
        camera_->SetExplainUrl(explain_url_, explain_token_);
        return true;
    }

    camera_config_t config_;
    std::unique_ptr<Esp32Camera> camera_;
    std::string explain_url_;
    std::string explain_token_;
    bool hmirror_ = false;
    bool vflip_ = false;
    bool swap_bytes_ = true;
};

}  // namespace

class HensunCamPilotV1Board : public WifiBoard {
private:
    Button boot_button_;
    HensunPilotDisplay* display_ = nullptr;
    Camera* camera_ = nullptr;

    void InitializeSpi() {
        spi_bus_config_t bus_config = {};
        bus_config.mosi_io_num = DISPLAY_MOSI_PIN;
        bus_config.miso_io_num = GPIO_NUM_NC;
        bus_config.sclk_io_num = DISPLAY_CLK_PIN;
        bus_config.quadwp_io_num = GPIO_NUM_NC;
        bus_config.quadhd_io_num = GPIO_NUM_NC;
        bus_config.max_transfer_sz = DISPLAY_WIDTH * DISPLAY_HEIGHT * sizeof(uint16_t);
        ESP_ERROR_CHECK(spi_bus_initialize(SPI3_HOST, &bus_config, SPI_DMA_CH_AUTO));
    }

    void InitializeDisplay() {
        esp_lcd_panel_io_handle_t panel_io = nullptr;
        esp_lcd_panel_handle_t panel = nullptr;

        esp_lcd_panel_io_spi_config_t io_config = {};
        io_config.cs_gpio_num = DISPLAY_CS_PIN;
        io_config.dc_gpio_num = DISPLAY_DC_PIN;
        io_config.spi_mode = DISPLAY_SPI_MODE;
        io_config.pclk_hz = 40 * 1000 * 1000;
        io_config.trans_queue_depth = 10;
        io_config.lcd_cmd_bits = 8;
        io_config.lcd_param_bits = 8;
        ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(SPI3_HOST, &io_config, &panel_io));

        esp_lcd_panel_dev_config_t panel_config = {};
        panel_config.reset_gpio_num = DISPLAY_RST_PIN;
        panel_config.rgb_ele_order = DISPLAY_RGB_ORDER;
        panel_config.bits_per_pixel = 16;
        ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(panel_io, &panel_config, &panel));

        ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));
        ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
        ESP_ERROR_CHECK(esp_lcd_panel_invert_color(panel, DISPLAY_INVERT_COLOR));
        ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(panel, DISPLAY_SWAP_XY));
        ESP_ERROR_CHECK(esp_lcd_panel_mirror(panel, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y));

        esp_lcd_panel_disp_on_off(panel, true);
#if CONFIG_USE_EMOTE_MESSAGE_STYLE
        display_ = new HensunEmoteLabDisplay(panel_io, panel, DISPLAY_WIDTH, DISPLAY_HEIGHT);
#else
        display_ = new HensunFaceDisplay(panel_io, panel,
            DISPLAY_WIDTH, DISPLAY_HEIGHT,
            DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y,
            DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY);
#endif
    }

    void InitializeButtons() {
        boot_button_.OnClick([this]() {
            auto& app = Application::GetInstance();
            ESP_LOGI(TAG, "BOOT click received, state=%d",
                static_cast<int>(app.GetDeviceState()));
            if (app.GetDeviceState() == kDeviceStateStarting) {
                ESP_LOGW(TAG, "Ignoring BOOT click while device is starting");
                return;
            }
            if (app.GetDeviceState() == kDeviceStateSpeaking) {
                display_->SetEmotion("interrupted");
            }
            app.ToggleChatState();
        });
        boot_button_.OnLongPress([this]() {
            ESP_LOGI(TAG, "BOOT long press: entering Wi-Fi configuration");
            EnterWifiConfigMode();
        });
        boot_button_.OnDoubleClick([this]() {
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

        camera_ = new HensunLazyCamera(camera_config);
        camera_->SetHMirror(false);
        camera_->SetVFlip(true);
    }

public:
    HensunCamPilotV1Board() : boot_button_(BOOT_BUTTON_GPIO) {
        InitializeSpi();
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
