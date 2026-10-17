#include "config_manager.hpp"

#include <cstdlib>
#include <cctype>
#include "nvs.h"
#include "nvs_flash.h"

namespace {
bool read_string(nvs_handle_t handle, const char* key, std::string& value) {
    size_t size = 0;
    if (nvs_get_str(handle, key, nullptr, &size) != ESP_OK || size < 2) return false;
    std::string buffer(size, '\0');
    if (nvs_get_str(handle, key, buffer.data(), &size) != ESP_OK) return false;
    buffer.resize(size - 1);
    value = buffer;
    return true;
}
bool read_blob(nvs_handle_t handle, const char* key, std::string& value) {
    size_t size = 0;
    if (nvs_get_blob(handle, key, nullptr, &size) != ESP_OK || size == 0) return false;
    value.resize(size);
    if (nvs_get_blob(handle, key, value.data(), &size) != ESP_OK) return false;
    value.resize(size);
    return true;
}
bool safe_code(const std::string& value) {
    if (value.empty() || value.size() > 64) return false;
    for (char c : value) if (!(std::isalnum(static_cast<unsigned char>(c)) || c == '-' || c == '_')) return false;
    return true;
}
}

bool ConfigManager::load(AppConfig& c, std::string& error) const {
    esp_err_t status = nvs_flash_init();
    if (status != ESP_OK) { error = "NVS initialization failed"; return false; }
    nvs_handle_t h;
    if (nvs_open("nwcfg", NVS_READONLY, &h) != ESP_OK) { error = "NVS namespace nwcfg missing"; return false; }
    std::string sensor, temp_offset, humidity_offset;
    bool ok = read_string(h, "wifi_ssid", c.wifi_ssid) &&
              read_string(h, "wifi_password", c.wifi_password) &&
              read_string(h, "mqtt_uri", c.mqtt_uri) &&
              read_string(h, "mqtt_username", c.mqtt_username) &&
              read_string(h, "mqtt_password", c.mqtt_password) &&
              read_blob(h, "mqtt_ca", c.mqtt_ca) &&
              read_string(h, "org_code", c.org_code) && read_string(h, "site_code", c.site_code) &&
              read_string(h, "device_id", c.device_id) &&
              read_string(h, "profile_code", c.profile_code) &&
              read_string(h, "sensor_type", sensor);
    uint8_t address = c.sht31_address, closed = static_cast<uint8_t>(c.door_closed_level);
    nvs_get_u32(h, "telemetry_sec", &c.telemetry_seconds);
    nvs_get_u32(h, "heartbeat_sec", &c.heartbeat_seconds);
    nvs_get_u8(h, "sht31_address", &address);
    nvs_get_u8(h, "door_closed_lvl", &closed);
    nvs_get_u32(h, "door_debounce", &c.door_debounce_ms);
    if (read_string(h, "temp_offset", temp_offset)) c.temperature_offset = std::strtof(temp_offset.c_str(), nullptr);
    if (read_string(h, "humidity_offset", humidity_offset)) c.humidity_offset = std::strtof(humidity_offset.c_str(), nullptr);
    nvs_close(h);
    if (!ok) { error = "required NVS configuration is missing"; return false; }
    c.sht31_address = address; c.door_closed_level = closed;
    if (sensor == "sht31") c.sensor_type = SensorType::Sht31;
    else if (sensor == "reed_door") c.sensor_type = SensorType::ReedDoor;
    else { error = "sensor_type must be sht31 or reed_door"; return false; }
    return validate(c, error);
}

bool ConfigManager::validate(const AppConfig& c, std::string& error) {
    if (c.wifi_ssid.empty() || c.wifi_password.empty() || c.mqtt_password.empty()) { error = "credentials missing"; return false; }
    if (c.mqtt_uri.rfind("mqtts://", 0) != 0) { error = "mqtt_uri must use mqtts://"; return false; }
    if (c.mqtt_ca.find("-----BEGIN CERTIFICATE-----") == std::string::npos) { error = "MQTT CA certificate missing or invalid"; return false; }
    if (!safe_code(c.org_code) || !safe_code(c.site_code) || !safe_code(c.device_id)) { error = "invalid org/site/device code"; return false; }
    if (c.mqtt_username != c.device_id) { error = "MQTT username must match device_id"; return false; }
    if ((c.sensor_type == SensorType::Sht31 && c.profile_code != "TEMP_HUM_SENSOR") ||
        (c.sensor_type == SensorType::ReedDoor && c.profile_code != "DOOR_SENSOR")) {
        error = "profile_code does not match sensor_type"; return false;
    }
    if (c.telemetry_seconds == 0 || c.heartbeat_seconds == 0) { error = "interval must be positive"; return false; }
    if (c.sht31_address != 0x44 && c.sht31_address != 0x45) { error = "SHT31 address must be 0x44 or 0x45"; return false; }
    if (c.door_closed_level != 0 && c.door_closed_level != 1) { error = "door_closed_level must be 0 or 1"; return false; }
    if (c.door_debounce_ms < 10 || c.door_debounce_ms > 2000) { error = "door debounce out of range"; return false; }
    return true;
}
