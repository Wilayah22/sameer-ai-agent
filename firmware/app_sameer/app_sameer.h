/*
 * Sameer (سمير) — family conversation app for StackChan.
 *
 * Tap the screen or pat the head: Sameer fetches a question from the Sameer server,
 * says it out loud, then steps back and listens. Only silence/talking numbers leave the
 * device; audio never does. At the end the family rates the session 1-3 on screen.
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
    enum class State { Idle, Busy, Listening, Rating };

    std::atomic<State> _state{State::Idle};
    std::atomic<bool> _start_requested{false};
    std::atomic<bool> _end_requested{false};
    std::atomic<int> _rating{0};

    // UI changes requested by the session task, applied on the main loop under the LVGL lock.
    std::mutex _ui_mutex;
    std::vector<std::function<void()>> _ui_queue;
    void post_ui(std::function<void()> fn);

    void on_screen_tap();
    void show_rating_buttons(bool show);

    static void session_task(void* arg);
    void run_session();
    bool speak(const std::string& text);
};
