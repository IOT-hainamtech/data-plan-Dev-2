#include "reed_door_driver.hpp"
#include "driver/gpio.h"
#include "esp_timer.h"

void ReedDoorDriver::begin(int closed_level, uint32_t debounce_ms) {
    closed_level_ = closed_level; debounce_ms_ = debounce_ms;
    gpio_config_t cfg{}; cfg.pin_bit_mask = 1ULL << GPIO_NUM_27;
    cfg.mode = GPIO_MODE_INPUT; cfg.pull_up_en = GPIO_PULLUP_ENABLE;
    cfg.pull_down_en = GPIO_PULLDOWN_DISABLE; cfg.intr_type = GPIO_INTR_DISABLE;
    gpio_config(&cfg);
    stable_level_ = candidate_level_ = gpio_get_level(GPIO_NUM_27);
    candidate_since_us_ = esp_timer_get_time();
}
bool ReedDoorDriver::poll(bool& changed) {
    changed = false;
    int level = gpio_get_level(GPIO_NUM_27);
    int64_t now = esp_timer_get_time();
    if (level != candidate_level_) { candidate_level_ = level; candidate_since_us_ = now; return is_open(); }
    if (candidate_level_ != stable_level_ && now - candidate_since_us_ >= static_cast<int64_t>(debounce_ms_) * 1000) {
        stable_level_ = candidate_level_; changed = true;
    }
    return is_open();
}
