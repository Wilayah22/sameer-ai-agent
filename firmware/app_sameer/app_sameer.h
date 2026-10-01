/*
 * Hiwar (حوار) — family conversation app for StackChan.
 *
 * Tap the screen or pat the head to start a live conversation, like a voice call: the server hands
 * the robot a short-lived Gemini Live token, and the robot streams the microphone straight to
 * Gemini and plays its voice as it arrives. Hiwar opens with a question, then talks with the
 * family until they say goodbye or tap to end. Audio is never stored; the dashboard gets numbers
 * only (turns, duration) and the robot's own opening question.
 *
 * If Live is unavailable, it falls back to the older turn-by-turn flow (/session_intro, /converse).
 */
#pragma once
#include <mooncake.h>
#include <atomic>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

class AppSameer : public mooncake::AppAbility {
public:
    AppSameer();

    void onCreate() override;
    void onOpen() override;
    void onRunning() override;
    void onClose() override;

    // Position in the launcher, used to come back here after the warm reboot on close.
    static constexpr int LauncherIndex = 7;

private:
    enum class State { Idle, Busy, Listening, Live };

    std::atomic<State> _state{State::Idle};
    std::atomic<bool> _start_requested{false};
    std::atomic<bool> _end_requested{false};
    std::atomic<bool> _interrupt_requested{false};
    std::atomic<bool> _robot_speaking{false};

    // UI changes requested by the session task, applied on the main loop under the LVGL lock.
    std::mutex _ui_mutex;
    std::vector<std::function<void()>> _ui_queue;
    void post_ui(std::function<void()> fn);

    void on_screen_tap();

    static void session_task(void* arg);
    void run_session();
    bool run_live_session();      // false when Live could not start (nothing was said yet)
    void run_classic_session();
    void say_thanks();
    bool speak(const std::string& text);
    void play_pcm(const std::string& pcm);
    std::string converse_turn(const std::string& session_id, const std::vector<int16_t>& audio);
};
