#pragma once
#include <cstdint>

class ReedDoorDriver {
public:
    void begin(int closed_level, uint32_t debounce_ms);
    bool poll(bool& changed);
    bool is_open() const { return stable_level_ != closed_level_; }
private:
    int closed_level_{0};
    int stable_level_{0};
    int candidate_level_{0};
    uint32_t debounce_ms_{50};
    int64_t candidate_since_us_{0};
    int64_t chatter_window_started_us_{0};
    int64_t suppressed_until_us_{0};
    uint8_t transitions_in_window_{0};
};
