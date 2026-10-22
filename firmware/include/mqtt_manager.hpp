#pragma once
#include <atomic>
#include <string>
#include "app_config.hpp"
#include "message_contract.hpp"
#include "mqtt_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

class MqttManager {
public:
    bool begin(const AppConfig& config, const std::string& offline_payload);
    bool publish(const std::string& topic, const std::string& payload, uint64_t seq, bool retain = false);
    bool connected() const { return connected_.load(); }
    uint32_t connection_generation() const { return connection_generation_.load(); }
    size_t inflight() const;
private:
    static void event_thunk(void* args, esp_event_base_t, int32_t event_id, void* event_data);
    static void reconnect_task(void* args);
    void handle(esp_mqtt_event_handle_t event);
    esp_mqtt_client_handle_t client_{nullptr};
    nw::InflightTracker<MQTT_MAX_INFLIGHT> inflight_;
    SemaphoreHandle_t lock_{nullptr};
    std::string will_topic_, will_payload_;
    std::atomic<bool> connected_{false};
    std::atomic<bool> reconnect_task_running_{false};
    std::atomic<uint32_t> connection_generation_{0};
    uint32_t reconnect_delay_ms_{1000};
};
