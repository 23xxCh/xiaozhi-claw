#include "hensun_panel.h"

#include "config.h"

#include <driver/spi_common.h>
#include <esp_err.h>
#include <esp_lcd_panel_vendor.h>
#include <esp_log.h>

namespace {
constexpr char kTag[] = "HensunPanel";
}

HensunPanel* HensunPanel::active_panel_ = nullptr;

HensunPanel::HensunPanel(int width, int height) : width_(width), height_(height) {
    spi_bus_config_t bus_config = {};
    bus_config.mosi_io_num = DISPLAY_MOSI_PIN;
    bus_config.miso_io_num = GPIO_NUM_NC;
    bus_config.sclk_io_num = DISPLAY_CLK_PIN;
    bus_config.quadwp_io_num = GPIO_NUM_NC;
    bus_config.quadhd_io_num = GPIO_NUM_NC;
    bus_config.max_transfer_sz = width_ * height_ * sizeof(uint16_t);
    ESP_ERROR_CHECK(
        spi_bus_initialize(DISPLAY_SPI_HOST, &bus_config, SPI_DMA_CH_AUTO));

    esp_lcd_panel_io_spi_config_t io_config = {};
    io_config.cs_gpio_num = DISPLAY_CS_PIN;
    io_config.dc_gpio_num = DISPLAY_DC_PIN;
    io_config.spi_mode = DISPLAY_SPI_MODE;
    io_config.pclk_hz = DISPLAY_SPI_FREQUENCY_HZ;
    io_config.trans_queue_depth = DISPLAY_SPI_QUEUE_DEPTH;
    io_config.lcd_cmd_bits = 8;
    io_config.lcd_param_bits = 8;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(
        DISPLAY_SPI_HOST, &io_config, &panel_io_));

    esp_lcd_panel_dev_config_t panel_config = {};
    panel_config.reset_gpio_num = DISPLAY_RST_PIN;
    panel_config.rgb_ele_order = DISPLAY_RGB_ORDER;
    panel_config.bits_per_pixel = 16;
    ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(panel_io_, &panel_config, &panel_));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel_));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel_));
    ESP_ERROR_CHECK(esp_lcd_panel_invert_color(panel_, DISPLAY_INVERT_COLOR));
    ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(panel_, DISPLAY_SWAP_XY));
    ESP_ERROR_CHECK(
        esp_lcd_panel_mirror(panel_, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel_, true));
    active_panel_ = this;
}

void HensunPanel::EnableEmoteFlush() {
    if (preview_flush_semaphore_ != nullptr) {
        return;
    }
    preview_flush_semaphore_ = xSemaphoreCreateBinary();
    ESP_ERROR_CHECK(preview_flush_semaphore_ == nullptr ? ESP_ERR_NO_MEM : ESP_OK);
    const esp_lcd_panel_io_callbacks_t callbacks = {
        .on_color_trans_done = IoReadyCallback,
    };
    ESP_ERROR_CHECK(esp_lcd_panel_io_register_event_callbacks(
        panel_io_, &callbacks, this));
}

HensunPanel::~HensunPanel() {
    if (active_panel_ == this) {
        active_panel_ = nullptr;
    }
    if (preview_flush_semaphore_ != nullptr) {
        vSemaphoreDelete(preview_flush_semaphore_);
        preview_flush_semaphore_ = nullptr;
    }
    if (panel_ != nullptr) {
        esp_lcd_panel_del(panel_);
        panel_ = nullptr;
    }
    if (panel_io_ != nullptr) {
        esp_lcd_panel_io_del(panel_io_);
        panel_io_ = nullptr;
    }
    spi_bus_free(DISPLAY_SPI_HOST);
}

void HensunPanel::AttachPlayer(emote_gen_player_handle_t player) {
    player_ = player;
}

bool HensunPanel::WaitForAnimationFlushes(int timeout_ms) {
    const int attempts = timeout_ms <= 0 ? 1 : (timeout_ms + 9) / 10;
    for (int attempt = 0;
         attempt < attempts && animation_flushes_pending_.load() > 0;
         ++attempt) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return animation_flushes_pending_.load() == 0;
}

bool HensunPanel::DrawPreview(const uint16_t* pixels, int duration_ms) {
    if (panel_ == nullptr || preview_flush_semaphore_ == nullptr || pixels == nullptr) {
        return false;
    }
    xSemaphoreTake(preview_flush_semaphore_, 0);
    preview_flush_pending_.store(true);
    const esp_err_t result =
        esp_lcd_panel_draw_bitmap(panel_, 0, 0, width_, height_, pixels);
    bool displayed = result == ESP_OK;
    if (displayed) {
        displayed =
            xSemaphoreTake(preview_flush_semaphore_, pdMS_TO_TICKS(750)) == pdTRUE;
    }
    if (!displayed) {
        preview_flush_pending_.store(false);
        ESP_LOGE(kTag, "preview flush failed: %s", esp_err_to_name(result));
    } else if (duration_ms > 0) {
        vTaskDelay(pdMS_TO_TICKS(duration_ms));
    }
    return displayed;
}

void HensunPanel::SetPowerSaveMode(bool on) {
    if (panel_ != nullptr) {
        esp_lcd_panel_disp_on_off(panel_, !on);
    }
}

void HensunPanel::FlushAnimation(int x_start, int y_start, int x_end,
                                 int y_end, const void* data,
                                 emote_gen_player_handle_t manager) {
    HensunPanel* panel = active_panel_;
    if (panel == nullptr || panel->panel_ == nullptr) {
        emote_gen_player_notify_flush_finished(manager);
        return;
    }
    panel->animation_flushes_pending_.fetch_add(1);
    const esp_err_t result = esp_lcd_panel_draw_bitmap(
        panel->panel_, x_start, y_start, x_end, y_end, data);
    if (result != ESP_OK) {
        panel->animation_flushes_pending_.fetch_sub(1);
        emote_gen_player_notify_flush_finished(manager);
    }
}

bool HensunPanel::IoReadyCallback(esp_lcd_panel_io_handle_t panel_io,
                                  esp_lcd_panel_io_event_data_t* event_data,
                                  void* user_ctx) {
    (void)panel_io;
    (void)event_data;
    auto* panel = static_cast<HensunPanel*>(user_ctx);
    if (panel == nullptr) {
        return false;
    }
    if (panel->preview_flush_pending_.exchange(false)) {
        BaseType_t task_woken = pdFALSE;
        xSemaphoreGiveFromISR(panel->preview_flush_semaphore_, &task_woken);
        return task_woken == pdTRUE;
    }
    if (panel->animation_flushes_pending_.load() > 0) {
        panel->animation_flushes_pending_.fetch_sub(1);
        if (panel->player_ != nullptr) {
            emote_gen_player_notify_flush_finished(panel->player_);
        }
    }
    return false;
}
