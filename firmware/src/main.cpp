#include <string>
#include "config_manager.hpp"
#include "esp_log.h"
#include "esp_task_wdt.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "message_contract.hpp"
#include "mqtt_manager.hpp"
#include "payload_builder.hpp"
#include "reed_door_driver.hpp"
#include "sequence_manager.hpp"
#include "sht31_driver.hpp"
#include "time_sync.hpp"
#include "wifi_manager.hpp"

namespace {
const char* TAG = "nextwireless";
bool timestamp(char out[25]) { return TimeSync::iso8601_utc(out, 25); }
}

extern "C" void app_main() {
    AppConfig config; std::string error;
    if (!ConfigManager().load(config, error)) {
        ESP_LOGE(TAG, "Configuration rejected: %s", error.c_str());
        return;
    }
    // Never log Wi-Fi or MQTT secrets.
    ESP_LOGI(TAG, "Starting device=%s sensor=%s", config.device_id.c_str(),
             config.sensor_type == SensorType::Sht31 ? "sht31" : "reed_door");
    WifiManager wifi;
    if (!wifi.connect(config) || !TimeSync().synchronize()) {
        ESP_LOGE(TAG, "Network/time synchronization failed; TLS will not start");
        esp_restart();
    }
    SequenceManager sequence; sequence.begin();
    char ts[25]; timestamp(ts);
    std::string status_topic = nw::topic(config.org_code, config.site_code, config.device_id, "status");
    std::string offline = PayloadBuilder::status(config.device_id, sequence.boot_id(), 0, ts, "offline");
    MqttManager mqtt;
    if (!mqtt.begin(config, offline)) { ESP_LOGE(TAG, "MQTT initialization failed"); esp_restart(); }

    Sht31Driver sht31; ReedDoorDriver door;
    if (config.sensor_type == SensorType::Sht31) {
        if (!sht31.begin(config.sht31_address, config.temperature_offset, config.humidity_offset)) {
            ESP_LOGE(TAG, "SHT31 I2C initialization failed");
        }
    } else door.begin(config.door_closed_level, config.door_debounce_ms);

    esp_task_wdt_config_t watchdog{.timeout_ms = 15000, .idle_core_mask = 0, .trigger_panic = true};
    esp_task_wdt_init(&watchdog); esp_task_wdt_add(nullptr);
    TickType_t last_telemetry = 0, last_heartbeat = 0;
    uint32_t seen_connection = 0;
    std::string pending_event; uint64_t pending_event_seq = 0;
    std::string pending_birth; uint64_t pending_birth_seq = 0;
    std::string pending_periodic, pending_periodic_topic; uint64_t pending_periodic_seq = 0;

    while (true) {
        esp_task_wdt_reset();
        TickType_t now = xTaskGetTickCount();
        if (mqtt.connected() && mqtt.connection_generation() != seen_connection && timestamp(ts)) {
            if (pending_birth.empty()) {
                pending_birth_seq = sequence.next();
                pending_birth = PayloadBuilder::status(config.device_id, sequence.boot_id(),
                                                        pending_birth_seq, ts, "online");
            }
            if (mqtt.publish(status_topic, pending_birth, pending_birth_seq, true)) {
                pending_birth.clear(); seen_connection = mqtt.connection_generation();
            }
        }
        if (config.sensor_type == SensorType::Sht31 &&
            pending_periodic.empty() && now - last_telemetry >= pdMS_TO_TICKS(config.telemetry_seconds * 1000U) && timestamp(ts)) {
            Sht31Reading reading{}; pending_periodic_seq = sequence.next();
            if (sht31.read(reading)) {
                pending_periodic = PayloadBuilder::telemetry(config.device_id, sequence.boot_id(),
                    pending_periodic_seq, ts, reading.temperature, reading.humidity);
                pending_periodic_topic = nw::topic(config.org_code, config.site_code,
                                                    config.device_id, "telemetry");
            } else {
                pending_periodic = PayloadBuilder::telemetry_error(config.device_id,
                    sequence.boot_id(), pending_periodic_seq, ts);
                pending_periodic_topic = nw::topic(config.org_code, config.site_code,
                                                    config.device_id, "telemetry");
            }
            last_telemetry = now;
        }
        if (config.sensor_type == SensorType::ReedDoor) {
            bool changed = false; bool open = door.poll(changed);
            if (changed && pending_event.empty() && timestamp(ts)) {
                pending_event_seq = sequence.next();
                pending_event = PayloadBuilder::door_event(config.device_id, sequence.boot_id(),
                    pending_event_seq, ts, open, sequence.event_id(config.device_id, pending_event_seq));
            }
            if (!pending_event.empty() && mqtt.publish(
                    nw::topic(config.org_code, config.site_code, config.device_id, "event"),
                    pending_event, pending_event_seq)) pending_event.clear();
        }
        if (pending_periodic.empty() &&
            now - last_heartbeat >= pdMS_TO_TICKS(config.heartbeat_seconds * 1000U) && timestamp(ts)) {
            pending_periodic_seq = sequence.next(); const char* door_state = nullptr;
            if (config.sensor_type == SensorType::ReedDoor) door_state = door.is_open() ? "open" : "closed";
            pending_periodic = PayloadBuilder::status(config.device_id, sequence.boot_id(),
                pending_periodic_seq, ts, "online", "ok", door_state);
            pending_periodic_topic = status_topic;
            last_heartbeat = now;
        }
        if (!pending_periodic.empty() && mqtt.publish(pending_periodic_topic,
                                                      pending_periodic, pending_periodic_seq)) {
            pending_periodic.clear(); pending_periodic_topic.clear();
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
