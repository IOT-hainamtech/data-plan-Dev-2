#pragma once
#include "app_config.hpp"

class WifiManager {
public:
    bool connect(const AppConfig& config, uint32_t timeout_ms = 60000);
    bool connected() const;
};
