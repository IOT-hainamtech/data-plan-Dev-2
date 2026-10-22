#include "sht31_driver.hpp"
#include "driver/i2c.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

bool Sht31Driver::begin(uint8_t address, float t_offset, float h_offset) {
    address_ = address; temperature_offset_ = t_offset; humidity_offset_ = h_offset;
    i2c_config_t cfg{};
    cfg.mode = I2C_MODE_MASTER;
    cfg.sda_io_num = GPIO_NUM_21; cfg.scl_io_num = GPIO_NUM_22;
    cfg.sda_pullup_en = GPIO_PULLUP_ENABLE; cfg.scl_pullup_en = GPIO_PULLUP_ENABLE;
    cfg.master.clk_speed = 100000;
    return i2c_param_config(I2C_NUM_0, &cfg) == ESP_OK &&
           i2c_driver_install(I2C_NUM_0, I2C_MODE_MASTER, 0, 0, 0) == ESP_OK;
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
    const uint8_t command[] = {0x24, 0x00};
    if (i2c_master_write_to_device(I2C_NUM_0, address_, command, sizeof(command),
                                   pdMS_TO_TICKS(100)) != ESP_OK) return false;
    vTaskDelay(pdMS_TO_TICKS(20));
    uint8_t raw[6];
    if (i2c_master_read_from_device(I2C_NUM_0, address_, raw, sizeof(raw),
                                    pdMS_TO_TICKS(100)) != ESP_OK) return false;
    if (!crc_valid(raw, raw[2]) || !crc_valid(raw + 3, raw[5])) return false;
    uint16_t t = (raw[0] << 8) | raw[1], h = (raw[3] << 8) | raw[4];
    out.temperature = -45.0F + 175.0F * static_cast<float>(t) / 65535.0F + temperature_offset_;
    out.humidity = 100.0F * static_cast<float>(h) / 65535.0F + humidity_offset_;
    if (out.humidity < 0) out.humidity = 0;
    if (out.humidity > 100) out.humidity = 100;
    return true;
}
