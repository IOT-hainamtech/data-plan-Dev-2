#include "time_sync.hpp"
#include <ctime>
#include "esp_sntp.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

bool TimeSync::synchronized() { std::time_t now; std::time(&now); return now > 1704067200; }
bool TimeSync::synchronize(const std::string& primary, const std::string& secondary,
                           unsigned timeout_seconds) {
    esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
    esp_sntp_set_sync_mode(SNTP_SYNC_MODE_SMOOTH);
    esp_sntp_set_sync_interval(60U * 60U * 1000U);
    esp_sntp_setservername(0, const_cast<char*>(primary.c_str()));
    esp_sntp_setservername(1, const_cast<char*>(secondary.c_str()));
    esp_sntp_init();
    for (unsigned i = 0; i < timeout_seconds * 2 && !synchronized(); ++i) vTaskDelay(pdMS_TO_TICKS(500));
    return synchronized();
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
