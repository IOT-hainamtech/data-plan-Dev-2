#pragma once
#include <cstdint>
#include <string>

class PayloadBuilder {
public:
    static std::string status(const std::string& device, const std::string& boot,
                              uint64_t seq, const char* timestamp, const char* state,
                              const char* fw_version = nullptr, const char* reason = nullptr,
                              int rssi = 0, const char* ip = nullptr);
    static std::string telemetry(const std::string& device, const std::string& boot,
                                 uint64_t seq, const char* timestamp, float temperature,
                                 float humidity);
    static std::string telemetry_error(const std::string& device, const std::string& boot,
                                       uint64_t seq, const char* timestamp);
    static std::string door_event(const std::string& device, const std::string& boot,
                                  uint64_t seq, const char* timestamp, bool open,
                                  const std::string& event_id);
    static std::string door_heartbeat(const std::string& device, const std::string& boot,
                                      uint64_t seq, const char* timestamp, bool open);
};
