/*
 * Hiwar's avatar for StackChan: the 24-expression face (hiwar_face.h) behind the standard Avatar
 * interface, so the stock modifiers keep working: BlinkModifier closes the eyes, SpeakingModifier
 * opens the mouth, BreathModifier and IdleMotionModifier move the face a little.
 */
#pragma once
#include "hiwar_face.h"
#include <stackchan/stackchan.h>
#include <memory>

namespace hiwar {

class HiwarAvatar;

// One of the avatar's eyes or its mouth. It only keeps the values the modifiers set and asks the
// avatar to redraw; HiwarAvatar draws the whole face at once.
class FaceFeature : public stackchan::avatar::Feature {
public:
    FaceFeature(HiwarAvatar& owner, int weight);
    void setWeight(int weight) override;
    void setPosition(const uitk::Vector2i& position) override;

private:
    HiwarAvatar& _owner;
};

class HiwarAvatar : public stackchan::avatar::Avatar {
public:
    ~HiwarAvatar();

    void init(lv_obj_t* parent);
    lv_obj_t* panel() const { return _panel; }

    void setExpression(Expression expression);
    Expression expression() const { return _state.expression; }

    // The six StackChan emotions map onto Hiwar's expressions.
    void setEmotion(const stackchan::avatar::Emotion& emotion) override;
    void update() override;

    void markDirty() { _dirty = true; }

private:
    lv_obj_t* _panel = nullptr;
    void* _buffer    = nullptr;
    std::unique_ptr<FaceCanvas> _face;
    FaceState _state;
    bool _dirty            = true;
    uint32_t _last_render  = 0;
};

}  // namespace hiwar
