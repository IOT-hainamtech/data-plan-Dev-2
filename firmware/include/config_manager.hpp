#pragma once
#include "app_config.hpp"

class ConfigManager {
public:
    bool load(AppConfig& config, std::string& error) const;
    static bool validate(const AppConfig& config, std::string& error);
};
