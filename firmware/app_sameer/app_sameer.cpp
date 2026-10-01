/*
 * Hiwar (حوار) — family conversation app for StackChan.
 * See app_sameer.h for the flow. Server API: sameer-ai-agent/README.md.
 */
#include "app_sameer.h"
#include <hal/hal.h>
#include <mooncake.h>
#include <mooncake_log.h>
#include <assets/assets.h>
#include <smooth_lvgl.hpp>
#include <stackchan/stackchan.h>
#include <apps/common/common.h>
#include <board.h>
#include <http.h>
#include <web_socket.h>
#include <mbedtls/base64.h>
#include <audio/audio_codec.h>
#include <cJSON.h>
#include <esp_heap_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <sdkconfig.h>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <deque>

using namespace mooncake;
using namespace smooth_ui_toolkit::lvgl_cpp;
using namespace stackchan;

static const char* _tag = "HIWAR";

namespace {

constexpr uint32_t ThemeColor = 0xD9B84A;  // Sameer gold
constexpr uint32_t ThemeDark  = 0x0A1930;  // Sameer navy

constexpr int IntroTimeoutMs    = 90000;  // first request may wake a sleeping free Render instance
constexpr int RequestTimeoutMs  = 30000;
constexpr int KeepAliveMs       = 10 * 60 * 1000;  // free Render sleeps after 15 idle minutes
constexpr int ChunkMs           = 100;    // mic analysis window
constexpr int TalkingHoldMs     = 1500;   // still "talking" this long after the last voiced chunk
constexpr int SilenceCheckSecs  = 20;     // ask the server early once silence is this long
constexpr int MinVoiceRms       = 250;
constexpr float VoiceOverNoise  = 2.5f;

// Spoken turns: a turn ends after this much silence, and is sent only if it had enough speech.
constexpr int TurnEndSilenceMs  = 900;
constexpr int MinTurnSpeechMs   = 600;
constexpr int MaxTurnMs         = 12000;
constexpr int PreRollChunks     = 3;      // keep the start of the first word
constexpr int TurnSampleRate    = 12000;  // mic is 24 kHz; every 2 samples are averaged into 1

// Head angles are in tenths of a degree; pitch runs 30 (level) to 870 (straight up).
constexpr int PitchFacingFamily = 150;
constexpr int PitchThinking     = 380;
constexpr int PitchNodTop       = 260;

constexpr int ModePanelMs = 10000;  // the mode buttons hide again after this long

// Same words as the server's wrap-up, used when the family ends the session from the screen.
constexpr const char* WrapUpText = "كانت جلسة جميلة! شكرًا لكم، ونلتقي في حوار قادم.";

std::unique_ptr<Container> _mode_panel;
std::vector<std::unique_ptr<Button>> _mode_buttons;

std::string server_url(const std::string& path)
{
    return std::string(CONFIG_SAMEER_SERVER_URL) + path;
}

// Read the whole response body. Http::ReadAll() can't be used here: the client only buffers
// 8 KB and ReadAll() waits for the end without draining it, so larger bodies (speech) stall.
bool read_body(Http& http, std::string& out)
{
    out.clear();
    out.reserve(http.GetBodyLength());
    std::vector<char> buffer(4096);
    while (true) {
        int n = http.Read(buffer.data(), buffer.size());
        if (n < 0) {
            return false;
        }
        if (n == 0) {
            return true;
        }
        out.append(buffer.data(), n);
    }
}

// POST JSON to the Sameer server. Returns the HTTP status (0 when the request failed).
int post_json(const std::string& path, const std::string& body, std::string& response, int timeoutMs)
{
    auto http = Board::GetInstance().GetNetwork()->CreateHttp(0);
    http->SetTimeout(timeoutMs);
    http->SetHeader("Content-Type", "application/json");
    if (std::string(CONFIG_SAMEER_DEVICE_TOKEN).size() > 0) {
        http->SetHeader("X-Device-Token", CONFIG_SAMEER_DEVICE_TOKEN);
    }
    http->SetContent(std::string(body));
    if (!http->Open("POST", server_url(path))) {
        mclog::tagError(_tag, "POST {} failed to connect", path);
        return 0;
    }
    int status = http->GetStatusCode();
    if (!read_body(*http, response)) {
        mclog::tagError(_tag, "POST {} failed while reading the response", path);
        status = 0;
    }
    http->Close();
    if (status != 200) {
        mclog::tagError(_tag, "POST {} -> {}: {}", path, status, response);
    }
    return status;
}

// Wake the server (and keep it awake) so the first question doesn't wait for a cold start.
void ping_server()
{
    auto http = Board::GetInstance().GetNetwork()->CreateHttp(0);
    http->SetTimeout(IntroTimeoutMs);
    if (http->Open("GET", server_url("/ping"))) {
        std::string ignored;
        read_body(*http, ignored);
        mclog::tagInfo(_tag, "ping -> {}", http->GetStatusCode());
    } else {
        mclog::tagError(_tag, "ping failed to connect");
    }
    http->Close();
}

std::string json_string(cJSON* root, const char* key)
{
    cJSON* item = cJSON_GetObjectItemCaseSensitive(root, key);
    return cJSON_IsString(item) && item->valuestring ? item->valuestring : "";
}

std::string to_json(cJSON* root)
{
    char* text = cJSON_PrintUnformatted(root);
    std::string out = text ? text : "{}";
    cJSON_free(text);
    cJSON_Delete(root);
    return out;
}

}  // namespace

AppSameer::AppSameer()
{
    setAppInfo().name = "HIWAR";
    static auto icon  = assets::get_image("icon_ai_agent.bin");
    setAppInfo().icon = (void*)&icon;
    static uint32_t theme_color = ThemeColor;
    setAppInfo().userData       = (void*)&theme_color;
}

void AppSameer::onCreate()
{
    mclog::tagInfo(_tag, "on create");
}

void AppSameer::onOpen()
{
    mclog::tagInfo(_tag, "on open, server: {}", CONFIG_SAMEER_SERVER_URL);

    std::unique_ptr<view::LoadingPage> loading_page;
    {
        LvglLockGuard lock;
        loading_page = std::make_unique<view::LoadingPage>(ThemeDark, ThemeColor);
    }

    GetHAL().startNetwork([&](std::string_view msg) {
        LvglLockGuard lock;
        loading_page->setMessage(msg);
    });

    LvglLockGuard lock;
    loading_page.reset();

    auto avatar = std::make_unique<avatar::DefaultAvatar>();
    avatar->init(lv_screen_active());
    avatar->getPanel()->onClick().connect([this]() { on_screen_tap(); });
    GetStackChan().attachAvatar(std::move(avatar));

    GetStackChan().addModifier(std::make_unique<BlinkModifier>());
    GetStackChan().addModifier(std::make_unique<BreathModifier>());
    GetStackChan().addModifier(std::make_unique<IdleMotionModifier>(6000, 12000));

    // Patting the head starts a session too.
    GetHAL().onHeadPetGesture.connect([this](HeadPetGesture gesture) {
        if (gesture == HeadPetGesture::Press) {
            on_head_pat();
        }
    });

    view::create_home_indicator([this]() { close(); }, 0xF1E2A8, ThemeDark);
    view::create_status_bar(0xF1E2A8, ThemeDark);

    // Network and audio block for seconds at a time, so the session runs on its own task.
    // It also wakes the server right away, while the family is still getting ready.
    xTaskCreatePinnedToCore(session_task, "hiwar", 16 * 1024, this, 4, nullptr, 1);
}

void AppSameer::onRunning()
{
    std::vector<std::function<void()>> pending;
    {
        std::lock_guard<std::mutex> lock(_ui_mutex);
        pending.swap(_ui_queue);
    }

    LvglLockGuard lvgl_lock;
    for (auto& fn : pending) {
        fn();
    }
    GetStackChan().update();
    view::update_home_indicator();
    view::update_status_bar();
}

void AppSameer::onClose()
{
    mclog::tagInfo(_tag, "on close");
    {
        LvglLockGuard lock;
        show_mode_panel(false);
        GetHAL().onHeadPetGesture.clear();
        GetStackChan().clearModifiers();
        GetStackChan().resetAvatar();
        view::destroy_home_indicator();
        view::destroy_status_bar();
    }
    // Restart back to this icon; this also stops the session task and frees the audio codec.
    GetHAL().requestWarmReboot(LauncherIndex);
}

void AppSameer::post_ui(std::function<void()> fn)
{
    std::lock_guard<std::mutex> lock(_ui_mutex);
    _ui_queue.push_back(std::move(fn));
}

void AppSameer::on_screen_tap()
{
    switch (_state.load()) {
        case State::Idle:
            // Show the two modes; the choice starts the session.
            _state          = State::Choosing;
            _panel_shown_at = GetHAL().millis();
            post_ui([this]() { show_mode_panel(true); });
            break;
        case State::Choosing:
            _state = State::Idle;  // tapping the face again closes the choice
            post_ui([this]() { show_mode_panel(false); });
            break;
        case State::Listening:
            _end_requested = true;  // the family can end the session early
            break;
        case State::Live:
            // Tapping while Hiwar talks cuts it short; tapping while it listens ends the call.
            if (_robot_speaking) {
                _interrupt_requested = true;
            } else {
                _end_requested = true;
            }
            break;
        default:
            break;
    }
}

// Patting the head is the quick way to start a family conversation.
void AppSameer::on_head_pat()
{
    State state = _state.load();
    if (state == State::Idle || state == State::Choosing) {
        choose_mode(false);
    } else {
        on_screen_tap();
    }
}

void AppSameer::choose_mode(bool personal)
{
    _personal        = personal;
    _state           = State::Busy;
    _start_requested = true;
    post_ui([this]() { show_mode_panel(false); });  // not inside the button's own click handler
}

// Must be called with the LVGL lock held.
void AppSameer::show_mode_panel(bool show)
{
    _mode_buttons.clear();
    _mode_panel.reset();
    if (!show) {
        return;
    }

    _mode_panel = std::make_unique<Container>(lv_screen_active());
    _mode_panel->setSize(300, 84);
    _mode_panel->align(LV_ALIGN_BOTTOM_MID, 0, -8);
    _mode_panel->setBgOpa(0);
    _mode_panel->setBorderWidth(0);
    _mode_panel->setPaddingAll(0);

    struct Choice {
        const char* label;
        uint32_t color;
        bool personal;
    };
    const Choice choices[] = {{LV_SYMBOL_HOME " Family", ThemeColor, false}, {"Just me", 0xF1E2A8, true}};
    for (int i = 0; i < 2; ++i) {
        auto button = std::make_unique<Button>(_mode_panel->get());
        button->setSize(140, 72);
        button->align(i == 0 ? LV_ALIGN_LEFT_MID : LV_ALIGN_RIGHT_MID, 0, 0);
        button->setBgColor(lv_color_hex(choices[i].color));
        button->setRadius(18);
        button->label().setText(choices[i].label);
        button->label().setTextFont(&lv_font_montserrat_24);
        button->label().setTextColor(lv_color_hex(ThemeDark));
        bool personal = choices[i].personal;
        button->onClick().connect([this, personal]() {
            if (_state == State::Choosing) {
                choose_mode(personal);
            }
        });
        _mode_buttons.push_back(std::move(button));
    }
}

/* -------------------------------------------------------------------------- */
/*                                Session task                                */
/* -------------------------------------------------------------------------- */

void AppSameer::session_task(void* arg)
{
    auto* app = static_cast<AppSameer*>(arg);
    ping_server();
    TickType_t last_ping = xTaskGetTickCount();
    while (true) {
        if (app->_start_requested.exchange(false)) {
            app->run_session();
            app->_state = State::Idle;
            app->_end_requested       = false;
            app->_interrupt_requested = false;
            last_ping = xTaskGetTickCount();
        } else if (app->_state == State::Choosing && GetHAL().millis() - app->_panel_shown_at >= ModePanelMs) {
            app->_state = State::Idle;
            app->post_ui([app]() { app->show_mode_panel(false); });
        } else if (xTaskGetTickCount() - last_ping >= pdMS_TO_TICKS(KeepAliveMs)) {
            ping_server();
            last_ping = xTaskGetTickCount();
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

// Fetch Arabic speech for `text` from the server and play it with a talking face.
bool AppSameer::speak(const std::string& text)
{
    cJSON* body = cJSON_CreateObject();
    cJSON_AddStringToObject(body, "text", text.c_str());

    auto http = Board::GetInstance().GetNetwork()->CreateHttp(0);
    http->SetTimeout(RequestTimeoutMs);
    http->SetHeader("Content-Type", "application/json");
    if (std::string(CONFIG_SAMEER_DEVICE_TOKEN).size() > 0) {
        http->SetHeader("X-Device-Token", CONFIG_SAMEER_DEVICE_TOKEN);
    }
    http->SetContent(to_json(body));
    if (!http->Open("POST", server_url("/tts")) || http->GetStatusCode() != 200) {
        mclog::tagError(_tag, "tts request failed");
        http->Close();
        return false;
    }

    auto codec = Board::GetInstance().GetAudioCodec();
    std::string rate = http->GetResponseHeader("X-Sample-Rate");
    if (!rate.empty() && std::atoi(rate.c_str()) != codec->output_sample_rate()) {
        mclog::tagWarn(_tag, "tts sample rate {} differs from speaker {}", rate, codec->output_sample_rate());
    }

    // Download first, then play, so a slow network never makes Sameer stutter.
    std::string pcm;
    if (!read_body(*http, pcm)) {
        mclog::tagError(_tag, "tts download failed after {} bytes", pcm.size());
        http->Close();
        return false;
    }
    http->Close();
    mclog::tagInfo(_tag, "tts: {} bytes", pcm.size());
    size_t samples = pcm.size() / 2;
    if (samples == 0) {
        return false;
    }

    play_pcm(pcm);
    return true;
}

// Play 16-bit mono PCM at the speaker's rate, with a talking mouth.
void AppSameer::play_pcm(const std::string& pcm)
{
    auto codec     = Board::GetInstance().GetAudioCodec();
    size_t samples = pcm.size() / 2;
    if (samples == 0) {
        return;
    }

    uint32_t duration_ms = samples * 1000 / codec->output_sample_rate();
    post_ui([duration_ms]() { GetStackChan().addModifier(std::make_unique<SpeakingModifier>(duration_ms + 200)); });

    codec->EnableOutput(true);
    std::vector<int16_t> chunk;
    const size_t chunk_samples = 1024;
    for (size_t offset = 0; offset < samples; offset += chunk_samples) {
        size_t n = std::min(chunk_samples, samples - offset);
        chunk.assign(reinterpret_cast<const int16_t*>(pcm.data()) + offset,
                     reinterpret_cast<const int16_t*>(pcm.data()) + offset + n);
        codec->OutputData(chunk);
    }
    vTaskDelay(pdMS_TO_TICKS(150));
    codec->EnableOutput(false);
}

// Send one spoken turn (12 kHz mono) to the server and play the robot's reply, if any.
// Returns the server's action: "reply", "listen", "wrap_up", or "" when the request failed.
std::string AppSameer::converse_turn(const std::string& session_id, const std::vector<int16_t>& audio)
{
    const uint32_t data_bytes = audio.size() * 2;
    std::string wav;
    wav.reserve(44 + data_bytes);
    auto put32 = [&wav](uint32_t v) { for (int i = 0; i < 4; ++i) wav.push_back(char((v >> (8 * i)) & 0xFF)); };
    auto put16 = [&wav](uint16_t v) { for (int i = 0; i < 2; ++i) wav.push_back(char((v >> (8 * i)) & 0xFF)); };
    wav += "RIFF";
    put32(36 + data_bytes);
    wav += "WAVEfmt ";
    put32(16);
    put16(1);  // PCM
    put16(1);  // mono
    put32(TurnSampleRate);
    put32(TurnSampleRate * 2);
    put16(2);
    put16(16);
    wav += "data";
    put32(data_bytes);
    wav.append(reinterpret_cast<const char*>(audio.data()), data_bytes);

    auto http = Board::GetInstance().GetNetwork()->CreateHttp(0);
    http->SetTimeout(RequestTimeoutMs);
    http->SetHeader("Content-Type", "audio/wav");
    if (std::string(CONFIG_SAMEER_DEVICE_TOKEN).size() > 0) {
        http->SetHeader("X-Device-Token", CONFIG_SAMEER_DEVICE_TOKEN);
    }
    http->SetContent(std::move(wav));
    if (!http->Open("POST", server_url("/converse?session_id=" + session_id))) {
        mclog::tagError(_tag, "converse request failed to connect");
        return "";
    }
    if (http->GetStatusCode() != 200) {
        mclog::tagError(_tag, "converse -> {}", http->GetStatusCode());
        http->Close();
        return "";
    }
    std::string action = http->GetResponseHeader("X-Action");
    std::string pcm;
    bool ok = read_body(*http, pcm);
    http->Close();
    mclog::tagInfo(_tag, "turn: {} ({} bytes of reply)", action, pcm.size());
    if (ok && !pcm.empty()) {
        play_pcm(pcm);
    }
    return action;
}

void AppSameer::run_session()
{
    _state = State::Busy;
    post_ui([]() {
        GetStackChan().avatar().setEmotion(avatar::Emotion::Doubt);
        GetStackChan().motion().moveWithSpeed(0, PitchThinking, 300);  // look up, thinking
    });

    bool personal = _personal;
    if (run_live_session(personal)) {
        return;
    }
    _state = State::Busy;
    if (personal) {
        // The one-to-one chat needs Live; there is no turn-by-turn version of it.
        mclog::tagWarn(_tag, "personal conversation unavailable");
        post_ui([]() { GetStackChan().addModifier(std::make_unique<TimedEmotionModifier>(avatar::Emotion::Sad, 3000)); });
        speak("عذرًا، لا أستطيع بدء الحوار الشخصي الآن. جرّب بعد قليل.");
        return;
    }
    mclog::tagWarn(_tag, "live conversation unavailable, using turn-by-turn mode");
    run_classic_session();
}

// A happy little nod as thanks, at the end of every session.
void AppSameer::say_thanks()
{
    post_ui([]() {
        GetStackChan().addModifier(std::make_unique<TimedEmotionModifier>(avatar::Emotion::Happy, 3000));
        GetStackChan().motion().moveWithSpeed(0, PitchNodTop, 500);
    });
    vTaskDelay(pdMS_TO_TICKS(500));
    post_ui([]() { GetStackChan().motion().moveWithSpeed(0, 30, 500); });
    vTaskDelay(pdMS_TO_TICKS(500));
    post_ui([]() {
        GetStackChan().motion().moveWithSpeed(0, PitchFacingFamily, 400);
        GetStackChan().avatar().setEmotion(avatar::Emotion::Neutral);
    });
}

// The older flow: one question, then each spoken turn is uploaded and answered (slower).
void AppSameer::run_classic_session()
{
    // 1. Start the session: the server picks the category and question.
    std::string response;
    if (post_json("/session_intro", "{}", response, IntroTimeoutMs) != 200) {
        post_ui([]() { GetStackChan().addModifier(std::make_unique<TimedEmotionModifier>(avatar::Emotion::Sad, 3000)); });
        return;
    }
    cJSON* intro = cJSON_Parse(response.c_str());
    std::string session_id = json_string(intro, "session_id");
    std::string question   = json_string(intro, "question");
    cJSON_Delete(intro);
    if (session_id.empty() || question.empty()) {
        return;
    }
    mclog::tagInfo(_tag, "session {}", session_id);

    post_ui([]() {
        GetStackChan().avatar().setEmotion(avatar::Emotion::Happy);
        GetStackChan().motion().moveWithSpeed(0, PitchFacingFamily, 400);
    });
    speak(question);

    // 2. Listen. Loudness is measured on the device; each finished spoken turn goes to the server,
    //    which understands it in memory (audio is never stored) and decides how the robot responds.
    auto codec = Board::GetInstance().GetAudioCodec();
    const int channels       = std::max(codec->input_channels(), 1);
    const int chunk_frames   = codec->input_sample_rate() * ChunkMs / 1000;
    std::vector<int16_t> buffer(chunk_frames * channels);

    _state = State::Listening;
    post_ui([]() { GetStackChan().avatar().setEmotion(avatar::Emotion::Neutral); });
    codec->EnableInput(true);
    GetHAL().showRgbColor(0x60, 0x48, 0x08);  // soft gold: the microphone is listening

    uint32_t start          = GetHAL().millis();
    uint32_t last_voice     = start;
    uint32_t last_check     = start;
    uint32_t next_check     = start + 10000;
    uint32_t silent_ms      = 0;
    uint32_t speaking_ms    = 0;  // time Sameer itself spent talking (follow-ups)
    float noise_floor       = 0;
    int calibration_chunks  = 0;
    bool wrapped_up         = false;

    bool in_turn            = false;
    uint32_t turn_start     = 0;
    int voiced_chunks       = 0;
    std::vector<int16_t> turn_audio;
    turn_audio.reserve(TurnSampleRate * MaxTurnMs / 1000 + TurnSampleRate);
    std::deque<std::vector<int16_t>> preroll;

    while (!wrapped_up) {
        if (!codec->InputData(buffer)) {
            vTaskDelay(pdMS_TO_TICKS(ChunkMs));
            continue;
        }

        double sum = 0;
        for (int i = 0; i < chunk_frames; ++i) {
            double s = buffer[i * channels];
            sum += s * s;
        }
        float rms = std::sqrt(sum / chunk_frames);

        // Learn the room's background level during the first second.
        if (calibration_chunks < 10) {
            noise_floor = calibration_chunks == 0 ? rms : std::min(noise_floor, rms);
            ++calibration_chunks;
        }
        bool voiced = rms > std::max(noise_floor * VoiceOverNoise, (float)MinVoiceRms);
        if (!voiced) {
            noise_floor = noise_floor * 0.98f + rms * 0.02f;  // follow slow changes in background noise
        }

        uint32_t now = GetHAL().millis();
        if (voiced) {
            last_voice = now;
        } else {
            silent_ms += ChunkMs;
        }
        bool talking       = now - last_voice < TalkingHoldMs;
        int silence_secs   = (now - last_voice) / 1000;
        int elapsed_secs   = (now - start - speaking_ms) / 1000;
        bool long_silence  = silence_secs >= SilenceCheckSecs && now - last_check >= 5000;

        if (_end_requested) {
            codec->EnableInput(false);
            GetHAL().showRgbColor(0, 0, 0);
            speak(WrapUpText);
            break;
        }
        // 3. Capture spoken turns; the server decides whether the robot replies, stays quiet or wraps up.
        std::vector<int16_t> decimated(chunk_frames / 2);
        for (int i = 0; i < chunk_frames / 2; ++i) {
            int32_t a = buffer[(2 * i) * channels], b = buffer[(2 * i + 1) * channels];
            decimated[i] = static_cast<int16_t>((a + b) / 2);
        }
        if (!in_turn) {
            if (voiced) {
                in_turn       = true;
                turn_start    = now;
                voiced_chunks = 1;
                turn_audio.clear();
                for (auto& earlier : preroll) {
                    turn_audio.insert(turn_audio.end(), earlier.begin(), earlier.end());
                }
                turn_audio.insert(turn_audio.end(), decimated.begin(), decimated.end());
            } else {
                preroll.push_back(std::move(decimated));
                if (preroll.size() > PreRollChunks) {
                    preroll.pop_front();
                }
            }
        } else {
            turn_audio.insert(turn_audio.end(), decimated.begin(), decimated.end());
            if (voiced) {
                ++voiced_chunks;
            }
            bool turn_over = now - last_voice >= TurnEndSilenceMs || now - turn_start >= MaxTurnMs;
            if (!turn_over) {
                continue;  // someone is mid-sentence: never interrupt
            }
            in_turn = false;
            preroll.clear();
            if (voiced_chunks * ChunkMs >= MinTurnSpeechMs) {
                codec->EnableInput(false);
                GetHAL().showRgbColor(0, 0, 0);
                post_ui([]() { GetStackChan().avatar().setEmotion(avatar::Emotion::Doubt); });

                uint32_t before    = GetHAL().millis();
                std::string action = converse_turn(session_id, turn_audio);
                speaking_ms += GetHAL().millis() - before;
                turn_audio.clear();
                if (action == "wrap_up") {
                    wrapped_up = true;
                    break;
                }

                post_ui([]() { GetStackChan().avatar().setEmotion(avatar::Emotion::Neutral); });
                codec->EnableInput(true);
                GetHAL().showRgbColor(0x60, 0x48, 0x08);
                last_voice = last_check = GetHAL().millis();
                next_check = last_voice + 10000;
                continue;
            }
        }

        if (now < next_check && !long_silence) {
            continue;
        }

        // 4. Tell the server how the conversation is going; it decides whether Sameer steps in.
        last_check   = now;
        cJSON* body  = cJSON_CreateObject();
        cJSON_AddStringToObject(body, "session_id", session_id.c_str());
        cJSON_AddNumberToObject(body, "elapsed_seconds", elapsed_secs);
        cJSON_AddNumberToObject(body, "silence_seconds", silence_secs);
        cJSON_AddBoolToObject(body, "talking", talking);
        if (post_json("/session_followup", to_json(body), response, RequestTimeoutMs) != 200) {
            next_check = now + 15000;
            continue;
        }

        cJSON* reply       = cJSON_Parse(response.c_str());
        std::string action = json_string(reply, "action");
        std::string text   = json_string(reply, "text");
        cJSON* again       = cJSON_GetObjectItemCaseSensitive(reply, "check_again_seconds");
        next_check         = now + (cJSON_IsNumber(again) ? again->valueint : 10) * 1000;
        cJSON_Delete(reply);

        if (action == "follow_up" || action == "wrap_up") {
            codec->EnableInput(false);
            GetHAL().showRgbColor(0, 0, 0);
            uint32_t before = GetHAL().millis();
            speak(text);
            speaking_ms += GetHAL().millis() - before;
            last_voice = GetHAL().millis();  // give the family a fresh moment to answer
            if (action == "wrap_up") {
                wrapped_up = true;
            } else {
                codec->EnableInput(true);
                GetHAL().showRgbColor(0x60, 0x48, 0x08);
            }
        }
    }
    codec->EnableInput(false);
    GetHAL().showRgbColor(0, 0, 0);

    uint32_t listened_ms = GetHAL().millis() - start - speaking_ms;

    // 5. Participation numbers for the dashboard (no speaker data: the device can't tell voices apart).
    {
        cJSON* body = cJSON_CreateObject();
        cJSON_AddStringToObject(body, "session_id", session_id.c_str());
        cJSON_AddNumberToObject(body, "duration_seconds", listened_ms / 1000);
        cJSON_AddNumberToObject(body, "silence_seconds", std::min(silent_ms, listened_ms) / 1000);
        cJSON_AddItemToObject(body, "speakers", cJSON_CreateArray());
        post_json("/evaluate_session", to_json(body), response, RequestTimeoutMs);
    }

    say_thanks();
}

/* -------------------------------------------------------------------------- */
/*                         Live conversation (Gemini Live)                     */
/* -------------------------------------------------------------------------- */

namespace {

constexpr int LiveInputRate      = 16000;  // what Gemini Live expects from the microphone
constexpr int LiveOutputRate     = 24000;  // what Gemini Live speaks
constexpr int LiveSetupTimeoutMs = 15000;
constexpr int EchoTailMs         = 350;    // keep the mic muted this long after Hiwar stops talking
constexpr int SilenceNudgeMs     = 25000;  // after this much quiet, Hiwar offers a new question
constexpr int MaxNudges          = 3;
constexpr int GoodbyeWaitMs      = 12000;
constexpr int HeartbeatMs        = 8000;
constexpr int MaxLiveMs          = 30 * 60 * 1000;
constexpr int MaxReconnects      = 4;      // Gemini closes a connection every ~10 minutes

constexpr const char* OpenNudge    = "[تنبيه] ابدأ الآن.";
constexpr const char* SilenceNudge = "[تنبيه] طال الصمت قليلًا. اقترح شيئًا خفيفًا جديدًا، أو ادعُ من لم يتكلم للمشاركة.";
constexpr const char* GoodbyeNudge = "[تنبيه] انتهى وقت الجلسة. ودّع بجملة قصيرة واشكر.";

std::string base64_encode(const void* data, size_t len)
{
    size_t out_len = 0;
    mbedtls_base64_encode(nullptr, 0, &out_len, static_cast<const unsigned char*>(data), len);
    std::string out(out_len, '\0');
    if (mbedtls_base64_encode(reinterpret_cast<unsigned char*>(out.data()), out.size(), &out_len,
                              static_cast<const unsigned char*>(data), len) != 0) {
        return "";
    }
    out.resize(out_len);
    return out;
}

std::vector<int16_t> base64_decode_pcm(const char* text)
{
    size_t text_len = std::strlen(text);
    std::vector<int16_t> pcm((text_len / 4) * 3 / 2 + 2);
    size_t out_len = 0;
    if (mbedtls_base64_decode(reinterpret_cast<unsigned char*>(pcm.data()), pcm.size() * 2, &out_len,
                              reinterpret_cast<const unsigned char*>(text), text_len) != 0) {
        return {};
    }
    pcm.resize(out_len / 2);
    return pcm;
}

// Linear resampling; good enough for speech between 16 and 24 kHz.
std::vector<int16_t> resample(const int16_t* in, size_t frames, int stride, int from_rate, int to_rate)
{
    if (from_rate == to_rate && stride == 1) {
        return std::vector<int16_t>(in, in + frames);
    }
    size_t out_frames = frames * to_rate / from_rate;
    std::vector<int16_t> out(out_frames);
    for (size_t i = 0; i < out_frames; ++i) {
        float pos  = float(i) * from_rate / to_rate;
        size_t a   = std::min(size_t(pos), frames - 1);
        size_t b   = std::min(a + 1, frames - 1);
        float frac = pos - a;
        out[i]     = int16_t(in[a * stride] * (1 - frac) + in[b * stride] * frac);
    }
    return out;
}

std::string realtime_text(const char* text)
{
    cJSON* root  = cJSON_CreateObject();
    cJSON* input = cJSON_AddObjectToObject(root, "realtimeInput");
    cJSON_AddStringToObject(input, "text", text);
    return to_json(root);
}

// State shared between the WebSocket callbacks, the playback task and the session loop.
struct LiveCall {
    std::unique_ptr<WebSocket> ws;
    std::mutex mutex;  // guards the fields below marked (m)
    std::deque<std::vector<int16_t>> playback;  // (m) Hiwar's voice, waiting to be played
    std::string resume_handle;                  // (m) lets a dropped connection continue the conversation
    std::string transcript;                     // (m) Hiwar's words in the current turn
    std::string opening_question;               // (m) Hiwar's first turn, for the dashboard
    std::string member;                         // (m) personal mode: who Hiwar is talking with
    std::vector<std::string> topics;            // (m) personal mode: general subjects, no details

    std::atomic<bool> setup_done{false};
    std::atomic<bool> connected{false};
    std::atomic<bool> go_away{false};
    std::atomic<bool> model_ended{false};     // Gemini called end_conversation
    std::atomic<bool> model_talking{false};   // between the first audio of a turn and turnComplete
    std::atomic<bool> playing{false};
    std::atomic<bool> stop_playback{false};
    std::atomic<bool> playback_done{false};
    std::atomic<int> replies{0};
    std::atomic<uint32_t> last_play_end{0};
    std::atomic<uint32_t> last_turn_complete{0};

    bool queue_empty()
    {
        std::lock_guard<std::mutex> lock(mutex);
        return playback.empty();
    }

    void clear_playback()
    {
        std::lock_guard<std::mutex> lock(mutex);
        playback.clear();
    }

    bool send(const std::string& message)
    {
        return ws && connected && ws->Send(message);
    }

    // One server message: audio, transcription, turn markers, tool calls, resumption handles.
    void on_message(const char* data, size_t len)
    {
        cJSON* root = cJSON_ParseWithLength(data, len);
        if (root == nullptr) {
            mclog::tagWarn(_tag, "live: unparsable message ({} bytes)", len);
            return;
        }
        if (cJSON_GetObjectItemCaseSensitive(root, "setupComplete")) {
            setup_done = true;
        }

        cJSON* content = cJSON_GetObjectItemCaseSensitive(root, "serverContent");
        if (content) {
            if (cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(content, "interrupted"))) {
                clear_playback();  // someone spoke over Hiwar: stop talking right away
                model_talking = false;
            }
            cJSON* turn  = cJSON_GetObjectItemCaseSensitive(content, "modelTurn");
            cJSON* parts = turn ? cJSON_GetObjectItemCaseSensitive(turn, "parts") : nullptr;
            cJSON* part  = nullptr;
            cJSON_ArrayForEach(part, parts)
            {
                cJSON* inline_data = cJSON_GetObjectItemCaseSensitive(part, "inlineData");
                cJSON* b64         = inline_data ? cJSON_GetObjectItemCaseSensitive(inline_data, "data") : nullptr;
                if (cJSON_IsString(b64) && b64->valuestring) {
                    auto pcm = base64_decode_pcm(b64->valuestring);
                    if (!pcm.empty()) {
                        model_talking = true;
                        std::lock_guard<std::mutex> lock(mutex);
                        playback.push_back(std::move(pcm));
                    }
                }
            }
            cJSON* said = cJSON_GetObjectItemCaseSensitive(content, "outputTranscription");
            cJSON* text = said ? cJSON_GetObjectItemCaseSensitive(said, "text") : nullptr;
            if (cJSON_IsString(text) && text->valuestring) {
                std::lock_guard<std::mutex> lock(mutex);
                if (transcript.size() < 600) {
                    transcript += text->valuestring;
                }
            }
            if (cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(content, "turnComplete"))) {
                model_talking      = false;
                last_turn_complete = GetHAL().millis();
                ++replies;
                std::lock_guard<std::mutex> lock(mutex);
                if (opening_question.empty() && !transcript.empty()) {
                    opening_question = transcript;
                }
                transcript.clear();
            }
        }

        cJSON* tool_call = cJSON_GetObjectItemCaseSensitive(root, "toolCall");
        cJSON* calls     = tool_call ? cJSON_GetObjectItemCaseSensitive(tool_call, "functionCalls") : nullptr;
        cJSON* call      = nullptr;
        cJSON_ArrayForEach(call, calls)
        {
            std::string name = json_string(call, "name");
            std::string id   = json_string(call, "id");
            cJSON* args = cJSON_GetObjectItemCaseSensitive(call, "args");
            if (name == "end_conversation") {
                model_ended = true;
            } else if (name == "set_member") {
                std::string role = json_string(args, "role");
                std::lock_guard<std::mutex> lock(mutex);
                if (!role.empty()) {
                    member = role;
                }
            } else if (name == "note_topic") {
                std::string topic = json_string(args, "topic");
                std::lock_guard<std::mutex> lock(mutex);
                if (!topic.empty() && std::find(topics.begin(), topics.end(), topic) == topics.end() &&
                    topics.size() < 12) {
                    topics.push_back(topic);
                }
            }
            cJSON* reply     = cJSON_CreateObject();
            cJSON* responses = cJSON_AddArrayToObject(cJSON_AddObjectToObject(reply, "toolResponse"), "functionResponses");
            cJSON* response  = cJSON_CreateObject();
            cJSON_AddStringToObject(response, "id", id.c_str());
            cJSON_AddStringToObject(response, "name", name.c_str());
            cJSON_AddStringToObject(cJSON_AddObjectToObject(response, "response"), "result", "ok");
            cJSON_AddItemToArray(responses, response);
            send(to_json(reply));
        }

        cJSON* resumption = cJSON_GetObjectItemCaseSensitive(root, "sessionResumptionUpdate");
        if (resumption && cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(resumption, "resumable"))) {
            std::string handle = json_string(resumption, "newHandle");
            if (!handle.empty()) {
                std::lock_guard<std::mutex> lock(mutex);
                resume_handle = handle;
            }
        }
        if (cJSON_GetObjectItemCaseSensitive(root, "goAway")) {
            go_away = true;  // Gemini will close this connection soon: reconnect and resume
        }
        cJSON_Delete(root);
    }
};

struct PlaybackArgs {
    LiveCall* call;
    std::function<void(uint32_t)> on_speaking;
};

// Plays Hiwar's voice as soon as each piece arrives, while the session loop keeps listening.
void playback_task(void* arg)
{
    auto* args     = static_cast<PlaybackArgs*>(arg);
    LiveCall& call = *args->call;
    auto codec     = Board::GetInstance().GetAudioCodec();
    const int rate = codec->output_sample_rate();
    uint32_t animated_until = 0;

    while (!call.stop_playback) {
        std::vector<int16_t> chunk;
        {
            std::lock_guard<std::mutex> lock(call.mutex);
            if (!call.playback.empty()) {
                chunk = std::move(call.playback.front());
                call.playback.pop_front();
            }
        }
        if (chunk.empty()) {
            if (call.playing) {
                call.playing       = false;
                call.last_play_end = GetHAL().millis();
            }
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        call.playing = true;
        if (rate != LiveOutputRate) {
            chunk = resample(chunk.data(), chunk.size(), 1, LiveOutputRate, rate);
        }
        uint32_t now = GetHAL().millis();
        if (now + 300 >= animated_until) {
            uint32_t ms = 1200;
            args->on_speaking(ms);
            animated_until = now + ms;
        }
        codec->OutputData(chunk);  // blocks for about the chunk's duration
    }
    call.playing       = false;
    call.playback_done = true;
    vTaskDelete(nullptr);
}

struct Heartbeat {
    std::string body;
    std::atomic<bool>* busy;
};

void heartbeat_task(void* arg)
{
    auto* beat = static_cast<Heartbeat*>(arg);
    std::string response;
    post_json("/live/heartbeat", beat->body, response, 15000);
    *beat->busy = false;
    delete beat;
    vTaskDelete(nullptr);
}

}  // namespace

bool AppSameer::run_live_session(bool personal)
{
    // 1. The server picks the topic and hands out a short-lived Gemini token for this conversation.
    std::string response;
    if (post_json("/live/start", personal ? R"({"mode":"personal"})" : R"({"mode":"family"})", response,
                  IntroTimeoutMs) != 200) {
        return false;
    }
    cJSON* started = cJSON_Parse(response.c_str());
    if (started == nullptr) {
        return false;
    }
    std::string session_id = json_string(started, "session_id");
    std::string ws_url     = json_string(started, "ws_url");
    std::string token      = json_string(started, "token");
    cJSON* setup           = cJSON_DetachItemFromObjectCaseSensitive(started, "setup");
    cJSON_Delete(started);
    if (session_id.empty() || ws_url.empty() || setup == nullptr) {
        cJSON_Delete(setup);
        return false;
    }
    mclog::tagInfo(_tag, "live session {}", session_id);

    LiveCall call;
    auto connect = [&]() -> bool {
        call.setup_done = false;
        call.connected  = false;
        call.go_away    = false;
        if (call.ws) {
            call.ws->OnData(nullptr);
            call.ws->OnDisconnected(nullptr);
            call.ws->Close();
        }
        call.ws = Board::GetInstance().GetNetwork()->CreateWebSocket(1);
        if (!call.ws) {
            return false;
        }
        call.ws->SetHeader("Authorization", ("Token " + token).c_str());
        call.ws->SetReceiveBufferSize(16 * 1024);
        call.ws->OnData([&call](const char* data, size_t len, bool) { call.on_message(data, len); });
        call.ws->OnDisconnected([&call]() {
            mclog::tagWarn(_tag, "live: disconnected");
            call.connected = false;
        });
        if (!call.ws->Connect(ws_url.c_str())) {
            mclog::tagError(_tag, "live: could not connect");
            return false;
        }
        call.connected = true;

        // Resume where the conversation left off, if Gemini gave us a handle.
        cJSON* message = cJSON_Duplicate(setup, true);
        cJSON* body    = cJSON_GetObjectItemCaseSensitive(message, "setup");
        {
            std::lock_guard<std::mutex> lock(call.mutex);
            if (body && !call.resume_handle.empty()) {
                cJSON_DeleteItemFromObjectCaseSensitive(body, "sessionResumption");
                cJSON_AddStringToObject(cJSON_AddObjectToObject(body, "sessionResumption"), "handle",
                                        call.resume_handle.c_str());
            }
        }
        call.send(to_json(message));

        uint32_t asked = GetHAL().millis();
        while (!call.setup_done && call.connected && GetHAL().millis() - asked < LiveSetupTimeoutMs) {
            vTaskDelay(pdMS_TO_TICKS(20));
        }
        if (!call.setup_done) {
            mclog::tagError(_tag, "live: setup was not accepted");
            return false;
        }
        return true;
    };

    if (!connect()) {
        if (call.ws) {
            call.ws->Close();
        }
        cJSON_Delete(setup);
        return false;
    }

    // 2. Speaker and microphone run at the same time; the playback task speaks as audio arrives.
    auto codec = Board::GetInstance().GetAudioCodec();
    codec->EnableOutput(true);
    codec->EnableInput(true);
    auto* playback_args = new PlaybackArgs{&call, [this](uint32_t ms) {
                                               post_ui([ms]() {
                                                   GetStackChan().addModifier(std::make_unique<SpeakingModifier>(ms));
                                               });
                                           }};
    xTaskCreatePinnedToCore(playback_task, "hiwar_play", 8 * 1024, playback_args, 5, nullptr, 0);

    _state = State::Live;
    post_ui([]() {
        GetStackChan().avatar().setEmotion(avatar::Emotion::Happy);
        GetStackChan().motion().moveWithSpeed(0, PitchFacingFamily, 400);
    });
    call.send(realtime_text(OpenNudge));  // Hiwar greets the family and asks the first question

    const int in_rate      = codec->input_sample_rate();
    const int channels     = std::max(codec->input_channels(), 1);
    const int chunk_frames = in_rate * ChunkMs / 1000;
    std::vector<int16_t> buffer(chunk_frames * channels);

    uint32_t start          = GetHAL().millis();
    uint32_t last_voice     = start;
    uint32_t last_beat      = start;
    uint32_t talk_ms        = 0;
    uint32_t heard_ms       = 0;  // time the mic was open (Hiwar not talking)
    float noise_floor       = 0;
    int calibration_chunks  = 0;
    int turns               = 0;  // the family's spoken turns, counted on the device
    int turn_voiced_ms      = 0;
    bool in_turn            = false;
    int nudges              = 0;
    int reconnects          = 0;
    bool was_speaking       = false;
    bool saying_goodbye     = false;
    uint32_t goodbye_asked  = 0;
    bool question_sent      = false;
    std::atomic<bool> beat_busy{false};

    auto send_heartbeat = [&](bool wait) {
        cJSON* body = cJSON_CreateObject();
        cJSON_AddStringToObject(body, "session_id", session_id.c_str());
        cJSON_AddNumberToObject(body, "turns", turns);
        cJSON_AddNumberToObject(body, "replies", call.replies.load());
        {
            std::lock_guard<std::mutex> lock(call.mutex);
            if (!personal && !question_sent && !call.opening_question.empty()) {
                cJSON_AddStringToObject(body, "question", call.opening_question.c_str());
                question_sent = true;
            }
            if (personal) {
                if (!call.member.empty()) {
                    cJSON_AddStringToObject(body, "member", call.member.c_str());
                }
                cJSON* list = cJSON_AddArrayToObject(body, "topics");
                for (auto& topic : call.topics) {
                    cJSON_AddItemToArray(list, cJSON_CreateString(topic.c_str()));
                }
            }
        }
        if (wait) {
            std::string ignored;
            post_json("/live/heartbeat", to_json(body), ignored, 15000);
            return;
        }
        if (beat_busy.exchange(true)) {
            cJSON_Delete(body);
            return;
        }
        // HTTP blocks for a moment, so it runs on its own task: the microphone never pauses.
        auto* beat = new Heartbeat{to_json(body), &beat_busy};
        if (xTaskCreate(heartbeat_task, "hiwar_beat", 6 * 1024, beat, 3, nullptr) != pdPASS) {
            delete beat;
            beat_busy = false;
        }
    };

    while (true) {
        uint32_t now = GetHAL().millis();

        // 3. Keep the connection alive: Gemini asks us to reconnect every ~10 minutes.
        if (call.go_away || !call.connected) {
            if (reconnects >= MaxReconnects || saying_goodbye || call.model_ended) {
                break;
            }
            ++reconnects;
            mclog::tagInfo(_tag, "live: reconnecting ({})", reconnects);
            if (!connect()) {
                break;
            }
        }

        // 4. How the conversation ends: Hiwar said goodbye, the family tapped, or time ran out.
        if (call.model_ended && !call.model_talking && !call.playing && call.queue_empty()) {
            break;
        }
        if (_end_requested.exchange(false) && !saying_goodbye) {
            saying_goodbye = true;
            goodbye_asked  = now;
            call.send(realtime_text(GoodbyeNudge));
        }
        if (!saying_goodbye && now - start >= MaxLiveMs) {
            saying_goodbye = true;
            goodbye_asked  = now;
            call.send(realtime_text(GoodbyeNudge));
        }
        if (saying_goodbye) {
            bool said_it = call.last_turn_complete > goodbye_asked && !call.playing && call.queue_empty();
            if (said_it || now - goodbye_asked >= GoodbyeWaitMs) {
                break;
            }
        }
        if (_interrupt_requested.exchange(false)) {
            call.clear_playback();  // tap while Hiwar talks: stop and listen
        }

        if (!codec->InputData(buffer)) {
            vTaskDelay(pdMS_TO_TICKS(ChunkMs));
            continue;
        }

        // 5. Half duplex: while Hiwar talks (and a moment after), the mic isn't sent, so it never
        //    hears itself through its own speaker. A tap on the screen cuts Hiwar short.
        bool speaking = call.playing || !call.queue_empty() || now - call.last_play_end < EchoTailMs;
        if (speaking != was_speaking) {
            was_speaking    = speaking;
            _robot_speaking = speaking;
            if (speaking) {
                GetHAL().showRgbColor(0, 0, 0);
            } else {
                GetHAL().showRgbColor(0x60, 0x48, 0x08);  // soft gold: Hiwar is listening
            }
        }
        if (speaking) {
            last_voice = now;  // Hiwar talking isn't silence
            in_turn    = false;
            continue;
        }
        heard_ms += ChunkMs;

        double sum = 0;
        for (int i = 0; i < chunk_frames; ++i) {
            double s = buffer[i * channels];
            sum += s * s;
        }
        float rms = std::sqrt(sum / chunk_frames);
        if (calibration_chunks < 10) {
            noise_floor = calibration_chunks == 0 ? rms : std::min(noise_floor, rms);
            ++calibration_chunks;
        }
        bool voiced = rms > std::max(noise_floor * VoiceOverNoise, (float)MinVoiceRms);
        if (!voiced) {
            noise_floor = noise_floor * 0.98f + rms * 0.02f;
        }

        // Count the family's turns for the dashboard (numbers only).
        if (voiced) {
            last_voice = now;
            talk_ms += ChunkMs;
            turn_voiced_ms += ChunkMs;
            in_turn = true;
        } else if (in_turn && now - last_voice >= TurnEndSilenceMs) {
            if (turn_voiced_ms >= MinTurnSpeechMs) {
                ++turns;
                nudges = 0;
            }
            in_turn        = false;
            turn_voiced_ms = 0;
        }

        auto pcm = resample(buffer.data(), chunk_frames, channels, in_rate, LiveInputRate);
        cJSON* message = cJSON_CreateObject();
        cJSON* audio   = cJSON_AddObjectToObject(cJSON_AddObjectToObject(message, "realtimeInput"), "audio");
        cJSON_AddStringToObject(audio, "data", base64_encode(pcm.data(), pcm.size() * 2).c_str());
        cJSON_AddStringToObject(audio, "mimeType", "audio/pcm;rate=16000");
        call.send(to_json(message));

        // 6. A long quiet moment: Hiwar offers something new, and after a few tries says goodbye.
        if (!saying_goodbye && !call.model_talking && now - last_voice >= SilenceNudgeMs) {
            last_voice = now;
            if (nudges >= MaxNudges) {
                saying_goodbye = true;
                goodbye_asked  = now;
                call.send(realtime_text(GoodbyeNudge));
            } else {
                ++nudges;
                call.send(realtime_text(SilenceNudge));
            }
        }

        if (now - last_beat >= HeartbeatMs) {
            last_beat = now;
            send_heartbeat(false);
        }
    }

    // 7. Hang up, then report the numbers so the dashboard fills in.
    _state          = State::Busy;
    _robot_speaking = false;
    call.stop_playback = true;
    while (!call.playback_done) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    delete playback_args;
    if (call.ws) {
        call.ws->OnData(nullptr);
        call.ws->OnDisconnected(nullptr);
        call.ws->Close();
    }
    call.connected = false;
    codec->EnableInput(false);
    codec->EnableOutput(false);
    GetHAL().showRgbColor(0, 0, 0);
    cJSON_Delete(setup);

    while (beat_busy) {
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    send_heartbeat(true);
    {
        cJSON* body = cJSON_CreateObject();
        cJSON_AddStringToObject(body, "session_id", session_id.c_str());
        cJSON_AddNumberToObject(body, "duration_seconds", (GetHAL().millis() - start) / 1000);
        cJSON_AddNumberToObject(body, "silence_seconds", (heard_ms > talk_ms ? heard_ms - talk_ms : 0) / 1000);
        cJSON_AddItemToObject(body, "speakers", cJSON_CreateArray());
        post_json("/evaluate_session", to_json(body), response, RequestTimeoutMs);
    }
    mclog::tagInfo(_tag, "live session done: {} turns, {} replies", turns, call.replies.load());
    say_thanks();
    return true;
}
