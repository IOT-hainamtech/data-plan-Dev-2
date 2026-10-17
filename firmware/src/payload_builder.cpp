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
    cJSON_AddStringToObject(root, "ts", ts);
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
                                   const char* quality, const char* door_state) {
    cJSON* root = base(device, boot, seq, ts);
    cJSON_AddStringToObject(root, "state", state);
    cJSON* data = cJSON_AddObjectToObject(root, "data");
    if (quality) cJSON_AddStringToObject(data, "quality", quality);
    if (door_state) cJSON_AddStringToObject(data, "door_state", door_state);
    return finish(root);
}
std::string PayloadBuilder::telemetry(const std::string& device, const std::string& boot,
                                      uint64_t seq, const char* ts, float temperature,
                                      float humidity) {
    cJSON* root = base(device, boot, seq, ts);
    cJSON* data = cJSON_AddObjectToObject(root, "data");
    cJSON_AddNumberToObject(data, "temperature", temperature);
    cJSON_AddNumberToObject(data, "humidity", humidity);
    return finish(root);
}
std::string PayloadBuilder::door_event(const std::string& device, const std::string& boot,
                                       uint64_t seq, const char* ts, bool open,
                                       const std::string& id) {
    cJSON* root = base(device, boot, seq, ts);
    cJSON_AddStringToObject(root, "event_id", id.c_str());
    cJSON_AddStringToObject(root, "event_type", open ? "DOOR_OPENED" : "DOOR_CLOSED");
    cJSON* data = cJSON_AddObjectToObject(root, "data");
    cJSON_AddStringToObject(data, "door_state", open ? "open" : "closed");
    return finish(root);
}
