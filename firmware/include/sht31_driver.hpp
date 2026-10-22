#pragma once
#include <cstdint>

struct Sht31Reading { float temperature; float humidity; };
class Sht31Driver {
public:
    bool begin(uint8_t address, float temperature_offset, float humidity_offset);
    bool read(Sht31Reading& reading);
    static bool crc_valid(const uint8_t* data, uint8_t expected);
private:
    uint8_t address_{0x44};
    float temperature_offset_{0};
    float humidity_offset_{0};
};
