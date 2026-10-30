#include "wifi_manager.hpp"
#include <cstring>
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_wifi.h"
#include "lwip/ip4_addr.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include <atomic>
#include <algorithm>

namespace {
constexpr EventBits_t CONNECTED = BIT0;
EventGroupHandle_t events;
std::atomic<bool> reconnect_task_running{false};
uint32_t reconnect_delay_ms = 1000;
void reconnect_task(void*) {
    while (!(xEventGroupGetBits(events) & CONNECTED)) {
        vTaskDelay(pdMS_TO_TICKS(reconnect_delay_ms));
        esp_wifi_connect();
        const uint32_t jitter = esp_random() % (reconnect_delay_ms / 5 + 1);
        reconnect_delay_ms = std::min<uint32_t>(reconnect_delay_ms * 2 + jitter, 60000);
    }
    reconnect_task_running = false;
    vTaskDelete(nullptr);
}
void event_handler(void*, esp_event_base_t base, int32_t id, void* data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) esp_wifi_connect();
    else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
    const auto* info =
        static_cast<const wifi_event_sta_disconnected_t*>(data);

    ESP_LOGW("wifi_manager", "WiFi disconnected, reason=%u",
             static_cast<unsigned>(info->reason));

    xEventGroupClearBits(events, CONNECTED);
        if (!reconnect_task_running.exchange(true))
            xTaskCreate(reconnect_task, "wifi_reconnect", 3072, nullptr, 5, nullptr);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        reconnect_delay_ms = 1000;
        xEventGroupSetBits(events, CONNECTED);
    }
}
}

bool WifiManager::connect(const AppConfig& c, uint32_t timeout_ms) {
    esp_netif_init(); esp_event_loop_create_default(); esp_netif_create_default_wifi_sta();
    events = xEventGroupCreate();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, nullptr));
    wifi_config_t cfg{};
    std::strncpy(reinterpret_cast<char*>(cfg.sta.ssid), c.wifi_ssid.c_str(), sizeof(cfg.sta.ssid) - 1);
    std::strncpy(reinterpret_cast<char*>(cfg.sta.password), c.wifi_password.c_str(), sizeof(cfg.sta.password) - 1);
    cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &cfg));
    ESP_ERROR_CHECK(esp_wifi_start());
    return (xEventGroupWaitBits(events, CONNECTED, pdFALSE, pdTRUE,
                                pdMS_TO_TICKS(timeout_ms)) & CONNECTED) != 0;
}
bool WifiManager::connected() const { return events && (xEventGroupGetBits(events) & CONNECTED); }
int WifiManager::rssi() const {
    wifi_ap_record_t ap{};
    return esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
}
std::string WifiManager::ip_address() const {
    esp_netif_t* netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    esp_netif_ip_info_t info{};
    if (!netif || esp_netif_get_ip_info(netif, &info) != ESP_OK) return {};
    char value[IP4ADDR_STRLEN_MAX]{};
    return ip4addr_ntoa_r(reinterpret_cast<const ip4_addr_t*>(&info.ip), value, sizeof(value))
        ? value : "";
}
void WifiManager::recover() { esp_wifi_disconnect(); esp_wifi_connect(); }
