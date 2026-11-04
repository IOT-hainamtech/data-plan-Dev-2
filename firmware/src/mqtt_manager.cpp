#include "mqtt_manager.hpp"
#include "esp_log.h"

bool MqttManager::begin(const AppConfig& c, const std::string& offline) {
    lock_ = xSemaphoreCreateMutex();
    will_topic_ = nw::topic(c.org_code, c.site_code, c.device_id, "status");
    will_payload_ = offline;
    esp_mqtt_client_config_t cfg{};
    cfg.broker.address.uri = c.mqtt_uri.c_str();
    cfg.broker.verification.certificate = c.mqtt_ca.c_str();
    cfg.credentials.username = c.mqtt_username.c_str();
    cfg.credentials.authentication.password = c.mqtt_password.c_str();
    // One stable identity per provisioned device. This also makes broker-side
    // duplicate-client diagnostics deterministic.
    cfg.credentials.client_id = c.device_id.c_str();
    cfg.session.protocol_ver = MQTT_PROTOCOL_V_3_1_1;
    cfg.session.keepalive = c.mqtt_keepalive_seconds;
    cfg.session.disable_clean_session = false;
    cfg.session.last_will.topic = will_topic_.c_str();
    cfg.session.last_will.msg = will_payload_.c_str();
    cfg.session.last_will.qos = 1;
    cfg.session.last_will.retain = true;
    // ESP-MQTT is the only reconnect owner. A second application reconnect
    // loop can overlap an in-progress TLS handshake and take over its own
    // broker session.
    cfg.network.disable_auto_reconnect = false;
    cfg.outbox.limit = 8192;
    client_ = esp_mqtt_client_init(&cfg);
    if (!client_) return false;
    esp_mqtt_client_register_event(client_, MQTT_EVENT_ANY, &event_thunk, this);
    return esp_mqtt_client_start(client_) == ESP_OK;
}
bool MqttManager::publish(const std::string& topic, const std::string& payload, uint64_t seq,
                          bool retain, bool durable, int qos) {
    if (!connected_ || !lock_) return false;
    if (!durable) {
        // Presence uses its own QoS 1 lane. ESP-MQTT owns retransmission and
        // PUBACK, while the application business inflight window remains free.
        return esp_mqtt_client_enqueue(client_, topic.c_str(), payload.c_str(),
                                       payload.size(), qos, retain, false) >= 0;
    }
    xSemaphoreTake(lock_, portMAX_DELAY);
    // Durable business messages retain application PUBACK tracking.
    if (inflight_.full()) { xSemaphoreGive(lock_); return false; }
    int id = esp_mqtt_client_enqueue(client_, topic.c_str(), payload.c_str(), payload.size(),
                                     qos, retain, durable);
    bool accepted = id >= 0 && inflight_.add(id, seq);
    xSemaphoreGive(lock_);
    return accepted;
}
size_t MqttManager::inflight() const {
    if (!lock_) return 0;
    xSemaphoreTake(lock_, portMAX_DELAY); size_t count = inflight_.size(); xSemaphoreGive(lock_); return count;
}
void MqttManager::event_thunk(void* args, esp_event_base_t, int32_t, void* data) {
    static_cast<MqttManager*>(args)->handle(static_cast<esp_mqtt_event_handle_t>(data));
}
void MqttManager::handle(esp_mqtt_event_handle_t event) {
    if (event->event_id == MQTT_EVENT_CONNECTED) {
        connected_ = true; ++connection_generation_;
        ESP_LOGI("mqtt", "MQTT_CONNECTED");
    } else if (event->event_id == MQTT_EVENT_DISCONNECTED) {
        connected_ = false;
        xSemaphoreTake(lock_, portMAX_DELAY);
        const size_t pending = inflight_.size();
        // MQTT message IDs belong to the old transport session. Durable
        // business messages remain owned by ESP-MQTT's outbox, while presence
        // uses store=false and must never consume a slot after disconnect.
        inflight_.clear();
        xSemaphoreGive(lock_);
        ESP_LOGW("mqtt", "MQTT_DISCONNECTED; cleared_inflight=%u auto-reconnect scheduled",
                 static_cast<unsigned>(pending));
    } else if (event->event_id == MQTT_EVENT_PUBLISHED) {
        uint64_t seq = 0;
        xSemaphoreTake(lock_, portMAX_DELAY);
        bool acknowledged = inflight_.acknowledge(event->msg_id, &seq);
        xSemaphoreGive(lock_);
        if (acknowledged) {
            ESP_LOGI("mqtt", "PUBLISH_ACK seq=%llu msg_id=%d",
                     static_cast<unsigned long long>(seq), event->msg_id);
        }
    }
}
