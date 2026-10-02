/*
 * Hiwar's face: 24 expressions drawn on the robot's screen with LVGL (no image assets).
 *
 * The face is a full-screen canvas. Eyes are glowing cream ovals with dark pupils; expressions add
 * closed-eye arcs, hearts, stars, tears, blush, brows and small gold symbols (Z, ?, !, sparkles).
 * Blinking, talking and the gentle breathing motion still come from the StackChan modifiers.
 */
#pragma once
#include <lvgl.h>
#include <cstdint>
#include <string>

namespace hiwar {

enum class Expression : uint8_t {
    Happy,
    Excited,
    Love,
    Sad,
    Crying,
    Angry,
    Shy,
    Surprised,
    Sleepy,
    Thinking,
    Confident,
    Neutral,
    Confused,
    Annoyed,
    Scared,
    Laughing,
    Bored,
    Pleading,
    Wink,
    LookingAround,
    Curious,
    Disappointed,
    Proud,
    Energetic,
    Count,
};

// Lowercase English name ("happy", "looking_around"), as used by the Gemini tool.
const char* expression_name(Expression expression);
bool expression_from_name(const std::string& name, Expression& out);

struct FaceState {
    Expression expression = Expression::Neutral;
    int eye_open          = 100;  // 0 (closed, blinking) .. 100
    int mouth_open        = 0;    // 0 .. 100, while talking
    int offset_x          = 0;    // pixels, from the breathing / idle motion
    int offset_y          = 0;
    uint32_t time_ms      = 0;    // drives small animations (tears, looking around)
};

class FaceCanvas {
public:
    static constexpr int Width  = 320;
    static constexpr int Height = 240;

    ~FaceCanvas();
    // Creates a full-screen canvas on `parent`. `buffer` must hold Width * Height RGB565 pixels.
    void create(lv_obj_t* parent, void* buffer);
    lv_obj_t* object() const { return _canvas; }
    void render(const FaceState& state);

private:
    lv_obj_t* _canvas = nullptr;
};

}  // namespace hiwar
