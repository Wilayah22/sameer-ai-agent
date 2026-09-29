/* سمير — سلوك الواجهة: الوضعان، القائمة، الكشف عند التمرير، لوحة الرابطة */
(function () {
  'use strict';
  var doc = document;
  var root = doc.documentElement;
  var NS = 'http://www.w3.org/2000/svg';
  root.classList.add('js');

  var reduce = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  /* ---------- الوضع الفاتح والداكن ---------- */
  var THEME_KEY = 'sameer-theme';
  var themeBtn = doc.querySelector('[data-theme-toggle]');
  function systemDark() { return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches); }
  function currentTheme() { return root.getAttribute('data-theme') || (systemDark() ? 'dark' : 'light'); }
  function syncToggle() { if (themeBtn) themeBtn.setAttribute('aria-pressed', String(currentTheme() === 'dark')); }
  try {
    var saved = window.localStorage.getItem(THEME_KEY);
    if (saved === 'dark' || saved === 'light') root.setAttribute('data-theme', saved);
  } catch (e) { /* التخزين غير متاح: نتبع النظام */ }
  if (themeBtn) {
    themeBtn.addEventListener('click', function () {
      var next = currentTheme() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { window.localStorage.setItem(THEME_KEY, next); } catch (e) { /* تجاهل */ }
      syncToggle();
    });
  }
  if ('MutationObserver' in window) {
    new MutationObserver(syncToggle).observe(root, { attributes: true, attributeFilter: ['data-theme'] });
  }
  syncToggle();

  /* ---------- الشريط العلوي والقائمة ---------- */
  var header = doc.querySelector('.site-header');
  function onScroll() { if (header) header.classList.toggle('is-scrolled', window.scrollY > 8); }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  var menuBtn = doc.querySelector('[data-menu-toggle]');
  var nav = doc.getElementById('site-nav');
  function setMenu(open) {
    if (!menuBtn || !nav) return;
    nav.classList.toggle('open', open);
    menuBtn.setAttribute('aria-expanded', String(open));
  }
  if (menuBtn && nav) {
    menuBtn.addEventListener('click', function () { setMenu(menuBtn.getAttribute('aria-expanded') !== 'true'); });
    nav.addEventListener('click', function (e) { if (e.target.closest('a')) setMenu(false); });
    doc.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && nav.classList.contains('open')) { setMenu(false); menuBtn.focus(); }
    });
  }

  /* ---------- تمييز القسم الحالي في القائمة ---------- */
  var spyLinks = [].slice.call(doc.querySelectorAll('.nav a[href*="#"]'));
  function spyTarget(a) { var h = a.getAttribute('href'); var i = h.indexOf('#'); return i >= 0 ? h.slice(i + 1) : ''; }
  if ('IntersectionObserver' in window && spyLinks.length) {
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        spyLinks.forEach(function (a) {
          if (spyTarget(a) === en.target.id) a.setAttribute('aria-current', 'true');
          else if (a.getAttribute('aria-current') === 'true') a.removeAttribute('aria-current');
        });
      });
    }, { rootMargin: '-35% 0px -60% 0px' });
    spyLinks.forEach(function (a) {
      var el = doc.getElementById(spyTarget(a));
      if (el) spy.observe(el);
    });
  }

  /* ---------- الكشف عند التمرير: انتقال بلا إخفاء للمحتوى ---------- */
  var revealEls = [].slice.call(doc.querySelectorAll('[data-reveal]'));
  if ('IntersectionObserver' in window && !reduce) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        var el = en.target;
        io.unobserve(el);
        el.classList.add('in');
        var delay = parseInt(el.style.getPropertyValue('--d'), 10) || 0;
        window.setTimeout(function () { el.removeAttribute('data-reveal'); el.classList.remove('in'); }, 900 + delay);
      });
    }, { rootMargin: '0px 0px -6% 0px', threshold: 0.05 });
    revealEls.forEach(function (el) { io.observe(el); });
  } else {
    revealEls.forEach(function (el) { el.removeAttribute('data-reveal'); });
  }

  /* ---------- مزاج الروبوت في الغلاف ---------- */
  var hero = doc.getElementById('hero-char');
  var moodTimer = 0;
  function mood(m, ms) {
    if (!hero) return;
    hero.setAttribute('data-mood', m);
    window.clearTimeout(moodTimer);
    if (ms) moodTimer = window.setTimeout(function () { hero.setAttribute('data-mood', 'happy'); }, ms);
  }
  doc.addEventListener('sameer:session', function () { mood('joy', 2600); });
  var stage = doc.querySelector('.stage');
  if (stage && hero) {
    stage.addEventListener('pointerenter', function () { if (hero.getAttribute('data-mood') === 'happy') mood('listening'); });
    stage.addEventListener('pointerleave', function () { if (hero.getAttribute('data-mood') === 'listening') mood('happy'); });
  }

  /* ---------- لوحة الرابطة الأسرية ---------- */
  var TIERS = [
    { min: 75, icon: 'i-f-joy', label: 'متفاعلة جدًا', cap: 'العائلة متفاعلة جدًا — استمروا على هذا المستوى' },
    { min: 50, icon: 'i-f-smile', label: 'تفاعل جيد', cap: 'تفاعل جيد ومستقر — جرّبوا فئات جديدة من الأسئلة' },
    { min: 25, icon: 'i-f-flat', label: 'بداية جيدة', cap: 'بداية جيدة — كل جلسة إضافية ترفع المؤشر' },
    { min: 0, icon: 'i-sprout', label: 'في البداية', cap: 'لسا بالبداية — أول جلسة عائلية تبدأ المؤشر بالتحرك' }
  ];

  /* المعادلة نفسها المعتمدة في اللوحة الحالية: الجلسات حتى 20 (40) + التقييم من 3 (45) + الفئات من 5 (15) */
  function parts(st) {
    var s = st.total_sessions || 0;
    var avg = st.average_rating_overall || 0;
    var c = Object.keys(st.average_rating_per_category || {}).length;
    return {
      s: s, avg: avg, c: c,
      sessions: Math.min(s / 20, 1) * 40,
      rating: Math.min(avg / 3, 1) * 45,
      cats: Math.min(c / 5, 1) * 15
    };
  }
  function bondScore(p) { return Math.round(p.sessions + p.rating + p.cats); }

  function el(w, name) { return w.querySelector('[data-el="' + name + '"]'); }
  function icon(id) {
    var s = doc.createElementNS(NS, 'svg');
    s.setAttribute('class', 'icon');
    s.setAttribute('aria-hidden', 'true');
    var u = doc.createElementNS(NS, 'use');
    u.setAttribute('href', '#' + id);
    s.appendChild(u);
    return s;
  }
  function tween(node, to, fmt) {
    var from = parseFloat(node.getAttribute('data-v') || '0');
    node.setAttribute('data-v', String(to));
    if (reduce || from === to || !window.requestAnimationFrame) { node.textContent = fmt(to); return; }
    var t0 = window.performance.now();
    (function step(t) {
      var k = Math.min((t - t0) / 700, 1);
      var e = 1 - Math.pow(1 - k, 3);
      node.textContent = fmt(from + (to - from) * e);
      if (k < 1) window.requestAnimationFrame(step);
    })(t0);
  }
  function pips(node, n) {
    if (!node) return;
    [].forEach.call(node.children, function (p, i) { p.classList.toggle('on', i < n); });
  }
  function emptyBox(text) {
    var d = doc.createElement('p');
    d.className = 'empty';
    d.textContent = text;
    return d;
  }

  function render(w, st) {
    var p = parts(st);
    var score = bondScore(p);
    var tier = TIERS.filter(function (t) { return score >= t.min; })[0];

    tween(el(w, 'bond'), score, function (v) { return String(Math.round(v)); });
    el(w, 'fill').style.width = score + '%';
    var bar = el(w, 'bar');
    bar.setAttribute('aria-valuenow', String(score));
    bar.setAttribute('aria-valuetext', score + ' من 100');
    var t = el(w, 'tier');
    t.textContent = '';
    t.appendChild(icon(tier.icon));
    t.appendChild(doc.createTextNode(tier.label));
    el(w, 'caption').textContent = tier.cap;

    var pts = [['sessions', p.sessions, 40], ['rating', p.rating, 45], ['cats', p.cats, 15]];
    pts.forEach(function (x) {
      el(w, 'pv-' + x[0]).textContent = Math.round(x[1]) + ' من ' + x[2];
      el(w, 'pf-' + x[0]).style.width = (x[1] / x[2]) * 100 + '%';
    });

    tween(el(w, 'sessions'), p.s, function (v) { return String(Math.round(v)); });
    tween(el(w, 'rating'), p.avg, function (v) { return v.toFixed(1); });
    tween(el(w, 'cats'), p.c, function (v) { return String(Math.round(v)); });
    pips(el(w, 'rating-pips'), Math.round(p.avg));
    pips(el(w, 'cats-pips'), p.c);

    var cats = st.average_rating_per_category || {};
    var names = Object.keys(cats);
    var list = el(w, 'catlist');
    list.textContent = '';
    if (!names.length) {
      list.appendChild(emptyBox('لا توجد بيانات كافية بعد — بعد أول جلسة عائلية ستظهر هنا'));
    } else {
      names.forEach(function (name) {
        var val = Number(cats[name]) || 0;
        var row = doc.createElement('div');
        row.className = 'cat-row';
        var n = doc.createElement('span'); n.className = 'cat-name'; n.textContent = name;
        var tr = doc.createElement('span'); tr.className = 'cat-track';
        var f = doc.createElement('span'); f.className = 'cat-fill'; f.style.width = Math.max(4, (val / 3) * 100) + '%';
        tr.appendChild(f);
        var v = doc.createElement('span'); v.className = 'cat-val'; v.textContent = val.toFixed(1);
        row.appendChild(n); row.appendChild(tr); row.appendChild(v);
        list.appendChild(row);
      });
    }

    var topics = st.recent_topics || [];
    var tl = el(w, 'topics');
    tl.textContent = '';
    if (!topics.length) {
      tl.appendChild(emptyBox('لم تُسجَّل مواضيع بعد'));
    } else {
      topics.forEach(function (tp, i) {
        var row = doc.createElement('div');
        row.className = 'topic-row';
        var b = doc.createElement('span'); b.className = 'topic-badge'; b.textContent = String(i + 1);
        var x = doc.createElement('span'); x.textContent = String(tp);
        row.appendChild(b); row.appendChild(x);
        tl.appendChild(row);
      });
    }
    return score;
  }

  /* ---------- بيانات تجريبية بالشكل نفسه الذي يعيده /stats ---------- */
  var DEMO_CATS = ['الذكريات', 'الامتنان', 'الأحلام', 'الحكايات', 'القيم'];
  var DEMO_TOPICS = [
    ['أجمل ذكرى من طفولتك', 'أول يوم لك في المدرسة'],
    ['شيء صغير تشكر عليه اليوم', 'شخص ساعدك هذا الأسبوع'],
    ['لو سافرنا كعائلة، أين نذهب؟', 'مهنة حلمت بها وأنت صغير'],
    ['حكاية سمعتها من جدّك', 'موقف طريف من رحلة قديمة'],
    ['متى شعرت أن الصدق صعب؟', 'ما معنى الكرم عندك؟']
  ];
  var DEMO_RATINGS = [3, 2, 3, 3, 2, 1, 3];
  var SEEDS = {
    preview: { cats: [0, 1, 2, 0, 1, 2], ratings: [2, 3, 2, 2, 3, 2] },
    full: { cats: [0, 1, 2, 3, 0, 1, 4, 2, 3, 0, 1, 2, 4, 3], ratings: [2, 3, 2, 3, 3, 2, 2, 3, 3, 2, 3, 3, 2, 3] }
  };
  function Demo(seed) {
    this.seed = seed;
    this.reset();
  }
  Demo.prototype.reset = function () {
    var self = this;
    this.list = this.seed.cats.map(function (c, i) {
      return { c: c, r: self.seed.ratings[i], t: DEMO_TOPICS[c][i % 2] };
    });
  };
  Demo.prototype.add = function () {
    var counts = DEMO_CATS.map(function () { return 0; });
    this.list.forEach(function (s) { counts[s.c]++; });
    var c = counts.indexOf(Math.min.apply(null, counts));
    var n = this.list.length;
    var s = { c: c, r: DEMO_RATINGS[n % DEMO_RATINGS.length], t: DEMO_TOPICS[c][n % 2] };
    this.list.push(s);
    return s;
  };
  Demo.prototype.stats = function () {
    var sum = {}, cnt = {}, all = 0;
    this.list.forEach(function (s) {
      var k = DEMO_CATS[s.c];
      sum[k] = (sum[k] || 0) + s.r;
      cnt[k] = (cnt[k] || 0) + 1;
      all += s.r;
    });
    var per = {};
    Object.keys(sum).forEach(function (k) { per[k] = sum[k] / cnt[k]; });
    return {
      total_sessions: this.list.length,
      average_rating_overall: this.list.length ? all / this.list.length : 0,
      average_rating_per_category: per,
      recent_topics: this.list.slice(-5).reverse().map(function (s) { return s.t; })
    };
  };

  function initDash(w) {
    var kind = w.getAttribute('data-dash');
    var cfg = window.SAMEER_CONFIG || {};
    var live = kind === 'full' && !!cfg.statsUrl;
    var status = el(w, 'status');
    var err = el(w, 'error');
    var stamp = el(w, 'stamp');

    [].forEach.call(w.querySelectorAll('[data-only]'), function (n) {
      n.hidden = n.getAttribute('data-only') !== (live ? 'api' : 'demo');
    });

    if (!live) {
      var demo = new Demo(SEEDS[kind === 'full' ? 'full' : 'preview']);
      render(w, demo.stats());
      w.addEventListener('click', function (e) {
        var b = e.target.closest('[data-action]');
        if (!b) return;
        var a = b.getAttribute('data-action');
        if (a === 'add') {
          var s = demo.add();
          var score = render(w, demo.stats());
          if (status) status.textContent = 'أُضيفت جلسة تجريبية في فئة ' + DEMO_CATS[s.c] + '، وأصبح المؤشر ' + score + ' من 100.';
          doc.dispatchEvent(new CustomEvent('sameer:session'));
        } else if (a === 'reset') {
          demo.reset();
          var sc = render(w, demo.stats());
          if (status) status.textContent = 'أُعيدت البيانات التجريبية، والمؤشر ' + sc + ' من 100.';
        }
      });
      return;
    }

    function load() {
      w.classList.add('is-loading');
      window.fetch(cfg.statsUrl, { headers: { Accept: 'application/json' }, cache: 'no-store' })
        .then(function (r) { if (!r.ok) throw new Error('bad status'); return r.json(); })
        .then(function (d) {
          render(w, d);
          if (err) err.hidden = true;
          if (stamp) {
            var now = new Date();
            stamp.textContent = 'آخر تحديث: ' + now.toLocaleTimeString('ar-SA', { hour: '2-digit', minute: '2-digit' });
          }
        })
        .catch(function () { if (err) err.hidden = false; })
        .then(function () { w.classList.remove('is-loading'); });
    }
    w.addEventListener('click', function (e) {
      if (e.target.closest('[data-action="refresh"], [data-action="retry"]')) load();
    });
    load();
    window.setInterval(function () { if (!doc.hidden) load(); }, 30000);
  }
  [].forEach.call(doc.querySelectorAll('[data-dash]'), initDash);
})();
