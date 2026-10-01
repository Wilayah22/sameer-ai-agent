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
#include <audio/audio_codec.h>
#include <cJSON.h>
#include <esp_heap_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <sdkconfig.h>
#include <algorithm>
#include <cmath>
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
constexpr int RatingWaitMs      = 90000;
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

// Same words as the server's wrap-up, used when the family ends the session from the screen.
constexpr const char* WrapUpText = "كانت جلسة جميلة! كيف تقيّمونها؟ واحد: عادية، اثنان: جيدة، ثلاثة: رائعة.";

std::unique_ptr<Container> _rating_panel;
std::vector<std::unique_ptr<Button>> _rating_buttons;

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
            on_screen_tap();
        }
    });

    view::create_home_indicator([this]() { close(); }, 0xF1E2A8, ThemeDark);
    view::create_status_bar(0xF1E2A8, ThemeDark);

    // Network and audio block for seconds at a time, so the session runs on its own task.
    // It also wakes the server right away, while the family is still getting ready.
    xTaskCreatePinnedToCore(session_task, "sameer", 12 * 1024, this, 4, nullptr, 1);
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
        show_rating_buttons(false);
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
            _start_requested = true;
            break;
        case State::Listening:
            _end_requested = true;  // the family can end the session early
            break;
        default:
            break;
    }
}

// Must be called with the LVGL lock held.
void AppSameer::show_rating_buttons(bool show)
{
    _rating_buttons.clear();
    _rating_panel.reset();
    if (!show) {
        return;
    }

    _rating_panel = std::make_unique<Container>(lv_screen_active());
    _rating_panel->setSize(300, 84);
    _rating_panel->align(LV_ALIGN_BOTTOM_MID, 0, -8);
    _rating_panel->setBgOpa(0);
    _rating_panel->setBorderWidth(0);
    _rating_panel->setPaddingAll(0);

    // 1 = ordinary, 2 = good, 3 = great: each button a little more gold.
    const uint32_t colors[] = {0xF1E2A8, 0xE6C766, 0xD9B84A};
    for (int value = 1; value <= 3; ++value) {
        auto button = std::make_unique<Button>(_rating_panel->get());
        button->setSize(84, 72);
        button->align(LV_ALIGN_LEFT_MID, (value - 1) * 108, 0);
        button->setBgColor(lv_color_hex(colors[value - 1]));
        button->setRadius(18);
        button->label().setText(std::to_string(value));
        button->label().setTextFont(&lv_font_montserrat_24);
        button->label().setTextColor(lv_color_hex(ThemeDark));
        button->onClick().connect([this, value]() { _rating = value; });
        _rating_buttons.push_back(std::move(button));
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
            app->_end_requested = false;
            last_ping = xTaskGetTickCount();
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

    // 6. Rating 1-3 on the screen.
    _rating = 0;
    _state  = State::Rating;
    post_ui([this]() {
        GetStackChan().avatar().setEmotion(avatar::Emotion::Happy);
        show_rating_buttons(true);
    });

    uint32_t asked = GetHAL().millis();
    while (_rating == 0 && GetHAL().millis() - asked < RatingWaitMs) {
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    post_ui([this]() { show_rating_buttons(false); });

    if (_rating > 0) {
        cJSON* body = cJSON_CreateObject();
        cJSON_AddStringToObject(body, "session_id", session_id.c_str());
        cJSON_AddNumberToObject(body, "rating", _rating.load());
        post_json("/save_rating", to_json(body), response, RequestTimeoutMs);

        // A happy little nod as thanks.
        post_ui([]() {
            GetStackChan().addModifier(std::make_unique<TimedEmotionModifier>(avatar::Emotion::Happy, 3000));
            GetStackChan().motion().moveWithSpeed(0, PitchNodTop, 500);
        });
        vTaskDelay(pdMS_TO_TICKS(500));
        post_ui([]() { GetStackChan().motion().moveWithSpeed(0, 30, 500); });
        vTaskDelay(pdMS_TO_TICKS(500));
        post_ui([]() { GetStackChan().motion().moveWithSpeed(0, PitchFacingFamily, 400); });
    }
    post_ui([]() { GetStackChan().avatar().setEmotion(avatar::Emotion::Neutral); });
}
