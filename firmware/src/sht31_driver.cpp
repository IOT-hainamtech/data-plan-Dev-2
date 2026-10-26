#include "sht31_driver.hpp"
#include "driver/i2c.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <cmath>

namespace {
constexpr gpio_num_t SDA_PIN = GPIO_NUM_21;
constexpr gpio_num_t SCL_PIN = GPIO_NUM_22;
constexpr int READ_ATTEMPTS = 3;
constexpr float MIN_TEMPERATURE_C = -20.0F;
constexpr float MAX_TEMPERATURE_C = 80.0F;
}

bool Sht31Driver::begin(uint8_t address, float t_offset, float h_offset) {
    address_ = address; temperature_offset_ = t_offset; humidity_offset_ = h_offset;
    i2c_config_t cfg{};
    cfg.mode = I2C_MODE_MASTER;
    cfg.sda_io_num = SDA_PIN; cfg.scl_io_num = SCL_PIN;
    cfg.sda_pullup_en = GPIO_PULLUP_ENABLE; cfg.scl_pullup_en = GPIO_PULLUP_ENABLE;
    cfg.master.clk_speed = 100000;
    if (i2c_param_config(I2C_NUM_0, &cfg) != ESP_OK ||
        i2c_driver_install(I2C_NUM_0, I2C_MODE_MASTER, 0, 0, 0) != ESP_OK) return false;
    initialized_ = probe();
    return initialized_;
}
bool Sht31Driver::probe() const {
    // Soft-reset is harmless and requires an ACK, so it also verifies the configured 0x44/0x45 address.
    const uint8_t reset[] = {0x30, 0xA2};
    for (int attempt = 0; attempt < READ_ATTEMPTS; ++attempt) {
        if (i2c_master_write_to_device(I2C_NUM_0, address_, reset, sizeof(reset),
                                       pdMS_TO_TICKS(100)) == ESP_OK) {
            vTaskDelay(pdMS_TO_TICKS(2));
            return true;
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    return false;
}
bool Sht31Driver::crc_valid(const uint8_t* data, uint8_t expected) {
    uint8_t crc = 0xFF;
    for (int i = 0; i < 2; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit) crc = (crc & 0x80) ? (crc << 1) ^ 0x31 : crc << 1;
    }
    return crc == expected;
}
bool Sht31Driver::read(Sht31Reading& out) {
    if (!initialized_ && !(initialized_ = probe())) return false;
    const uint8_t command[] = {0x24, 0x00};
    for (int attempt = 0; attempt < READ_ATTEMPTS; ++attempt) {
        uint8_t raw[6]{};
        if (i2c_master_write_to_device(I2C_NUM_0, address_, command, sizeof(command),
                                       pdMS_TO_TICKS(100)) == ESP_OK) {
            vTaskDelay(pdMS_TO_TICKS(20));
            if (i2c_master_read_from_device(I2C_NUM_0, address_, raw, sizeof(raw),
                                            pdMS_TO_TICKS(100)) == ESP_OK &&
                crc_valid(raw, raw[2]) && crc_valid(raw + 3, raw[5])) {
                uint16_t t = (raw[0] << 8) | raw[1], h = (raw[3] << 8) | raw[4];
                const float temperature = -45.0F + 175.0F * static_cast<float>(t) / 65535.0F + temperature_offset_;
                const float humidity = 100.0F * static_cast<float>(h) / 65535.0F + humidity_offset_;
                // Match the active TEMP_HUM_SENSOR backend profile. Do not clamp invalid readings.
                if (std::isfinite(temperature) && std::isfinite(humidity) &&
                    temperature >= MIN_TEMPERATURE_C && temperature <= MAX_TEMPERATURE_C &&
                    humidity >= 0.0F && humidity <= 100.0F) {
                    out = {temperature, humidity};
                    return true;
                }
            }
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    initialized_ = false;
    return false;
}
