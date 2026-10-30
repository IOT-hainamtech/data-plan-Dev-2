#pragma once
#include <cstdint>
#include <string>

class SequenceManager {
public:
    void begin();
    uint64_t next() { return sequence_++; }
    const std::string& boot_id() const { return boot_id_; }
    std::string event_id(const std::string& device_id, uint64_t seq) const;
private:
    std::string boot_id_;
    uint64_t sequence_{0};
};
