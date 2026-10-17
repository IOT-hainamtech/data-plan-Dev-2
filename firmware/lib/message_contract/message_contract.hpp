#pragma once
#include <cctype>
#include <cstdint>
#include <string>

namespace nw {
inline bool valid_code(const std::string& value) {
    if (value.empty() || value.size() > 64) return false;
    for (unsigned char c : value) if (!(std::isalnum(c) || c == '-' || c == '_')) return false;
    return true;
}
inline bool valid_channel(const std::string& channel) {
    return channel == "telemetry" || channel == "event" || channel == "status";
}
inline std::string topic(const std::string& org, const std::string& site,
                         const std::string& device, const std::string& channel) {
    if (!valid_code(org) || !valid_code(site) || !valid_code(device) || !valid_channel(channel)) return {};
    return "nw/v1/" + org + "/" + site + "/" + device + "/" + channel;
}
inline std::string event_id(const std::string& device, const std::string& boot, uint64_t seq) {
    return device + "-" + boot + "-" + std::to_string(seq);
}

template <size_t Capacity>
class InflightTracker {
public:
    bool add(int message_id, uint64_t seq) {
        for (auto& slot : slots_) if (!slot.used) { slot = {true, message_id, seq}; return true; }
        return false;
    }
    bool acknowledge(int message_id) {
        for (auto& slot : slots_) if (slot.used && slot.message_id == message_id) { slot.used = false; return true; }
        return false;
    }
    size_t size() const { size_t n = 0; for (const auto& slot : slots_) n += slot.used; return n; }
    bool full() const { return size() == Capacity; }
private:
    struct Slot { bool used{false}; int message_id{-1}; uint64_t seq{0}; };
    Slot slots_[Capacity]{};
};
}
