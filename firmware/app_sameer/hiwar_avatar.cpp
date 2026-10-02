/*
 * Hiwar's avatar. See hiwar_avatar.h.
 */
#include "hiwar_avatar.h"
#include <hal/hal.h>
#include <esp_heap_caps.h>
#include <algorithm>

namespace hiwar {

namespace {

constexpr uint32_t FrameMs   = 50;  // at most 20 redraws a second
constexpr int MaxOffsetPx    = 8;   // how far breathing / idle motion moves the face

bool animated(Expression e)
{
    return e == Expression::Crying || e == Expression::Scared || e == Expression::LookingAround;
}

}  // namespace

FaceFeature::FaceFeature(HiwarAvatar& owner, int weight) : _owner(owner)
{
    Feature::setWeight(weight);
}

void FaceFeature::setWeight(int weight)
{
    Feature::setWeight(weight);
    _owner.markDirty();
}

void FaceFeature::setPosition(const uitk::Vector2i& position)
{
    Element::setPosition(position);
    _owner.markDirty();
}

HiwarAvatar::~HiwarAvatar()
{
    _face.reset();  // deletes the canvas before its pixels
    if (_panel) {
        lv_obj_delete(_panel);
    }
    heap_caps_free(_buffer);
}

void HiwarAvatar::init(lv_obj_t* parent)
{
    _panel = lv_obj_create(parent);
    lv_obj_set_size(_panel, FaceCanvas::Width, FaceCanvas::Height);
    lv_obj_align(_panel, LV_ALIGN_CENTER, 0, 0);
    lv_obj_set_style_radius(_panel, 0, 0);
    lv_obj_set_style_border_width(_panel, 0, 0);
    lv_obj_set_style_pad_all(_panel, 0, 0);
    lv_obj_set_style_bg_color(_panel, lv_color_black(), 0);
    lv_obj_remove_flag(_panel, LV_OBJ_FLAG_SCROLLABLE);

    // 150 KB of pixels: PSRAM.
    _buffer = heap_caps_malloc(FaceCanvas::Width * FaceCanvas::Height * 2, MALLOC_CAP_SPIRAM);
    _face   = std::make_unique<FaceCanvas>();
    _face->create(_panel, _buffer);

    _key_elements.leftEye  = std::make_unique<FaceFeature>(*this, 100);
    _key_elements.rightEye = std::make_unique<FaceFeature>(*this, 100);
    _key_elements.mouth    = std::make_unique<FaceFeature>(*this, 0);
}

void HiwarAvatar::setExpression(Expression expression)
{
    if (_state.expression != expression) {
        _state.expression = expression;
        _dirty            = true;
    }
}

void HiwarAvatar::setEmotion(const stackchan::avatar::Emotion& emotion)
{
    using stackchan::avatar::Emotion;
    _emotion = emotion;  // keep getEmotion() meaningful for TimedEmotionModifier
    switch (emotion) {
        case Emotion::Happy: setExpression(Expression::Happy); break;
        case Emotion::Angry: setExpression(Expression::Angry); break;
        case Emotion::Sad: setExpression(Expression::Sad); break;
        case Emotion::Doubt: setExpression(Expression::Thinking); break;
        case Emotion::Sleepy: setExpression(Expression::Sleepy); break;
        default: setExpression(Expression::Neutral); break;
    }
}

void HiwarAvatar::update()
{
    Avatar::update();
    if (!_face) {
        return;
    }

    uint32_t now = GetHAL().millis();
    if (!(_dirty || animated(_state.expression)) || now - _last_render < FrameMs) {
        return;
    }

    auto& keys        = getKeyElements();
    _state.eye_open   = std::min(keys.leftEye->getWeight(), keys.rightEye->getWeight());
    _state.mouth_open = keys.mouth->getWeight();
    auto pos          = keys.leftEye->getPosition();
    _state.offset_x   = pos.x * MaxOffsetPx / 100;
    _state.offset_y   = pos.y * MaxOffsetPx / 100;
    _state.time_ms    = now;

    _face->render(_state);
    _dirty       = false;
    _last_render = now;
}

}  // namespace hiwar
