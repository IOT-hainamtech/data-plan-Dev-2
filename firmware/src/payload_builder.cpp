#include "payload_builder.hpp"
#include "cJSON.h"
#include <cstdlib>

namespace {
cJSON* base(const std::string& device, const std::string& boot, uint64_t seq, const char* ts) {
    cJSON* root = cJSON_CreateObject();
    cJSON_AddNumberToObject(root, "schema_version", 1);
    cJSON_AddStringToObject(root, "device_id", device.c_str());
    cJSON_AddStringToObject(root, "boot_id", boot.c_str());
    cJSON_AddNumberToObject(root, "seq", static_cast<double>(seq));
    if (ts && *ts) {
        cJSON_AddStringToObject(root, "ts", ts);
        cJSON_AddStringToObject(root, "ts_source", "ntp");
    } else {
        cJSON_AddStringToObject(root, "ts_source", "none");
    }
    return root;
}
std::string finish(cJSON* root) {
    char* encoded = cJSON_PrintUnformatted(root);
    std::string result = encoded ? encoded : "";
    cJSON_free(encoded); cJSON_Delete(root); return result;
}
}
std::string PayloadBuilder::status(const std::string& device, const std::string& boot,
                                   uint64_t seq, const char* ts, const char* state,
                                   const char* fw_version, const char* reason,
                                   int rssi, const char* ip) {
    cJSON* root = base(device, boot, seq, ts);
    cJSON_AddStringToObject(root, "msg_kind", "status");
    cJSON_AddStringToObject(root, "state", state);
    cJSON_AddObjectToObject(root, "data");
    if (fw_version) cJSON_AddStringToObject(root, "fw_version", fw_version);
    if (reason) cJSON_AddStringToObject(root, "reason", reason);
    if (ip) {
        cJSON_AddNumberToObject(root, "rssi", rssi);
        cJSON_AddStringToObject(root, "ip", ip);
    }
    return finish(root);
}
std::string PayloadBuilder::telemetry(const std::string& device, const std::string& boot,
                                      uint64_t seq, const char* ts, float temperature,
                                      float humidity) {
    cJSON* root = base(device, boot, seq, ts);
    cJSON_AddStringToObject(root, "msg_kind", "measurement");
    cJSON* data = cJSON_AddObjectToObject(root, "data");
    cJSON_AddStringToObject(root, "quality", "good");
    cJSON_AddNumberToObject(data, "temperature", temperature);
    cJSON_AddNumberToObject(data, "humidity", humidity);
    return finish(root);
}
std::string PayloadBuilder::telemetry_error(const std::string& device, const std::string& boot,
                                            uint64_t seq, const char* ts) {
    cJSON* root = base(device, boot, seq, ts);
    cJSON_AddStringToObject(root, "msg_kind", "measurement");
    cJSON_AddObjectToObject(root, "data");
    cJSON_AddStringToObject(root, "quality", "error");
    return finish(root);
}
std::string PayloadBuilder::door_event(const std::string& device, const std::string& boot,
                                       uint64_t seq, const char* ts, bool open,
                                       const std::string& id) {
    cJSON* root = base(device, boot, seq, ts);
    cJSON_AddStringToObject(root, "msg_kind", "event");
    cJSON_AddStringToObject(root, "event_id", id.c_str());
    cJSON_AddStringToObject(root, "event_type", open ? "DOOR_OPENED" : "DOOR_CLOSED");
    cJSON* data = cJSON_AddObjectToObject(root, "data");
    cJSON_AddStringToObject(data, "door_state", open ? "open" : "closed");
    return finish(root);
}
std::string PayloadBuilder::door_heartbeat(const std::string& device, const std::string& boot,
                                           uint64_t seq, const char* ts, bool open) {
    cJSON* root = base(device, boot, seq, ts);
    cJSON_AddStringToObject(root, "msg_kind", "heartbeat");
    cJSON_AddStringToObject(root, "quality", "good");
    cJSON* data = cJSON_AddObjectToObject(root, "data");
    cJSON_AddStringToObject(data, "door_state", open ? "open" : "closed");
    return finish(root);
}
