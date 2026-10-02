/*
 * Hiwar's face, drawn with LVGL's software renderer. See hiwar_face.h.
 */
#include "hiwar_face.h"
#include <algorithm>
#include <cmath>
#include <cstring>

namespace hiwar {

namespace {

const char* const Names[] = {
    "happy",    "excited",  "love",     "sad",    "crying",   "angry",          "shy",     "surprised",
    "sleepy",   "thinking", "confident", "neutral", "confused", "annoyed",       "scared",  "laughing",
    "bored",    "pleading", "wink",     "looking_around", "curious", "disappointed", "proud", "energetic",
};
static_assert(sizeof(Names) / sizeof(Names[0]) == static_cast<int>(Expression::Count), "one name per expression");

const lv_color_t Cream = lv_color_hex(0xF4F1EA);
const lv_color_t Ink   = lv_color_hex(0x0B0F19);  // pupils, eyelids: the screen's black
const lv_color_t Gold  = lv_color_hex(0xE0B83A);
const lv_color_t Pink  = lv_color_hex(0xFF5C8A);
const lv_color_t Blush = lv_color_hex(0xFF7A9A);
const lv_color_t Tear  = lv_color_hex(0x5CC8FF);

constexpr int EyeY      = 102;
constexpr int EyeDx     = 62;  // from the centre
constexpr int EyeW      = 46;
constexpr int EyeH      = 56;
constexpr int MouthY    = 170;
constexpr int CenterX   = FaceCanvas::Width / 2;

// Small drawing helpers over an LVGL layer, all offset by the breathing motion.
// Coordinates below are drawn around (CenterX, ScaleY) and scaled by Scale, so the whole face can
// grow or shrink in one place.
constexpr float Scale = 1.25f;
constexpr int ScaleY  = 125;

struct Painter {
    lv_layer_t* layer;
    int ox, oy;

    int X(int x) const { return ox + CenterX + int((x - CenterX) * Scale); }
    int Y(int y) const { return oy + ScaleY + int((y - ScaleY) * Scale); }
    static int S(int v) { return int(v * Scale); }

    void rect(int x, int y, int w, int h, lv_color_t color, int radius = 0, lv_opa_t opa = LV_OPA_COVER,
              int glow = 0)
    {
        lv_draw_rect_dsc_t dsc;
        lv_draw_rect_dsc_init(&dsc);
        dsc.bg_color = color;
        dsc.bg_opa   = opa;
        dsc.radius   = radius;
        if (glow > 0) {
            dsc.shadow_width = glow;
            dsc.shadow_color = color;
            dsc.shadow_opa   = LV_OPA_40;
        }
        lv_area_t area = {X(x), Y(y), X(x) + S(w) - 1, Y(y) + S(h) - 1};
        lv_draw_rect(layer, &dsc, &area);
    }

    // Filled ellipse centred on (cx, cy).
    void oval(int cx, int cy, int w, int h, lv_color_t color, lv_opa_t opa = LV_OPA_COVER, int glow = 0)
    {
        rect(cx - w / 2, cy - h / 2, w, h, color, LV_RADIUS_CIRCLE, opa, glow);
    }

    // LVGL angles: 0 = right, 90 = down (clockwise).
    void arc(int cx, int cy, int r, int start, int end, int width, lv_color_t color)
    {
        lv_draw_arc_dsc_t dsc;
        lv_draw_arc_dsc_init(&dsc);
        dsc.center      = {X(cx), Y(cy)};
        dsc.radius      = S(r);
        dsc.start_angle = start;
        dsc.end_angle   = end;
        dsc.width       = S(width);
        dsc.color       = color;
        dsc.rounded     = 1;
        lv_draw_arc(layer, &dsc);
    }

    void line(int x1, int y1, int x2, int y2, int width, lv_color_t color)
    {
        lv_draw_line_dsc_t dsc;
        lv_draw_line_dsc_init(&dsc);
        dsc.p1          = {(lv_value_precise_t)X(x1), (lv_value_precise_t)Y(y1)};
        dsc.p2          = {(lv_value_precise_t)X(x2), (lv_value_precise_t)Y(y2)};
        dsc.width       = S(width);
        dsc.color       = color;
        dsc.round_start = 1;
        dsc.round_end   = 1;
        lv_draw_line(layer, &dsc);
    }

    void triangle(int x1, int y1, int x2, int y2, int x3, int y3, lv_color_t color)
    {
        lv_draw_triangle_dsc_t dsc;
        lv_draw_triangle_dsc_init(&dsc);
        dsc.p[0]  = {(lv_value_precise_t)X(x1), (lv_value_precise_t)Y(y1)};
        dsc.p[1]  = {(lv_value_precise_t)X(x2), (lv_value_precise_t)Y(y2)};
        dsc.p[2]  = {(lv_value_precise_t)X(x3), (lv_value_precise_t)Y(y3)};
        dsc.color = color;
        dsc.opa   = LV_OPA_COVER;
        lv_draw_triangle(layer, &dsc);
    }

    void text(int x, int y, const char* str, lv_color_t color, const lv_font_t* font)
    {
        lv_draw_label_dsc_t dsc;
        lv_draw_label_dsc_init(&dsc);
        dsc.color = color;
        dsc.font  = font;
        dsc.text  = str;
        lv_area_t area = {X(x), Y(y), X(x) + 80, Y(y) + 40};
        lv_draw_label(layer, &dsc, &area);
    }

    /* ------------------------------ Face pieces ------------------------------ */

    // A glowing oval eye with a dark pupil looking towards (gx, gy) in -1..1, and a glint.
    void eye(int cx, int cy, int w, int h, float gx, float gy, int open, float pupil = 0.56f, bool glints = false)
    {
        int eh = std::max(4, h * open / 100);
        oval(cx, cy, w, eh, Cream, LV_OPA_COVER, 14);
        if (open < 35) {
            return;
        }
        int pd = int(w * pupil);
        int px = cx + int(gx * w * 0.16f);
        int py = cy + int(gy * eh * 0.16f);
        oval(px, py, pd, std::min(pd, eh - 8), Ink);
        int g = std::max(4, pd / 3);
        oval(px - pd / 5, py - pd / 5, g, g, Cream);
        if (glints) {
            oval(px + pd / 5, py + pd / 6, g / 2 + 1, g / 2 + 1, Cream);
        }
    }

    // Closed, smiling eye: an arch "∩".
    void happy_eye(int cx, int cy, int r = 20) { arc(cx, cy + r / 2, r, 200, 340, 9, Cream); }

    // Closed, relaxed or sad eye: a cup "∪".
    void closed_eye(int cx, int cy, int r = 18) { arc(cx, cy - r / 2, r, 25, 155, 8, Cream); }

    // Eye with the top covered by a straight eyelid (bored, annoyed, sleepy, confident).
    void lidded_eye(int cx, int cy, int w, int h, float lid, float gx, float gy, int open, float tilt = 0)
    {
        eye(cx, cy, w, h, gx, gy, open);
        int top    = cy - h / 2 - 16;  // above the eye's glow too
        int cover  = int(h * lid) + 16;
        int dx     = w / 2 + 14;
        int lean   = int(tilt * 10);
        // The lid: a dark band whose lower edge is slanted by `tilt`. A rectangle plus one wedge
        // (overlapping by a pixel) leaves no seam for the eye's glow to show through.
        int straight = cover - std::abs(lean);
        rect(cx - dx, top, 2 * dx, straight + 2, Ink);
        if (lean != 0) {
            int low_x = lean > 0 ? cx - dx : cx + dx;
            triangle(cx - dx, top + straight, cx + dx, top + straight, low_x, top + cover + std::abs(lean), Ink);
        }
    }

    void heart(int cx, int cy, int s, lv_color_t color)
    {
        int r = s / 4;
        oval(cx - r, cy - r / 2, 2 * r + 2, 2 * r + 2, color);
        oval(cx + r, cy - r / 2, 2 * r + 2, 2 * r + 2, color);
        triangle(cx - 2 * r - 1, cy, cx + 2 * r + 1, cy, cx, cy + int(s * 0.62f), color);
    }

    // Four-point sparkle.
    void sparkle(int cx, int cy, int s, lv_color_t color)
    {
        int t = std::max(2, s / 4);
        triangle(cx - t, cy, cx + t, cy, cx, cy - s, color);
        triangle(cx - t, cy, cx + t, cy, cx, cy + s, color);
        triangle(cx, cy - t, cx, cy + t, cx - s, cy, color);
        triangle(cx, cy - t, cx, cy + t, cx + s, cy, color);
    }

    void brow(int cx, int cy, int len, int tilt, bool left)
    {
        int dy = left ? tilt : -tilt;  // positive tilt: inner end lower (angry)
        line(cx - len / 2, cy - dy, cx + len / 2, cy + dy, 6, Cream);
    }

    void smile(int cx, int cy, int r = 18, int width = 7) { arc(cx, cy - r / 2, r, 30, 150, width, Cream); }
    void frown(int cx, int cy, int r = 20, int width = 7) { arc(cx, cy + r, r, 210, 330, width, Cream); }

    // Open laughing mouth "D", flat on top.
    void open_smile(int cx, int cy, int w, int h)
    {
        oval(cx, cy, w, 2 * h, Cream);
        rect(cx - w / 2 - 2, cy - h - 2, w + 4, h + 2, Ink);
        oval(cx, cy + h / 2, w / 2, h / 2, Pink, LV_OPA_80);  // tongue
        rect(cx - w / 2, cy - 3, w, 4, Cream, 2);
    }

    void o_mouth(int cx, int cy, int w, int h)
    {
        oval(cx, cy, w, h, Cream);
        oval(cx, cy, w - 10, h - 10, Ink);
    }

    void wavy(int cx, int cy, int w)
    {
        int seg = w / 4;
        for (int i = 0; i < 4; ++i) {
            int x = cx - w / 2 + i * seg;
            line(x, cy + (i % 2 ? 4 : -4), x + seg, cy + (i % 2 ? -4 : 4), 5, Cream);
        }
    }

    void blush(int cx, int cy)
    {
        oval(cx, cy, 34, 16, Blush, LV_OPA_50);
        for (int i = -1; i <= 1; ++i) {
            line(cx + i * 9 - 3, cy + 5, cx + i * 9 + 3, cy - 5, 3, Blush);
        }
    }

    void tear_stream(int cx, int top, uint32_t t)
    {
        rect(cx - 6, top, 12, 46, Tear, 6, LV_OPA_80);
        int drop = int((t / 12) % 60);
        oval(cx, top + 46 + drop / 2, 12, 16, Tear, LV_OPA_70);
    }

    void motion_lines(int x, int y, bool right)
    {
        int s = right ? 1 : -1;
        line(x, y, x + s * 10, y - 14, 4, Gold);
        line(x + s * 16, y + 6, x + s * 30, y - 4, 4, Gold);
    }
};

}  // namespace

const char* expression_name(Expression expression)
{
    int i = static_cast<int>(expression);
    return i >= 0 && i < static_cast<int>(Expression::Count) ? Names[i] : "neutral";
}

bool expression_from_name(const std::string& name, Expression& out)
{
    for (int i = 0; i < static_cast<int>(Expression::Count); ++i) {
        if (name == Names[i]) {
            out = static_cast<Expression>(i);
            return true;
        }
    }
    return false;
}

FaceCanvas::~FaceCanvas()
{
    if (_canvas) {
        lv_obj_delete(_canvas);
    }
}

void FaceCanvas::create(lv_obj_t* parent, void* buffer)
{
    _canvas = lv_canvas_create(parent);
    lv_canvas_set_buffer(_canvas, buffer, Width, Height, LV_COLOR_FORMAT_RGB565);
    lv_obj_align(_canvas, LV_ALIGN_CENTER, 0, 0);
    lv_obj_remove_flag(_canvas, LV_OBJ_FLAG_CLICKABLE);
    render(FaceState());
}

void FaceCanvas::render(const FaceState& s)
{
    if (!_canvas) {
        return;
    }
    lv_canvas_fill_bg(_canvas, Ink, LV_OPA_COVER);
    lv_layer_t layer;
    lv_canvas_init_layer(_canvas, &layer);
    Painter p{&layer, s.offset_x, s.offset_y};

    const int lx = CenterX - EyeDx, rx = CenterX + EyeDx, ey = EyeY, my = MouthY;
    const int open = s.eye_open;
    const uint32_t t = s.time_ms;
    bool talking = s.mouth_open > 12;
    bool mouth_drawn = false;  // the expression keeps its own open mouth while talking

    switch (s.expression) {
        case Expression::Happy:
            p.happy_eye(lx, ey);
            p.happy_eye(rx, ey);
            if (!talking) p.smile(CenterX, my);
            break;
        case Expression::Excited:
            p.eye(lx, ey, EyeW + 6, EyeH + 6, 0, 0, open, 0.62f);
            p.eye(rx, ey, EyeW + 6, EyeH + 6, 0, 0, open, 0.62f);
            if (open > 35) {
                p.sparkle(lx, ey, 14, Cream);
                p.sparkle(rx, ey, 14, Cream);
            }
            p.open_smile(CenterX, my - 4, 44, 16);
            mouth_drawn = true;
            p.motion_lines(rx + 30, ey - 50, true);
            p.sparkle(lx - 40, ey - 50, 9, Gold);
            break;
        case Expression::Love:
            p.heart(lx, ey - 10, 52, Pink);
            p.heart(rx, ey - 10, 52, Pink);
            if (!talking) p.smile(CenterX, my);
            p.heart(rx + 44, ey - 62, 20, Pink);
            p.heart(rx + 58, ey - 46, 12, Pink);
            break;
        case Expression::Sad:
            p.eye(lx, ey + 6, EyeW - 4, EyeH - 8, 0, 0.6f, open);
            p.eye(rx, ey + 6, EyeW - 4, EyeH - 8, 0, 0.6f, open);
            p.brow(lx, ey - 40, 34, -6, true);
            p.brow(rx, ey - 40, 34, -6, false);
            if (!talking) p.frown(CenterX, my + 4);
            break;
        case Expression::Crying:
            p.closed_eye(lx, ey);
            p.closed_eye(rx, ey);
            p.brow(lx, ey - 34, 32, -6, true);
            p.brow(rx, ey - 34, 32, -6, false);
            p.tear_stream(lx, ey + 8, t);
            p.tear_stream(rx, ey + 8, t + 400);
            p.wavy(CenterX, my + 6, 40);
            mouth_drawn = true;
            break;
        case Expression::Angry:
            p.lidded_eye(lx, ey, EyeW, EyeH - 6, 0.38f, 0.2f, 0.2f, open, 1.4f);
            p.lidded_eye(rx, ey, EyeW, EyeH - 6, 0.38f, -0.2f, 0.2f, open, -1.4f);
            p.brow(lx + 4, ey - 38, 38, 10, true);
            p.brow(rx - 4, ey - 38, 38, 10, false);
            if (!talking) p.frown(CenterX, my + 6, 16);
            p.line(rx + 40, ey - 62, rx + 52, ey - 50, 4, Gold);
            p.line(rx + 52, ey - 62, rx + 40, ey - 50, 4, Gold);
            break;
        case Expression::Shy:
            p.happy_eye(lx, ey, 18);
            p.happy_eye(rx, ey, 18);
            p.blush(lx - 8, ey + 34);
            p.blush(rx + 8, ey + 34);
            if (!talking) p.smile(CenterX, my, 12, 6);
            break;
        case Expression::Surprised:
            p.eye(lx, ey, EyeW + 10, EyeH + 12, 0, 0, open, 0.4f);
            p.eye(rx, ey, EyeW + 10, EyeH + 12, 0, 0, open, 0.4f);
            p.o_mouth(CenterX, my + 4, 30, 36);
            mouth_drawn = true;
            p.motion_lines(rx + 30, ey - 50, true);
            p.motion_lines(lx - 30, ey - 50, false);
            break;
        case Expression::Sleepy:
            p.lidded_eye(lx, ey + 6, EyeW, EyeH - 10, 0.62f, 0, 0.6f, std::min(open, 80));
            p.lidded_eye(rx, ey + 6, EyeW, EyeH - 10, 0.62f, 0, 0.6f, std::min(open, 80));
            if (!talking) p.oval(CenterX, my + 2, 16, 14, Cream);
            p.text(rx + 34, ey - 66, "Z", Gold, &lv_font_montserrat_24);
            p.text(rx + 52, ey - 72, "z", Gold, &lv_font_montserrat_16);
            break;
        case Expression::Thinking:
            p.eye(lx, ey, EyeW, EyeH, 0.8f, -0.8f, open);
            p.eye(rx, ey, EyeW, EyeH, 0.8f, -0.8f, open);
            p.brow(rx, ey - 44, 32, -4, false);
            if (!talking) p.line(CenterX - 4, my, CenterX + 18, my - 4, 6, Cream);
            p.text(rx + 40, ey - 70, "?", Gold, &lv_font_montserrat_24);
            break;
        case Expression::Confident:
            p.lidded_eye(lx, ey, EyeW, EyeH - 4, 0.32f, 0, 0, open);
            p.lidded_eye(rx, ey, EyeW, EyeH - 4, 0.32f, 0, 0, open);
            if (!talking) p.arc(CenterX + 6, my - 10, 18, 40, 130, 7, Cream);  // a small smirk
            p.sparkle(rx + 44, ey - 62, 10, Gold);
            break;
        case Expression::Neutral:
            p.eye(lx, ey, EyeW, EyeH, 0, 0, open);
            p.eye(rx, ey, EyeW, EyeH, 0, 0, open);
            if (!talking) p.line(CenterX - 12, my, CenterX + 12, my, 6, Cream);
            break;
        case Expression::Confused:
            p.eye(lx, ey + 4, EyeW - 8, EyeH - 10, -0.5f, 0.4f, open);
            p.eye(rx, ey - 2, EyeW + 6, EyeH + 6, 0.6f, -0.6f, open);
            p.brow(lx, ey - 32, 30, -5, true);
            p.brow(rx, ey - 50, 32, 2, false);
            if (!talking) p.wavy(CenterX, my + 2, 34);
            p.text(rx + 40, ey - 70, "?", Gold, &lv_font_montserrat_24);
            break;
        case Expression::Annoyed:
            p.lidded_eye(lx, ey, EyeW, EyeH - 6, 0.5f, 0.8f, 0.2f, open);
            p.lidded_eye(rx, ey, EyeW, EyeH - 6, 0.5f, 0.8f, 0.2f, open);
            if (!talking) p.line(CenterX - 14, my + 2, CenterX + 14, my - 2, 6, Cream);
            for (int i = 0; i < 3; ++i) {
                p.arc(rx + 46, ey - 58, 6 + i * 4, 0 + i * 60, 260 + i * 60, 2, Gold);  // a scribble
            }
            break;
        case Expression::Scared: {
            int shake = (t / 60) % 2 ? 2 : -2;
            p.eye(lx + shake, ey, EyeW + 6, EyeH + 8, 0, 0, open, 0.34f);
            p.eye(rx + shake, ey, EyeW + 6, EyeH + 8, 0, 0, open, 0.34f);
            p.brow(lx, ey - 46, 32, -7, true);
            p.brow(rx, ey - 46, 32, -7, false);
            p.wavy(CenterX + shake, my + 4, 44);
            mouth_drawn = true;
            p.arc(lx - 50, ey, 14, 120, 240, 3, Cream);
            p.arc(rx + 50, ey, 14, 300, 60, 3, Cream);
            break;
        }
        case Expression::Laughing:
            p.happy_eye(lx, ey + 2, 22);
            p.happy_eye(rx, ey + 2, 22);
            p.open_smile(CenterX, my - 6, 62, 22);
            mouth_drawn = true;
            p.motion_lines(rx + 30, ey - 50, true);
            p.motion_lines(lx - 30, ey - 50, false);
            break;
        case Expression::Bored:
            p.lidded_eye(lx, ey + 4, EyeW, EyeH - 8, 0.55f, 0, 0.5f, open);
            p.lidded_eye(rx, ey + 4, EyeW, EyeH - 8, 0.55f, 0, 0.5f, open);
            if (!talking) p.line(CenterX - 10, my + 2, CenterX + 10, my + 2, 6, Cream);
            break;
        case Expression::Pleading:
            p.eye(lx, ey + 2, EyeW + 8, EyeH + 8, 0, 0.2f, open, 0.74f, true);
            p.eye(rx, ey + 2, EyeW + 8, EyeH + 8, 0, 0.2f, open, 0.74f, true);
            p.brow(lx, ey - 46, 26, -5, true);
            p.brow(rx, ey - 46, 26, -5, false);
            if (!talking) p.arc(CenterX - 7, my - 4, 7, 20, 160, 4, Cream);  // a tiny "w"
            if (!talking) p.arc(CenterX + 7, my - 4, 7, 20, 160, 4, Cream);
            p.sparkle(rx + 44, ey - 50, 8, Gold);
            break;
        case Expression::Wink:
            p.happy_eye(lx, ey, 20);
            p.eye(rx, ey, EyeW, EyeH, 0, 0, open);
            if (!talking) p.smile(CenterX + 4, my, 18);
            p.sparkle(rx + 44, ey - 60, 10, Gold);
            break;
        case Expression::LookingAround: {
            // The gaze drifts from side to side.
            float gx = std::sin(t / 700.0f);
            p.eye(lx, ey, EyeW, EyeH, gx, -0.1f, open);
            p.eye(rx, ey, EyeW, EyeH, gx, -0.1f, open);
            if (!talking) p.oval(CenterX, my, 14, 12, Cream);
            break;
        }
        case Expression::Curious:
            p.eye(lx, ey, EyeW + 4, EyeH + 4, 0.3f, -0.3f, open, 0.6f);
            p.eye(rx, ey, EyeW + 4, EyeH + 4, 0.3f, -0.3f, open, 0.6f);
            p.brow(rx, ey - 48, 30, 0, false);
            if (!talking) p.oval(CenterX, my + 2, 16, 18, Cream);
            if (!talking) p.oval(CenterX, my + 2, 8, 10, Ink);
            // A small gold light bulb: an idea.
            p.oval(rx + 46, ey - 60, 20, 20, Gold, LV_OPA_COVER, 10);
            p.rect(rx + 41, ey - 51, 10, 9, Gold, 2);
            break;
        case Expression::Disappointed:
            p.lidded_eye(lx, ey + 8, EyeW - 4, EyeH - 12, 0.45f, 0, 0.8f, open, 0.8f);
            p.lidded_eye(rx, ey + 8, EyeW - 4, EyeH - 12, 0.45f, 0, 0.8f, open, -0.8f);
            if (!talking) p.frown(CenterX, my + 4, 16, 6);
            break;
        case Expression::Proud:
            p.oy -= 6;  // chin up
            p.happy_eye(lx, ey - 4, 20);
            p.happy_eye(rx, ey - 4, 20);
            if (!talking) p.smile(CenterX, my - 4, 20);
            p.motion_lines(lx - 30, ey - 50, false);
            p.motion_lines(rx + 30, ey - 50, true);
            p.sparkle(CenterX, ey - 64, 9, Gold);
            break;
        case Expression::Energetic:
            p.line(lx - 18, ey + 8, lx, ey - 12, 9, Cream);  // "^ ^"
            p.line(lx, ey - 12, lx + 18, ey + 8, 9, Cream);
            p.line(rx - 18, ey + 8, rx, ey - 12, 9, Cream);
            p.line(rx, ey - 12, rx + 18, ey + 8, 9, Cream);
            p.open_smile(CenterX, my - 6, 56, 20);
            mouth_drawn = true;
            p.motion_lines(rx + 30, ey - 50, true);
            p.motion_lines(lx - 30, ey - 50, false);
            p.sparkle(rx + 50, ey - 60, 9, Gold);
            break;
        case Expression::Count:
            break;
    }

    // Talking: an open mouth that follows the voice, unless the expression already has one open.
    if (talking && !mouth_drawn) {
        int h = 8 + s.mouth_open * 26 / 100;
        p.oval(CenterX, my + 2, 34, h, Cream);
        p.oval(CenterX, my + 2 + h / 6, 22, std::max(2, h - 10), Ink);
    }

    lv_canvas_finish_layer(_canvas, &layer);
}

}  // namespace hiwar
