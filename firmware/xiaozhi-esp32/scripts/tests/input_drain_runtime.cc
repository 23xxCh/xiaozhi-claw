#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <functional>
#include <future>
#include <memory>
#include <mutex>
#include <string>
#include <vector>
using namespace std::chrono_literals;
constexpr int AS_EVENT_AUDIO_PROCESSOR_RUNNING = 1;
void xEventGroupClearBits(int& bits, int mask) { bits &= ~mask; }
struct Engine {
    std::promise<void> stopped;
    void FinishVoiceProcessing() { stopped.set_value(); }
};
struct AudioService {
    bool audio_engine_initialized_ = true;
    std::unique_ptr<Engine> audio_engine_ = std::make_unique<Engine>();
    int event_group_ = 0;
    std::mutex audio_queue_mutex_;
    std::condition_variable audio_queue_cv_;
    std::deque<int> audio_encode_queue_, audio_send_queue_;
    bool encode_in_flight_ = false;
    std::atomic<bool> send_queue_overflowed_{false};
    bool FinishVoiceInput();
    void EnableAudioTesting(bool) {}
    std::unique_ptr<int> PopPacketFromSendQueue() {
        std::lock_guard<std::mutex> lock(audio_queue_mutex_);
        if (audio_send_queue_.empty()) return nullptr;
        auto p = std::make_unique<int>(audio_send_queue_.front());
        audio_send_queue_.pop_front();
        return p;
    }
};
enum { kDeviceStateAudioTesting, kDeviceStateWifiConfiguring, kDeviceStateListening, kDeviceStateIdle };
struct Protocol {
    std::vector<int> events;
    bool fail_send = false;
    bool SendAudio(std::unique_ptr<int> p) {
        if (fail_send) return false;
        events.push_back(*p);
        return true;
    }
    void SendStopListening() { events.push_back(-1); }
};
struct Application {
    AudioService audio_service_;
    Protocol transport;
    Protocol* protocol_ = &transport;
    int state = kDeviceStateListening;
    std::string error;
    int GetDeviceState() { return state; }
    void SetDeviceState(int next) { state = next; }
    bool IsControlChannelReady() { return true; }
    void AbortDialogueToStandby(const char* reason, bool) { error = reason; state = kDeviceStateIdle; }
    void HandleStopListeningEvent();
};
struct LiteAudioEngine {
    std::mutex output_mutex_;
    bool voice_processing_enabled_ = true;
    std::vector<int16_t> output_buffer_;
    int frame_samples_ = 960;
    std::function<void(std::vector<int16_t>&&)> output_callback_;
    void FinishVoiceProcessing();
};
struct AfeAudioEngine {
    std::mutex voice_output_mutex_;
    static constexpr int kVoiceProcessingEnabled = 1;
    int event_group_ = 1;
    bool is_speaking_ = true;
    std::atomic<bool> output_reset_pending_{false};
    std::vector<int16_t> output_buffer_;
    int frame_samples_ = 960;
    std::function<void(std::vector<int16_t>&&)> output_callback_;
    void UpdateActiveState() {}
    void FinishVoiceProcessing();
};
// PRODUCTION_METHODS
int main() {
    Application app;
    // Queue empty is not drained: last frame is still in the codec worker.
    app.audio_service_.encode_in_flight_ = true;
    app.audio_service_.audio_send_queue_.push_back(1);
    auto stopped = app.audio_service_.audio_engine_->stopped.get_future();
    auto finish = std::async(std::launch::async, [&] { app.HandleStopListeningEvent(); });
    stopped.wait();
    assert(finish.wait_for(30ms) == std::future_status::timeout);
    {
        std::lock_guard<std::mutex> lock(app.audio_service_.audio_queue_mutex_);
        app.audio_service_.audio_send_queue_.push_back(2);
        app.audio_service_.encode_in_flight_ = false;
        app.audio_service_.audio_queue_cv_.notify_all();
    }
    finish.get();
    assert((app.transport.events == std::vector<int>{1, 2, -1}));
    app.HandleStopListeningEvent(); // duplicate stop cannot commit twice
    assert(app.transport.events.size() == 3);

    Application timeout;
    timeout.audio_service_.audio_encode_queue_.push_back(1);
    auto start = std::chrono::steady_clock::now();
    timeout.HandleStopListeningEvent();
    assert(timeout.error == "input-drain-failed" && timeout.transport.events.empty());
    assert(std::chrono::steady_clock::now() - start < 3s);

    Application overflow;
    overflow.audio_service_.send_queue_overflowed_ = true;
    overflow.HandleStopListeningEvent();
    assert(overflow.error == "input-drain-failed" && overflow.transport.events.empty());
    Application network;
    network.audio_service_.audio_send_queue_.push_back(1);
    network.transport.fail_send = true;
    network.HandleStopListeningEvent();
    assert(network.error == "input-send-failed" && network.transport.events.empty());

    LiteAudioEngine tail;
    tail.output_buffer_ = {31, 32, 33};
    std::vector<int16_t> result;
    tail.output_callback_ = [&](auto&& pcm) { result = std::move(pcm); };
    tail.FinishVoiceProcessing();
    assert(result.size() == 960 && result[0] == 31 && result[2] == 33 && result[3] == 0);
    assert(!tail.voice_processing_enabled_ && tail.output_buffer_.empty());
    tail.FinishVoiceProcessing();
    assert(result[0] == 31); // no second output

    AfeAudioEngine afe;
    std::unique_lock<std::mutex> producing(afe.voice_output_mutex_);
    auto afe_finish = std::async(std::launch::async, [&] { afe.FinishVoiceProcessing(); });
    assert(afe_finish.wait_for(30ms) == std::future_status::timeout);
    afe.output_buffer_ = {41, 42}; // the VAD-ending producer finishes its PCM first
    afe.output_callback_ = [&](auto&& pcm) { result = std::move(pcm); };
    producing.unlock();
    afe_finish.get();
    assert(result.size() == 960 && result[0] == 41 && result[1] == 42 && result[2] == 0);
    assert(!afe.is_speaking_ && afe.event_group_ == 0);
}
