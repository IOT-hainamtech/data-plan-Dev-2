#pragma once
#include "app_config.hpp"
#include <string>

class WifiManager {
public:
    bool connect(const AppConfig& config, uint32_t timeout_ms = 20000);
    bool connected() const;
    int rssi() const;
    std::string ip_address() const;
    void recover();
};
