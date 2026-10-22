#include "time_sync.hpp"
#include <ctime>
#include "esp_sntp.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

bool TimeSync::synchronized() { std::time_t now; std::time(&now); return now > 1704067200; }
bool TimeSync::synchronize(unsigned timeout_seconds) {
    esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, const_cast<char*>("pool.ntp.org"));
    esp_sntp_init();
    for (unsigned i = 0; i < timeout_seconds * 2 && !synchronized(); ++i) vTaskDelay(pdMS_TO_TICKS(500));
    return synchronized();
}
bool TimeSync::iso8601_utc(char* output, unsigned size) {
    if (!synchronized() || size < 21) return false;
    std::time_t now; std::time(&now); std::tm tm{}; gmtime_r(&now, &tm);
    return std::strftime(output, size, "%Y-%m-%dT%H:%M:%SZ", &tm) > 0;
}
