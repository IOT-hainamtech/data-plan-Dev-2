#include "sequence_manager.hpp"
#include "message_contract.hpp"
#include "esp_random.h"
#include <cstdio>

void SequenceManager::begin() {
    uint8_t bytes[8];
    esp_fill_random(bytes, sizeof(bytes));
    char output[17];
    for (size_t i = 0; i < sizeof(bytes); ++i) std::snprintf(output + i * 2, 3, "%02x", bytes[i]);
    boot_id_ = output;
    sequence_ = 0;
}
uint64_t SequenceManager::next() { return ++sequence_; }
std::string SequenceManager::event_id(const std::string& device, uint64_t seq) const {
    return nw::event_id(device, boot_id_, seq);
}
