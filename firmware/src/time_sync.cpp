#include "time_sync.hpp"
#include <atomic>
#include <ctime>
#include "esp_sntp.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace {
std::atomic<bool> initial_sync_completed{false};

void on_time_synchronized(struct timeval*) {
    initial_sync_completed.store(true, std::memory_order_release);
}
}

bool TimeSync::synchronized() {
    if (!initial_sync_completed.load(std::memory_order_acquire)) return false;
    std::time_t now;
    std::time(&now);
    return now > 1704067200;
}

bool TimeSync::synchronize(const std::string& primary, const std::string& secondary,
                           unsigned timeout_seconds) {
    initial_sync_completed.store(false, std::memory_order_release);
    esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
    // A retained RTC value can be plausible but stale after a soft reset. Set
    // the initial SNTP response immediately and wait for the real sync callback
    // before any timestamp is labelled as NTP-sourced.
    esp_sntp_set_sync_mode(SNTP_SYNC_MODE_IMMED);
    esp_sntp_set_sync_interval(60U * 60U * 1000U);
    esp_sntp_set_time_sync_notification_cb(on_time_synchronized);
    esp_sntp_setservername(0, const_cast<char*>(primary.c_str()));
    esp_sntp_setservername(1, const_cast<char*>(secondary.c_str()));
    esp_sntp_init();
    for (unsigned i = 0; i < timeout_seconds * 2 && !synchronized(); ++i) vTaskDelay(pdMS_TO_TICKS(500));
    if (!synchronized()) return false;

    // The clock now has a trustworthy baseline. Future hourly corrections can
    // be smooth without delaying initial boot by the previous clock offset.
    esp_sntp_set_sync_mode(SNTP_SYNC_MODE_SMOOTH);
    return true;
}
bool TimeSync::iso8601_utc(char* output, unsigned size) {
    if (!synchronized() || size < 21) return false;
    std::time_t now; std::time(&now); std::tm tm{}; gmtime_r(&now, &tm);
    return std::strftime(output, size, "%Y-%m-%dT%H:%M:%SZ", &tm) > 0;
}
bool TimeSync::vietnam_datetime(char* output, unsigned size) {
    if (!synchronized() || size < 26) return false;
    std::time_t now; std::time(&now); now += 7 * 60 * 60;
    std::tm tm{}; gmtime_r(&now, &tm);
    return std::strftime(output, size, "%d/%m/%Y %H:%M:%S UTC+7", &tm) > 0;
}
