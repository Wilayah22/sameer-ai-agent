/*
 * Sameer (سمير) — family conversation app for StackChan.
 *
 * Tap the screen or pat the head: Sameer fetches a question from the Sameer server and
 * says it out loud. Then it listens: each time someone finishes speaking, that short turn is
 * sent to the server, which understands it in memory (never stored) and decides whether the
 * robot replies, stays quiet, or wraps up. At the end the family rates the session 1-3.
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
    void play_pcm(const std::string& pcm);
    std::string converse_turn(const std::string& session_id, const std::vector<int16_t>& audio);
};
