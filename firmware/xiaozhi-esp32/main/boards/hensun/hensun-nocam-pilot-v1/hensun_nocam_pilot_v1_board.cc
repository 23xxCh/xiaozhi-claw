#include "wifi_board.h"
#include "codecs/no_audio_codec.h"
#include "display/lcd_display.h"
#include "application.h"
#include "assets/lang_config.h"
#include "button.h"
#include "config.h"
#include "led/single_led.h"

#include <driver/spi_common.h>
#include <esp_lcd_panel_io.h>
#include <esp_lcd_panel_ops.h>
#include <esp_lcd_panel_vendor.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <src/libs/qrcode/lv_qrcode.h>

#include <cstring>
#include <vector>

#define TAG "HensunNoCamPilotV1Board"

namespace {

constexpr int kHensunNoCamMicInputGain = 4;

class HensunNoCamAudioCodecSimplex final : public NoAudioCodecSimplex {
public:
    using NoAudioCodecSimplex::NoAudioCodecSimplex;

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
            // The board's 24-bit I2S microphone is left-aligned in its 32-bit
            // slot. Apply a bounded 4x board-local gain so quiet speech reaches
            // MultiNet without returning to the clipping-prone generic 16x path.
            int32_t value = (bit32_buffer[index] >> 16) * kHensunNoCamMicInputGain;
            if (value > INT16_MAX) {
                value = INT16_MAX;
            } else if (value < -INT16_MAX) {
                value = -INT16_MAX;
            }
            dest[index] = static_cast<int16_t>(value);
            const int32_t abs_value = value < 0 ? -value : value;
            abs_sum += static_cast<uint32_t>(abs_value);
            if (abs_value > peak) {
                peak = abs_value;
            }
        }

        static int64_t last_level_log_us = 0;
        const int64_t now_us = esp_timer_get_time();
        if (samples > 0 && now_us - last_level_log_us >= 2 * 1000 * 1000) {
            last_level_log_us = now_us;
            ESP_LOGI(TAG, "Mic input level: avg_abs=%lu peak=%ld samples=%d gain=%d",
                     static_cast<unsigned long>(abs_sum / samples),
                     static_cast<long>(peak), samples, kHensunNoCamMicInputGain);
        }
        return samples;
    }
};

struct EmotionRoute {
    const char* input;
    const char* asset;
};

constexpr EmotionRoute kEmotionRoutes[] = {
    {"neutral", "neutral"},
    {"robot_2", "neutral"},
    {"idle", "sleepy"},
    {"link", "neutral"},
    {"listening", "surprised"},
    {"speaking", "neutral"},
    {"relaxed", "neutral"},
    {"happy", "silly"},
    {"laughing", "silly"},
    {"silly", "silly"},
    {"funny", "silly"},
    {"caring", "caring"},
    {"affectionate", "caring"},
    {"curious", "confused"},
    {"confused", "confused"},
    {"thinking", "confused"},
    {"surprised", "surprised"},
    {"wake", "surprised"},
    {"concerned", "sad"},
    {"worried", "sad"},
    {"apology", "sad"},
    {"safe_block", "sad"},
    {"apologetic", "sad"},
    {"sad", "sad"},
    {"crying", "sad"},
    {"shy", "shy"},
    {"embarrassed", "shy"},
    {"angry", "angry"},
    {"sleepy", "sleepy"},
    {"tired", "sleepy"},
};

const char* StandardAssetForEmotion(const char* emotion) {
    if (emotion != nullptr) {
        for (const auto& route : kEmotionRoutes) {
            if (std::strcmp(emotion, route.input) == 0) {
                return route.asset;
            }
        }
    }
    return "neutral";
}

class HensunNoCamDisplay final : public SpiLcdDisplay {
private:
    lv_obj_t* activation_overlay_ = nullptr;
    const char* current_asset_ = nullptr;

    void ClearActivationOverlay() {
        DisplayLockGuard lock(this);
        if (activation_overlay_ != nullptr && lv_obj_is_valid(activation_overlay_)) {
            lv_obj_del(activation_overlay_);
        }
        activation_overlay_ = nullptr;
    }

    void ApplyFullScreenOverlayStyle() {
        lv_obj_set_style_bg_opa(top_bar_, LV_OPA_TRANSP, 0);
        lv_obj_set_style_bg_opa(bottom_bar_, LV_OPA_TRANSP, 0);

        lv_obj_set_style_text_color(network_label_, lv_color_white(), 0);
        lv_obj_set_style_text_color(status_label_, lv_color_white(), 0);
        lv_obj_set_style_text_color(notification_label_, lv_color_white(), 0);
        lv_obj_set_style_text_color(mute_label_, lv_color_white(), 0);
        lv_obj_set_style_text_color(battery_label_, lv_color_white(), 0);
        lv_obj_set_style_text_color(chat_message_label_, lv_color_white(), 0);
    }

public:
    using SpiLcdDisplay::SpiLcdDisplay;

    void SetupUI() override {
        current_asset_ = nullptr;
        SpiLcdDisplay::SetupUI();
        DisplayLockGuard lock(this);
        ApplyFullScreenOverlayStyle();
    }

    void SetTheme(Theme* theme) override {
        current_asset_ = nullptr;
        SpiLcdDisplay::SetTheme(theme);
        DisplayLockGuard lock(this);
        ApplyFullScreenOverlayStyle();
    }

    void SetEmotion(const char* emotion) override {
        DisplayLockGuard lock(this);
        ClearActivationOverlay();
        const char* asset = StandardAssetForEmotion(emotion);
        // Aliases can select the same GIF. Keep its current frame instead of
        // destroying and restarting the animation on every repeated event.
        if (current_asset_ != nullptr && std::strcmp(current_asset_, asset) == 0 &&
            gif_controller_ != nullptr && gif_controller_->IsLoaded() &&
            emoji_image_ != nullptr && !lv_obj_has_flag(emoji_image_, LV_OBJ_FLAG_HIDDEN)) {
            return;
        }
        ESP_LOGI(TAG, "Display emotion route: requested=%s asset=%s",
                 emotion == nullptr ? "" : emotion, asset);
        SpiLcdDisplay::SetEmotion(asset);
        current_asset_ = asset;
    }

    void ShowActivationCode(const char* code, const char* claim_url) override {
        if (code == nullptr || std::strlen(code) != 6) {
            return;
        }
        std::string qr_url = claim_url == nullptr ? "" : claim_url;
        if (qr_url.empty()) {
            qr_url = "https://staging.hensun-desk.top/claim#code=";
            qr_url += code;
        }

        DisplayLockGuard lock(this);
        if (activation_overlay_ != nullptr && lv_obj_is_valid(activation_overlay_)) {
            lv_obj_del(activation_overlay_);
        }
        activation_overlay_ = lv_obj_create(lv_screen_active());
        lv_obj_remove_style_all(activation_overlay_);
        lv_obj_set_size(activation_overlay_, LV_HOR_RES, LV_VER_RES);
        lv_obj_set_style_bg_color(activation_overlay_, lv_color_hex(0x101318), 0);
        lv_obj_set_style_bg_opa(activation_overlay_, LV_OPA_COVER, 0);
        lv_obj_set_scrollbar_mode(activation_overlay_, LV_SCROLLBAR_MODE_OFF);
        lv_obj_move_foreground(activation_overlay_);

        lv_obj_t* qr = lv_qrcode_create(activation_overlay_);
        lv_qrcode_set_size(qr, 172);
        lv_qrcode_set_dark_color(qr, lv_color_black());
        lv_qrcode_set_light_color(qr, lv_color_white());
        lv_qrcode_set_quiet_zone(qr, true);
        lv_qrcode_set_data(qr, qr_url.c_str());
        lv_obj_align(qr, LV_ALIGN_LEFT_MID, 10, 0);

        lv_obj_t* title = lv_label_create(activation_overlay_);
        lv_label_set_text(title, "扫码绑定");
        lv_obj_set_style_text_color(title, lv_color_white(), 0);
        lv_obj_align(title, LV_ALIGN_TOP_RIGHT, -19, 49);

        lv_obj_t* code_label = lv_label_create(activation_overlay_);
        lv_label_set_text(code_label, code);
        lv_obj_set_style_text_color(code_label, lv_color_hex(0x31DDE7), 0);
        lv_obj_set_style_text_letter_space(code_label, 2, 0);
        lv_obj_align(code_label, LV_ALIGN_RIGHT_MID, -15, 0);

        lv_obj_t* hint = lv_label_create(activation_overlay_);
        lv_label_set_text(hint, "备用码");
        lv_obj_set_style_text_color(hint, lv_color_hex(0xB7BEC9), 0);
        lv_obj_align(hint, LV_ALIGN_BOTTOM_RIGHT, -35, -53);
    }
};

}  // namespace

class HensunNoCamPilotV1Board : public WifiBoard {
private:
    Button boot_button_;
    Button volume_up_button_;
    Button volume_down_button_;
    LcdDisplay* display_ = nullptr;

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
        ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, true));

        display_ = new HensunNoCamDisplay(
            panel_io,
            panel,
            DISPLAY_WIDTH,
            DISPLAY_HEIGHT,
            DISPLAY_OFFSET_X,
            DISPLAY_OFFSET_Y,
            DISPLAY_MIRROR_X,
            DISPLAY_MIRROR_Y,
            DISPLAY_SWAP_XY);
    }

    void InitializeButtons() {
        boot_button_.OnClick([this]() {
            auto& app = Application::GetInstance();
            if (app.GetDeviceState() == kDeviceStateStarting) {
                EnterWifiConfigMode();
                return;
            }
            app.ToggleChatState();
        });
        boot_button_.OnLongPress([this]() {
            EnterWifiConfigMode();
        });

        volume_up_button_.OnClick([this]() {
            auto* codec = GetAudioCodec();
            auto volume = codec->output_volume() + 10;
            if (volume > 100) {
                volume = 100;
            }
            codec->SetOutputVolume(volume);
            GetDisplay()->ShowNotification(Lang::Strings::VOLUME + std::to_string(volume));
        });
        volume_up_button_.OnLongPress([this]() {
            GetAudioCodec()->SetOutputVolume(100);
            GetDisplay()->ShowNotification(Lang::Strings::MAX_VOLUME);
        });

        volume_down_button_.OnClick([this]() {
            auto* codec = GetAudioCodec();
            auto volume = codec->output_volume() - 10;
            if (volume < 0) {
                volume = 0;
            }
            codec->SetOutputVolume(volume);
            GetDisplay()->ShowNotification(Lang::Strings::VOLUME + std::to_string(volume));
        });
        volume_down_button_.OnLongPress([this]() {
            GetAudioCodec()->SetOutputVolume(0);
            GetDisplay()->ShowNotification(Lang::Strings::MUTED);
        });
    }

public:
    HensunNoCamPilotV1Board()
        : boot_button_(BOOT_BUTTON_GPIO),
          volume_up_button_(VOLUME_UP_BUTTON_GPIO),
          volume_down_button_(VOLUME_DOWN_BUTTON_GPIO) {
        InitializeSpi();
        InitializeDisplay();
        InitializeButtons();
        GetBacklight()->RestoreBrightness();
    }

    Led* GetLed() override {
        static SingleLed led(BUILTIN_LED_GPIO);
        return &led;
    }

    AudioCodec* GetAudioCodec() override {
        static HensunNoCamAudioCodecSimplex audio_codec(
            AUDIO_INPUT_SAMPLE_RATE,
            AUDIO_OUTPUT_SAMPLE_RATE,
            AUDIO_I2S_SPK_GPIO_BCLK,
            AUDIO_I2S_SPK_GPIO_LRCK,
            AUDIO_I2S_SPK_GPIO_DOUT,
            AUDIO_I2S_MIC_GPIO_SCK,
            AUDIO_I2S_MIC_GPIO_WS,
            AUDIO_I2S_MIC_GPIO_DIN);
        return &audio_codec;
    }

    Display* GetDisplay() override {
        return display_;
    }

    Backlight* GetBacklight() override {
        static PwmBacklight backlight(
            DISPLAY_BACKLIGHT_PIN,
            DISPLAY_BACKLIGHT_OUTPUT_INVERT);
        return &backlight;
    }
};

DECLARE_BOARD(HensunNoCamPilotV1Board);
