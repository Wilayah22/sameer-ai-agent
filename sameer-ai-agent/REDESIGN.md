# إعادة تصميم موقع سمير — ملفات التركيب

## الملفات
- `static/sameer.css` : نظام التصميم (primitive → semantic → component) والوضعان الفاتح والداكن
- `static/sameer.js`  : تبديل الوضع، القائمة، الحركة عند التمرير، ولوحة الرابطة
- `templates/index.html` : صفحة الهبوط
- `templates/dashboard.html` : اللوحة، وتقرأ من `/stats` كما هي اليوم

## التركيب (Flask)
1. انسخ `static/sameer.css` و `static/sameer.js` إلى مجلد `static/` في مشروعك.
2. انسخ `templates/index.html` و `templates/dashboard.html` إلى مجلد `templates/`.
3. اجعل المسارين يعرضان القالبين:

```python
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")
```

مسار `/stats` يبقى دون تغيير. يجب أن يعيد الحقول نفسها:
`total_sessions`, `average_rating_overall`, `average_rating_per_category`, `recent_topics`.

## ملاحظات
- لم أختبر الملفات على خادمك في Render. اختبرتها على خادم محلي يحاكي المسارات الثلاثة بالشكل نفسه من JSON.
- `landing.css` و `robot-hero.png` القديمان لم يعودا مستخدمين. الروبوت في الغلاف رسم SVG مدمج.
- معادلة مؤشر الرابطة منقولة كما هي من لوحتك الحالية: الجلسات حتى 20 (40 نقطة) + التقييم من 3 (45) + الفئات من 5 (15).
- أسماء الفئات والمواضيع تُعرض الآن كنص عادي (`textContent`) بدل `innerHTML`، فلا يمكن لنص مُدخل أن يحقن وسومًا في الصفحة.
