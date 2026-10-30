#pragma once

#include <cstdint>
#include <string>

enum class SensorType { Sht31, ReedDoor };

struct AppConfig {
    std::string wifi_ssid;
    std::string wifi_password;
    std::string mqtt_uri;
    std::string mqtt_username;
    std::string mqtt_password;
    std::string mqtt_ca;
    std::string org_code;
    std::string site_code;
    std::string device_id;
    std::string profile_code;
    SensorType sensor_type{SensorType::Sht31};
    uint32_t telemetry_seconds{60};
    uint32_t heartbeat_seconds{60};
    uint8_t sht31_address{0x44};
    float temperature_offset{0.0F};
    float humidity_offset{0.0F};
    int door_closed_level{0};
    uint32_t door_debounce_ms{50};
    uint32_t mqtt_keepalive_seconds{30};
    std::string ntp_server_1{"pool.ntp.org"};
    std::string ntp_server_2{"time.google.com"};
};
