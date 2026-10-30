#include <cmath>
#include <cstdint>
#include <deque>
#include <string>
#include "config_manager.hpp"
#include "esp_log.h"
#include "esp_random.h"
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
constexpr size_t MAX_PENDING_EVENTS = 8;
struct PendingEvent { std::string payload; uint64_t seq; };
bool timestamp(char out[25]) { return TimeSync::iso8601_utc(out, 25); }

bool deadline_reached(TickType_t now, TickType_t deadline) {
    return static_cast<int32_t>(now - deadline) >= 0;
}
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
    if (!wifi.connect(config) ||
        !TimeSync().synchronize(config.ntp_server_1, config.ntp_server_2)) {
        ESP_LOGE(TAG, "Network/time synchronization failed; TLS will not start");
        esp_restart();
    }
    SequenceManager sequence; sequence.begin();
    const uint64_t initial_birth_seq = sequence.next();  // U-MQ-10: zero
    const uint64_t lwt_seq = sequence.next();            // unique within boot
    char ts[25]; timestamp(ts);
    std::string status_topic = nw::topic(config.org_code, config.site_code, config.device_id, "status");
    std::string offline = PayloadBuilder::status(config.device_id, sequence.boot_id(), lwt_seq, nullptr,
                                                  "offline", FW_VERSION, "lwt");
    MqttManager mqtt;
    if (!mqtt.begin(config, offline)) { ESP_LOGE(TAG, "MQTT initialization failed"); esp_restart(); }

    Sht31Driver sht31; ReedDoorDriver door;
    if (config.sensor_type == SensorType::Sht31) {
        if (!sht31.begin(config.sht31_address, config.temperature_offset, config.humidity_offset)) {
            ESP_LOGE(TAG, "SHT31 not responding at 0x%02x on SDA=21/SCL=22; error telemetry will be sent",
                     config.sht31_address);
        } else {
            ESP_LOGI(TAG, "SHT31 ready at 0x%02x on SDA=21/SCL=22", config.sht31_address);
        }
        ESP_LOGI(TAG, "SHT31 telemetry interval=%lu seconds",
                 static_cast<unsigned long>(config.telemetry_seconds));
    } else {
        door.begin(config.door_closed_level, config.door_debounce_ms);
        ESP_LOGI(TAG, "MC-38 ready on GPIO27 INPUT_PULLUP closed_level=%d debounce_ms=%lu initial=%s",
                 config.door_closed_level, static_cast<unsigned long>(config.door_debounce_ms),
                 door.is_open() ? "open" : "closed");
    }

    esp_task_wdt_config_t watchdog{.timeout_ms = 30000, .idle_core_mask = 0, .trigger_panic = true};
    esp_err_t watchdog_state = esp_task_wdt_status(nullptr);
    if (watchdog_state == ESP_ERR_INVALID_STATE) {
        ESP_ERROR_CHECK(esp_task_wdt_init(&watchdog));
        watchdog_state = ESP_ERR_NOT_FOUND;
    } else {
        ESP_ERROR_CHECK(esp_task_wdt_reconfigure(&watchdog));
    }
    if (watchdog_state == ESP_ERR_NOT_FOUND) {
        ESP_ERROR_CHECK(esp_task_wdt_add(nullptr));
    } else {
        ESP_ERROR_CHECK(watchdog_state);
    }
    TickType_t now_ticks = xTaskGetTickCount();
    TickType_t next_sht31_sample = now_ticks + pdMS_TO_TICKS(
        config.telemetry_seconds * 1000U + esp_random() % 5001U);
    TickType_t next_door_heartbeat = now_ticks;
    TickType_t mqtt_disconnected_since = now_ticks;
    TickType_t last_wifi_recovery = now_ticks;
    uint32_t seen_connection = 0;
    std::deque<PendingEvent> pending_events;
    std::string pending_birth; uint64_t pending_birth_seq = 0;
    bool initial_birth_pending = true;
    std::string pending_periodic, pending_periodic_topic; uint64_t pending_periodic_seq = 0;
    bool have_previous_temperature = false;
    float previous_temperature = 0.0F;

    while (true) {
        esp_task_wdt_reset();
        TickType_t now = xTaskGetTickCount();
        if (mqtt.connected()) {
            mqtt_disconnected_since = now;
        } else {
            const TickType_t down = now - mqtt_disconnected_since;
            if (down >= pdMS_TO_TICKS(15U * 60U * 1000U)) {
                ESP_LOGE(TAG, "MQTT unavailable for 15 minutes; restarting device");
                esp_restart();
            }
            if (down >= pdMS_TO_TICKS(5U * 60U * 1000U) &&
                now - last_wifi_recovery >= pdMS_TO_TICKS(5U * 60U * 1000U)) {
                ESP_LOGW(TAG, "MQTT unavailable for 5 minutes; restarting Wi-Fi association");
                wifi.recover(); last_wifi_recovery = now;
            }
        }
        if (mqtt.connected() && mqtt.connection_generation() != seen_connection && timestamp(ts)) {
            if (pending_birth.empty()) {
                pending_birth_seq = initial_birth_pending ? initial_birth_seq : sequence.next();
                const std::string ip = wifi.ip_address();
                pending_birth = PayloadBuilder::status(config.device_id, sequence.boot_id(),
                    pending_birth_seq, ts, "online", FW_VERSION, nullptr, wifi.rssi(), ip.c_str());
            }
            if (mqtt.publish(status_topic, pending_birth, pending_birth_seq, true)) {
                pending_birth.clear(); initial_birth_pending = false;
                seen_connection = mqtt.connection_generation();
            }
        }
        if (config.sensor_type == SensorType::Sht31 && deadline_reached(now, next_sht31_sample)) {
            do next_sht31_sample += pdMS_TO_TICKS(config.telemetry_seconds * 1000U);
            while (deadline_reached(now, next_sht31_sample));

            Sht31Reading reading{};
            bool sensor_ok = sht31.read(reading);
            char local_time[32]{};
            bool time_ok = timestamp(ts) && TimeSync::vietnam_datetime(local_time, sizeof(local_time));
            uint64_t sample_seq = 0;
            const char* publish_state = mqtt.connected() ? "PUBLISH_BACKPRESSURE" : "MQTT_DISCONNECTED";

            if (!time_ok) {
                ESP_LOGE(TAG, "TIME_NOT_SYNCED | sensor_sample=%s | MQTT=%s",
                         sensor_ok ? "VALID" : "SENSOR_ERROR",
                         mqtt.connected() ? "MQTT_CONNECTED" : "MQTT_DISCONNECTED");
            } else if (!pending_periodic.empty()) {
                publish_state = "PUBLISH_PENDING_PREVIOUS";
            } else {
                sample_seq = sequence.next();
                pending_periodic_seq = sample_seq;
                pending_periodic_topic = nw::topic(config.org_code, config.site_code,
                                                    config.device_id, "telemetry");
                pending_periodic = sensor_ok
                    ? PayloadBuilder::telemetry(config.device_id, sequence.boot_id(), sample_seq,
                                                ts, reading.temperature, reading.humidity)
                    : PayloadBuilder::telemetry_error(config.device_id, sequence.boot_id(),
                                                      sample_seq, ts);
                if (mqtt.publish(pending_periodic_topic, pending_periodic,
                                 pending_periodic_seq)) {
                    publish_state = "PUBLISH_QUEUED";
                    pending_periodic.clear(); pending_periodic_topic.clear();
                }
            }

            if (!sensor_ok) {
                ESP_LOGW(TAG, "[%s] %s | SENSOR_ERROR | seq=%llu | MQTT: %s",
                         time_ok ? local_time : "TIME_NOT_SYNCED", config.device_id.c_str(),
                         static_cast<unsigned long long>(sample_seq), publish_state);
            } else {
                float delta = have_previous_temperature
                    ? reading.temperature - previous_temperature : 0.0F;
                const char* trend = !have_previous_temperature || std::fabs(delta) < 0.005F
                    ? "STABLE" : (delta > 0.0F ? "RISING" : "FALLING");
                ESP_LOGI(TAG,
                    "[%s] %s | TEMP: %.2f C | HUM: %.2f%%RH | DELTA: %+.2f C | %s | seq=%llu | MQTT: %s",
                    time_ok ? local_time : "TIME_NOT_SYNCED", config.device_id.c_str(),
                    reading.temperature, reading.humidity, delta, trend,
                    static_cast<unsigned long long>(sample_seq), publish_state);
                previous_temperature = reading.temperature;
                have_previous_temperature = true;
            }
        }
        if (config.sensor_type == SensorType::ReedDoor) {
            bool changed = false; bool open = door.poll(changed);
            if (changed && timestamp(ts)) {
                if (pending_events.size() < MAX_PENDING_EVENTS) {
                    const uint64_t event_seq = sequence.next();
                    pending_events.push_back({PayloadBuilder::door_event(
                        config.device_id, sequence.boot_id(), event_seq, ts, open,
                        sequence.event_id(config.device_id, event_seq)), event_seq});
                    char local_time[32]{}; TimeSync::vietnam_datetime(local_time, sizeof(local_time));
                    ESP_LOGI(TAG, "[%s] Door transition=%s event=%s seq=%llu",
                        local_time, open ? "OPEN" : "CLOSED", open ? "DOOR_OPENED" : "DOOR_CLOSED",
                        static_cast<unsigned long long>(event_seq));
                } else {
                    ESP_LOGE(TAG, "Door event queue full; state=%s", open ? "OPEN" : "CLOSED");
                }
            }
            if (!pending_events.empty() && mqtt.publish(
                    nw::topic(config.org_code, config.site_code, config.device_id, "event"),
                    pending_events.front().payload, pending_events.front().seq)) pending_events.pop_front();
            if (pending_periodic.empty() && deadline_reached(now, next_door_heartbeat) && timestamp(ts)) {
                do next_door_heartbeat += pdMS_TO_TICKS(config.heartbeat_seconds * 1000U);
                while (deadline_reached(now, next_door_heartbeat));
                pending_periodic_seq = sequence.next();
                pending_periodic = PayloadBuilder::door_heartbeat(config.device_id, sequence.boot_id(),
                    pending_periodic_seq, ts, door.is_open());
                pending_periodic_topic = nw::topic(config.org_code, config.site_code,
                                                    config.device_id, "telemetry");
                char local_time[32]{}; TimeSync::vietnam_datetime(local_time, sizeof(local_time));
                ESP_LOGI(TAG, "[%s] %s | DOOR: %s | HEARTBEAT | seq=%llu",
                    local_time, config.device_id.c_str(), door.is_open() ? "OPEN" : "CLOSED",
                    static_cast<unsigned long long>(pending_periodic_seq));
            }
        }
        if (!pending_periodic.empty() && mqtt.publish(pending_periodic_topic,
                                                      pending_periodic, pending_periodic_seq)) {
            pending_periodic.clear(); pending_periodic_topic.clear();
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
