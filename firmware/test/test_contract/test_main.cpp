#include <unity.h>
#include "message_contract.hpp"

void test_topic_contract() {
    TEST_ASSERT_EQUAL_STRING("nw/v1/demo/demo-site/TH-001/telemetry",
        nw::topic("demo", "demo-site", "TH-001", "telemetry").c_str());
    TEST_ASSERT_TRUE(nw::topic("hainam", "hq", "DR-001", "event").size() > 0);
    TEST_ASSERT_TRUE(nw::topic("bad/org", "hq", "DR-001", "event").empty());
    TEST_ASSERT_TRUE(nw::topic("demo", "hq", "DR-001", "unknown").empty());
}
void test_event_id_is_stable_for_same_sequence() {
    TEST_ASSERT_EQUAL_STRING("DR-001-abc123-42", nw::event_id("DR-001", "abc123", 42).c_str());
}
void test_inflight_limit_and_puback() {
    nw::InflightTracker<3> tracker;
    TEST_ASSERT_TRUE(tracker.add(11, 1)); TEST_ASSERT_TRUE(tracker.add(12, 2));
    TEST_ASSERT_TRUE(tracker.add(13, 3)); TEST_ASSERT_TRUE(tracker.full());
    TEST_ASSERT_FALSE(tracker.add(14, 4));
    TEST_ASSERT_TRUE(tracker.acknowledge(12)); TEST_ASSERT_EQUAL(2, tracker.size());
    TEST_ASSERT_TRUE(tracker.add(14, 4));
}
int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_topic_contract);
    RUN_TEST(test_event_id_is_stable_for_same_sequence);
    RUN_TEST(test_inflight_limit_and_puback);
    return UNITY_END();
}
