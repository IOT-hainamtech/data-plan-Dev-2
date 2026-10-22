#pragma once
class TimeSync {
public:
    bool synchronize(unsigned timeout_seconds = 45);
    static bool synchronized();
    static bool iso8601_utc(char* output, unsigned size);
};
