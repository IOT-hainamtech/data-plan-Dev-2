#pragma once
#include <string>
class TimeSync {
public:
    bool synchronize(const std::string& primary, const std::string& secondary,
                     unsigned timeout_seconds = 45);
    static bool synchronized();
    static bool iso8601_utc(char* output, unsigned size);
    static bool vietnam_datetime(char* output, unsigned size);
};
