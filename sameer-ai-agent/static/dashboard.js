/* لوحة سمير: تقرأ /api/dashboard وتعرضها، وتربط أزرار الجلسة بالروبوت */
(function () {
  'use strict';
  var doc = document;
  var $ = function (sel, root) { return (root || doc).querySelector(sel); };
  var $$ = function (sel, root) { return [].slice.call((root || doc).querySelectorAll(sel)); };

  var CAT_ICONS = { 'الذكريات': 'i-photo', 'الامتنان': 'i-heart', 'الأحلام': 'i-moon', 'الحكايات': 'i-book', 'القيم': 'i-sprout' };
  var AVATAR_COLORS = [['#E8EEF7', '#0F2942'], ['#FBF3DD', '#8A6A0B'], ['#E8F5EE', '#1F7A52'], ['#F1ECF8', '#5B3F8C'], ['#FDEEEC', '#9A3B2E']];
  var ROLE_OPTIONS = ['الأب', 'الأم', 'الابن', 'الابنة', 'الجد', 'الجدة', 'الأخ', 'الأخت'];
  var PREVIEW_ROWS = 4;

  var state = { data: null, range: 30, filter: null, query: '', showAll: false, busy: false };

  /* ---------- أدوات ---------- */
  function h(tag, attrs, children) {
    var node = doc.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') node.textContent = attrs[k];
      else if (k === 'class') node.className = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }
  function icon(id, size) {
    var svg = doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'i');
    svg.setAttribute('aria-hidden', 'true');
    if (size) { svg.style.width = size + 'px'; svg.style.height = size + 'px'; }
    var use = doc.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#' + id);
    svg.appendChild(use);
    return svg;
  }
  function catIcon(category) {
    return h('span', { class: 'cat-ico' }, [icon(CAT_ICONS[category] || 'i-chat')]);
  }
  function minutesText(n) {
    if (n === 1) return 'دقيقة واحدة';
    if (n === 2) return 'دقيقتان';
    if (n >= 3 && n <= 10) return n + ' دقائق';
    return n + ' دقيقة';
  }
  function minutesUnit(n) { return n >= 3 && n <= 10 ? 'دقائق' : 'دقيقة'; }
  function peopleText(n) {
    if (n === 1) return 'مشارك واحد';
    if (n === 2) return 'مشاركان';
    if (n >= 3 && n <= 10) return n + ' مشاركين';
    return n + ' مشاركًا';
  }
  function api(method, url, body) {
    return fetch(url, {
      method: method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined
    }).then(function (res) {
      return res.json().then(function (json) {
        if (!res.ok) throw new Error(json.error || 'تعذر الاتصال بالخادم');
        return json;
      });
    });
  }

  /* ---------- الروبوت ---------- */
  var robotTpl = $('#tpl-robot');
  function mountRobots() {
    $$('[data-robot]').forEach(function (slot) {
      if (slot.firstChild) return;
      var svg = robotTpl.content.firstElementChild.cloneNode(true);
      svg.setAttribute('data-mood', slot.getAttribute('data-robot'));
      slot.appendChild(svg);
    });
  }
  function mood(slot, m) { var r = slot && slot.querySelector('.robot'); if (r) r.setAttribute('data-mood', m); }

  /* ---------- المؤشر ---------- */
  function renderBond(bond) {
    var C = 326.7;
    $('[data-bond-value]').textContent = bond.value;
    $('[data-bond-bar]').style.strokeDashoffset = String(C * (1 - Math.min(bond.value, 100) / 100));
    $('[data-bond-ring]').setAttribute('aria-label', 'مؤشر الترابط العائلي ' + bond.value + ' من 100');
    $('[data-bond-label]').textContent = bond.label;
    var pill = $('[data-bond-trend]');
    pill.textContent = '';
    pill.className = 'trend-pill';
    if (bond.change_pct === null) {
      pill.classList.add('flat');
      pill.textContent = 'أول شهر مع سمير';
    } else {
      if (bond.change_pct < 0) pill.classList.add('flat');
      pill.appendChild(icon(bond.change_pct < 0 ? 'i-dn' : 'i-up', 14));
      pill.appendChild(doc.createTextNode(Math.abs(bond.change_pct) + '% مقارنة بالشهر الماضي'));
    }
  }

  /* ---------- بطاقات المؤشرات ---------- */
  function setDelta(name, text, up) {
    var el = $('[data-delta="' + name + '"]');
    el.textContent = '';
    el.className = 'delta' + (up ? '' : ' flat');
    if (up) el.appendChild(icon('i-up', 14));
    el.appendChild(doc.createTextNode(text));
  }
  function countDelta(n) {
    if (n > 0) return ['+' + n + ' عن الشهر الماضي', true];
    if (n < 0) return [Math.abs(n) + ' أقل من الشهر الماضي', false];
    return ['مثل الشهر الماضي', false];
  }
  function renderKpis(k) {
    $('[data-kpi="sessions"]').textContent = k.sessions;
    setDelta.apply(null, ['sessions'].concat(countDelta(k.sessions_change)));

    var dur = $('[data-kpi="duration"]');
    dur.textContent = '';
    if (k.avg_duration_minutes === null) {
      dur.textContent = '—';
      setDelta('duration', 'تظهر بعد أول جلسة مكتملة', false);
    } else {
      dur.appendChild(doc.createTextNode(k.avg_duration_minutes));
      dur.appendChild(h('small', { text: minutesUnit(k.avg_duration_minutes) }));
      if (k.duration_change_pct === null) setDelta('duration', 'أول شهر من الجلسات', false);
      else if (k.duration_change_pct >= 0) setDelta('duration', '+' + k.duration_change_pct + '% عن الشهر الماضي', true);
      else setDelta('duration', Math.abs(k.duration_change_pct) + '% أقصر من الشهر الماضي', false);
    }

    $('[data-kpi="participants"]').textContent = k.participants;
    if (k.members_total && k.participants >= k.members_total) setDelta('participants', 'جميع أفراد العائلة', true);
    else if (k.members_total) setDelta('participants', 'من أصل ' + k.members_total + ' أفراد', false);
    else setDelta('participants', 'أضيفوا أفراد العائلة من الإعدادات', false);

    $('[data-kpi="questions"]').textContent = k.questions;
    setDelta.apply(null, ['questions'].concat(countDelta(k.questions_change)));
  }

  /* ---------- المخطط ---------- */
  var SVGNS = 'http://www.w3.org/2000/svg';
  function s(tag, attrs) {
    var node = doc.createElementNS(SVGNS, tag);
    Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }
  function renderChart() {
    var wrap = $('[data-chart]');
    var points = state.data.trend.slice(-state.range);
    $('[data-trend-sub]').textContent = $('[data-range] option[value="' + state.range + '"]').textContent;
    wrap.textContent = '';

    var W = Math.max(wrap.clientWidth, 300), H = 280;
    var pad = { l: 36, r: 16, t: 28, b: 30 };
    var iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    var x = function (i) { return pad.l + (points.length === 1 ? iw / 2 : i * iw / (points.length - 1)); };
    // Start the axis just below the lowest value so the month's movement is visible.
    var low = Math.min.apply(null, points.map(function (p) { return p.value; }));
    var lo = Math.max(0, Math.floor((low - 5) / 20) * 20);
    var y = function (v) { return pad.t + ih - (v - lo) / (100 - lo) * ih; };

    var svg = s('svg', { class: 'chart', viewBox: '0 0 ' + W + ' ' + H, width: W, height: H, dir: 'ltr', role: 'img' });
    svg.setAttribute('aria-label', 'تطور مؤشر الترابط العائلي من ' + points[0].value + ' إلى ' + points[points.length - 1].value);
    var defs = s('defs', {});
    var grad = s('linearGradient', { id: 'areaGrad', x1: 0, y1: 0, x2: 0, y2: 1 });
    grad.appendChild(s('stop', { offset: 0, 'stop-color': '#C9A227', 'stop-opacity': '.28' }));
    grad.appendChild(s('stop', { offset: 1, 'stop-color': '#C9A227', 'stop-opacity': '0' }));
    defs.appendChild(grad);
    svg.appendChild(defs);

    [0, 1, 2, 3, 4].map(function (k) { return Math.round(lo + k * (100 - lo) / 4); }).forEach(function (v) {
      svg.appendChild(s('line', { class: 'grid-line', x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v) }));
      var t = s('text', { class: 'axis', x: pad.l - 10, y: y(v) + 4, 'text-anchor': 'end' });
      t.textContent = v;
      svg.appendChild(t);
    });
    var step = Math.max(1, Math.round((points.length - 1) / 5));
    points.forEach(function (p, i) {
      if (i % step !== 0 && i !== points.length - 1) return;
      if (i !== points.length - 1 && points.length - 1 - i < step / 2) return;
      var t = s('text', { class: 'axis', x: x(i), y: H - 8, 'text-anchor': 'middle' });
      t.textContent = Number(p.date.slice(8));
      svg.appendChild(t);
    });

    var d = '';
    points.forEach(function (p, i) {
      if (i === 0) { d = 'M' + x(0) + ' ' + y(p.value); return; }
      var mx = (x(i - 1) + x(i)) / 2;
      d += ' C' + mx + ' ' + y(points[i - 1].value) + ' ' + mx + ' ' + y(p.value) + ' ' + x(i) + ' ' + y(p.value);
    });
    svg.appendChild(s('path', { d: d + ' L' + x(points.length - 1) + ' ' + y(lo) + ' L' + x(0) + ' ' + y(lo) + 'Z', fill: 'url(#areaGrad)' }));
    svg.appendChild(s('path', { class: 'line', d: d }));

    var last = points.length - 1;
    var best = points.reduce(function (b, p, i) { return p.value > points[b].value ? i : b; }, 0);
    points.forEach(function (p, i) {
      if (p.sessions && i !== last && i !== best) svg.appendChild(s('circle', { class: 'pt', cx: x(i), cy: y(p.value), r: 3.5 }));
    });
    [best, last].forEach(function (i, n) {
      if (n === 1 || i !== last) svg.appendChild(s('circle', { class: 'pt-key', cx: x(i), cy: y(points[i].value), r: 6 }));
    });
    var tag = s('g', {});
    var tx = Math.min(x(last), W - pad.r - 18);
    tag.appendChild(s('rect', { x: tx - 18, y: y(points[last].value) - 34, width: 36, height: 24, rx: 7, fill: '#0A1930' }));
    var tt = s('text', { x: tx, y: y(points[last].value) - 17, 'text-anchor': 'middle', fill: '#fff', 'font-weight': 700, 'font-size': 13, 'font-family': 'Cairo' });
    tt.textContent = points[last].value;
    tag.appendChild(tt);
    svg.appendChild(tag);

    var hover = s('line', { class: 'hover-line', y1: pad.t, y2: pad.t + ih, x1: -10, x2: -10 });
    svg.appendChild(hover);
    var overlay = s('rect', { x: pad.l, y: pad.t, width: iw, height: ih, fill: 'transparent' });
    svg.appendChild(overlay);
    wrap.appendChild(svg);

    var tip = h('div', { class: 'chart-tip', hidden: '' });
    wrap.appendChild(tip);
    overlay.addEventListener('pointermove', function (e) {
      var rect = svg.getBoundingClientRect();
      var px = (e.clientX - rect.left) * (W / rect.width);
      var i = Math.max(0, Math.min(points.length - 1, Math.round((px - pad.l) / iw * (points.length - 1))));
      var p = points[i];
      hover.setAttribute('x1', x(i)); hover.setAttribute('x2', x(i));
      tip.hidden = false;
      tip.textContent = '';
      tip.appendChild(h('b', { class: 'num', text: String(p.value) }));
      tip.appendChild(h('div', { text: new Date(p.date + 'T12:00:00').toLocaleDateString('ar', { day: 'numeric', month: 'long', numberingSystem: 'latn' }) + (p.sessions ? ' · ' + (p.sessions === 1 ? 'جلسة واحدة' : p.sessions + ' جلسات') : '') }));
      tip.style.left = (x(i) * rect.width / W) + 'px';
      tip.style.top = (y(p.value) * rect.height / H) + 'px';
    });
    overlay.addEventListener('pointerleave', function () { tip.hidden = true; hover.setAttribute('x1', -10); hover.setAttribute('x2', -10); });
  }

  /* ---------- المواضيع ---------- */
  function renderTopics(topics) {
    var box = $('[data-topics]');
    box.textContent = '';
    topics.forEach(function (t, i) {
      var name = h('span', { class: 'name', text: t.category }, [
        h('small', { text: t.sessions ? (t.sessions === 1 ? 'جلسة واحدة' : t.sessions === 2 ? 'جلستان' : t.sessions + ' جلسات') : 'لا جلسات بعد' })
      ]);
      var fill = h('span', { class: 'fill' + (i === 0 && t.pct !== null ? ' gold' : '') });
      box.appendChild(h('div', { class: 'bar-row' }, [
        catIcon(t.category), name, h('span', { class: 'pct num', text: t.pct === null ? '—' : t.pct + '%' }),
        h('span', { class: 'track', 'aria-hidden': 'true' }, [fill])
      ]));
      requestAnimationFrame(function () { fill.style.width = (t.pct || 0) + '%'; });
    });
  }

  /* ---------- المشاركة ---------- */
  function renderPeople(list) {
    var box = $('[data-people]');
    box.textContent = '';
    if (!list.length) {
      box.appendChild(h('p', { class: 'empty', text: 'أضيفوا أفراد العائلة من الإعدادات ليظهر هنا من شارك في الجلسات.' }));
      return;
    }
    list.forEach(function (p, i) {
      var c = AVATAR_COLORS[i % AVATAR_COLORS.length];
      var av = h('span', { class: 'avatar', 'aria-hidden': 'true' }, [icon('i-person')]);
      av.style.background = c[0]; av.style.color = c[1];
      var fill = h('span', { class: 'fill' + (i === 0 ? ' gold' : '') });
      box.appendChild(h('div', { class: 'person' }, [
        av, h('b', { text: p.role }),
        h('span', { class: 'track', 'aria-hidden': 'true' }, [fill]),
        h('span', { class: 'pct num', text: p.pct === null ? '—' : p.pct + '%' })
      ]));
      requestAnimationFrame(function () { fill.style.width = (p.pct || 0) + '%'; });
    });
    if (list.every(function (p) { return p.pct === null; })) {
      box.appendChild(h('p', { class: 'muted', text: 'تظهر النسب بعد أول جلسة يُعرف فيها من شارك.' }));
    }
  }

  /* ---------- الجلسات ---------- */
  function renderSessions() {
    var box = $('[data-sessions]');
    var q = state.query.trim();
    var rows = state.data.recent_sessions.filter(function (r) {
      if (state.filter && r.category !== state.filter) return false;
      if (!q) return true;
      return (r.topic + ' ' + r.category + ' ' + r.weekday).indexOf(q) !== -1;
    });
    var limited = !state.showAll && !q && !state.filter;
    var shown = limited ? rows.slice(0, PREVIEW_ROWS) : rows;
    $('[data-show-all]').hidden = !limited || rows.length <= PREVIEW_ROWS;

    var chips = $('[data-filters]');
    chips.textContent = '';
    chips.hidden = !state.filter;
    if (state.filter) {
      var clear = h('button', { type: 'button', 'aria-label': 'إزالة التصفية' }, [icon('i-close', 12)]);
      clear.addEventListener('click', function () { state.filter = null; setNav('sessions'); renderSessions(); });
      chips.appendChild(h('span', { class: 'chip', text: 'فئة ' + state.filter }, [clear]));
    }

    box.textContent = '';
    if (!shown.length) {
      box.appendChild(h('p', { class: 'empty', text: q || state.filter ? 'لا توجد جلسات مطابقة.' : 'لا توجد جلسات بعد. المسوا سمير على المائدة لبدء أول جلسة.' }));
      return;
    }
    shown.forEach(function (r) {
      var meta = h('div', { class: 'session-m' }, [h('span', { text: r.weekday })]);
      if (r.duration_minutes !== null) meta.appendChild(h('span', {}, [icon('i-clock'), doc.createTextNode(minutesText(r.duration_minutes))]));
      if (r.participants) meta.appendChild(h('span', {}, [icon('i-users'), doc.createTextNode(peopleText(r.participants))]));
      var side = h('div', {}, [h('span', { class: 'status' + (r.status === 'completed' ? '' : ' wait'), text: r.status === 'completed' ? 'مكتملة' : 'بانتظار التقييم' })]);
      if (r.rating) {
        var pips = h('div', { class: 'pips', role: 'img', 'aria-label': 'التقييم ' + r.rating + ' من 3' });
        for (var i = 1; i <= 3; i++) pips.appendChild(h('i', { class: i <= r.rating ? 'on' : '' }));
        side.appendChild(pips);
      }
      box.appendChild(h('div', { class: 'session' }, [
        catIcon(r.category),
        h('div', { style: 'min-width:0' }, [h('p', { class: 'session-t', text: r.topic, title: r.topic }), meta]),
        side
      ]));
    });
  }

  /* ---------- ملاحظة سمير والاقتراح ---------- */
  function renderInsight() {
    var d = state.data;
    $('[data-insight]').textContent = d.insight.text;
    $('[data-insight-src]').textContent = d.insight.source === 'gemini' ? 'كتبها سمير من أرقام آخر 30 يومًا' : 'من أرقام آخر 30 يومًا';
    renderSuggestion(d.suggestion);
  }
  function renderSuggestion(sg) {
    var q = $('[data-next-q]'), cat = $('[data-next-cat]');
    q.classList.remove('loading');
    if (!sg) { q.textContent = '…'; cat.hidden = true; return; }
    q.textContent = sg.question;
    cat.hidden = false;
    cat.textContent = '';
    cat.appendChild(icon(CAT_ICONS[sg.category] || 'i-chat', 14));
    cat.appendChild(doc.createTextNode(sg.category));
    $('[data-queued]').hidden = !sg.queued;
    mood($('[data-insight-robot]'), sg.queued ? 'listening' : 'happy');
    renderUpdates();
  }
  function setBusy(on) {
    state.busy = on;
    $$('[data-queue],[data-explore]').forEach(function (b) { b.disabled = on; });
  }
  function explore(scroll) {
    if (state.busy) return;
    if (scroll) $('.insight').scrollIntoView({ behavior: 'smooth', block: 'center' });
    setBusy(true);
    var q = $('[data-next-q]');
    q.classList.add('loading');
    q.textContent = 'سمير يفكر في سؤال جديد…';
    mood($('[data-insight-robot]'), 'thinking');
    api('POST', '/api/suggestion').then(function (sg) {
      state.data.suggestion = sg;
      renderSuggestion(sg);
    }).catch(function (err) {
      q.classList.remove('loading');
      q.textContent = err.message;
      mood($('[data-insight-robot]'), 'happy');
    }).then(function () { setBusy(false); });
  }
  function queue() {
    if (state.busy) return;
    setBusy(true);
    mood($('[data-insight-robot]'), 'thinking');
    api('POST', '/api/suggestion/queue').then(function (sg) {
      state.data.suggestion = sg;
      renderSuggestion(sg);
      mood($('[data-robot="listening"]'), 'happy');
    }).catch(function (err) {
      $('[data-next-q]').textContent = err.message;
    }).then(function () { setBusy(false); });
  }

  /* ---------- التحديثات ---------- */
  function renderUpdates() {
    var d = state.data, list = $('[data-updates]');
    list.textContent = '';
    var waiting = false;
    if (d.suggestion && d.suggestion.queued) {
      list.appendChild(h('li', { text: 'سؤال جاهز على سمير' }, [h('small', { text: d.suggestion.question })]));
    }
    d.recent_sessions.slice(0, 3).forEach(function (r) {
      if (r.status !== 'completed') waiting = true;
      list.appendChild(h('li', { text: 'جلسة ' + r.category + ' يوم ' + r.weekday }, [
        h('small', { text: r.status === 'completed' ? 'اكتملت' + (r.rating ? ' بتقييم ' + r.rating + ' من 3' : '') : 'بانتظار تقييم العائلة' })
      ]));
    });
    if (!list.children.length) list.appendChild(h('li', { text: 'لا توجد تحديثات بعد.' }));
    $('[data-bell-dot]').hidden = !(waiting || (d.suggestion && d.suggestion.queued));
  }

  /* ---------- التحميل ---------- */
  function render() {
    var d = state.data;
    $$('[data-bind="family-name"]').forEach(function (n) { n.textContent = d.family.name; });
    $('[data-demo]').hidden = !d.demo;
    renderBond(d.bond);
    renderKpis(d.kpis);
    renderChart();
    renderTopics(d.topics);
    renderPeople(d.participation);
    renderSessions();
    renderInsight();
  }
  function load() {
    return api('GET', '/api/dashboard').then(function (data) {
      state.data = data;
      render();
      if (!data.suggestion) explore(false);
    }).catch(function (err) {
      $('[data-insight]').textContent = 'تعذر تحميل بيانات اللوحة: ' + err.message;
    });
  }

  /* ---------- الإعدادات ---------- */
  var dialog = $('[data-settings]');
  var form = $('[data-settings-form]');
  function memberRow(m) {
    var role = h('input', { class: 'field', list: 'roles', maxlength: '30', placeholder: 'الصفة', 'aria-label': 'الصفة', required: '' });
    role.value = m.role || '';
    var age = h('input', { class: 'field', type: 'number', min: '1', max: '120', placeholder: 'العمر', 'aria-label': 'العمر' });
    if (m.age) age.value = m.age;
    var del = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'حذف' }, [icon('i-close', 16)]);
    var row = h('div', { class: 'member-row' }, [role, age, del]);
    del.addEventListener('click', function () { row.remove(); });
    return row;
  }
  function openSettings() {
    if (!state.data) return;
    var fam = state.data.demo ? { name: '', members: [] } : state.data.family;
    form.elements.name.value = fam.name === 'عائلتي' ? '' : fam.name;
    var box = $('[data-members]');
    box.textContent = '';
    (fam.members.length ? fam.members : [{}]).forEach(function (m) { box.appendChild(memberRow(m)); });
    $('[data-form-err]').hidden = true;
    dialog.showModal();
  }
  var datalist = h('datalist', { id: 'roles' });
  ROLE_OPTIONS.forEach(function (r) { datalist.appendChild(h('option', { value: r })); });
  doc.body.appendChild(datalist);

  $('[data-add-member]').addEventListener('click', function () { $('[data-members]').appendChild(memberRow({})); });
  $('[data-close-settings]').addEventListener('click', function () { dialog.close(); });
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var members = $$('.member-row', form).map(function (row) {
      var inputs = row.querySelectorAll('input');
      return { role: inputs[0].value.trim(), age: inputs[1].value ? Number(inputs[1].value) : null };
    }).filter(function (m) { return m.role; });
    api('POST', '/family', { name: form.elements.name.value.trim(), members: members }).then(function () {
      dialog.close();
      load();
    }).catch(function (err) {
      var el = $('[data-form-err]');
      el.textContent = err.message;
      el.hidden = false;
    });
  });

  /* ---------- التنقل ---------- */
  var sidebar = $('#sidebar'), menuBtn = $('[data-menu]');
  function setMenu(open) { sidebar.classList.toggle('open', open); menuBtn.setAttribute('aria-expanded', String(open)); }
  function setNav(name) { $$('.nav a').forEach(function (a) { a.setAttribute('aria-current', String(a.dataset.nav === name)); }); }
  menuBtn.addEventListener('click', function () { setMenu(!sidebar.classList.contains('open')); });
  $$('.nav a').forEach(function (a) {
    a.addEventListener('click', function (e) {
      setMenu(false);
      if (a.hasAttribute('data-open-settings')) return;
      setNav(a.dataset.nav);
      if (a.dataset.nav === 'memories' || a.dataset.nav === 'sessions') {
        state.filter = a.dataset.filter || null;
        if (state.data) renderSessions();
      }
      if (a.dataset.nav === 'top') { e.preventDefault(); window.scrollTo({ top: 0, behavior: 'smooth' }); }
    });
  });
  $$('[data-open-settings]').forEach(function (b) {
    b.addEventListener('click', function (e) { e.preventDefault(); setMenu(false); openSettings(); });
  });

  var bell = $('[data-bell]'), pop = $('[data-bell-pop]');
  bell.addEventListener('click', function (e) {
    e.stopPropagation();
    pop.hidden = !pop.hidden;
    bell.setAttribute('aria-expanded', String(!pop.hidden));
  });
  doc.addEventListener('click', function (e) { if (!pop.hidden && !pop.contains(e.target)) { pop.hidden = true; bell.setAttribute('aria-expanded', 'false'); } });
  doc.addEventListener('keydown', function (e) { if (e.key === 'Escape') { pop.hidden = true; setMenu(false); } });

  $('[data-search]').addEventListener('input', function (e) {
    state.query = e.target.value;
    if (state.data) renderSessions();
  });
  $('[data-search]').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') $('#sessions').scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
  $('[data-show-all]').addEventListener('click', function () { state.showAll = true; renderSessions(); });
  $('[data-range]').addEventListener('change', function (e) { state.range = Number(e.target.value); renderChart(); });
  $$('[data-queue]').forEach(function (b) { b.addEventListener('click', queue); });
  $$('[data-explore]').forEach(function (b) { b.addEventListener('click', function () { explore(b.closest('.welcome') !== null); }); });

  var resizeTimer;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { if (state.data) renderChart(); }, 150);
  });

  mountRobots();
  load();
  setInterval(function () { if (!doc.hidden && !state.busy && !dialog.open) load(); }, 60000);
})();
