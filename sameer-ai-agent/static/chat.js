/* محادثة سمير: ترسل الرسائل إلى /api/chat وتعرض الردود كنص عادي */
(function () {
  'use strict';
  var doc = document;
  var log = doc.querySelector('[data-chat-log]');
  var form = doc.querySelector('[data-chat-form]');
  if (!log || !form) return;

  var input = form.querySelector('textarea');
  var sendBtn = form.querySelector('button[type="submit"]');
  var starters = doc.querySelector('[data-chat-starters]');
  var resetBtn = doc.querySelector('[data-chat-reset]');
  var greeting = log.innerHTML;
  var KEY = 'sameer-conversation';
  var conversationId = null;
  var busy = false;

  try { conversationId = sessionStorage.getItem(KEY); } catch (e) {}

  function remember(id) {
    conversationId = id;
    try {
      if (id) sessionStorage.setItem(KEY, id); else sessionStorage.removeItem(KEY);
    } catch (e) {}
  }

  function add(kind, text) {
    var div = doc.createElement('div');
    div.className = 'msg msg--' + kind;
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  function setBusy(on) {
    busy = on;
    sendBtn.disabled = on;
    input.disabled = on;
  }

  function send(text) {
    text = text.trim();
    if (!text || busy) return;
    if (starters) starters.hidden = true;
    add('user', text);
    input.value = '';
    setBusy(true);
    var typing = add('sameer msg--typing', 'سمير يفكر…');

    fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, conversation_id: conversationId })
    })
      .then(function (res) {
        return res.json().then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
      })
      .then(function (r) {
        typing.remove();
        if (r.ok) {
          remember(r.data.conversation_id);
          add('sameer', r.data.reply || '…');
        } else if (r.status === 409) {
          remember(null);
          add('error', 'طالت المحادثة كثيرًا. ابدأوا محادثة جديدة.');
        } else {
          add('error', 'تعذر الوصول إلى سمير. حاولوا مرة أخرى.');
        }
      })
      .catch(function () {
        typing.remove();
        add('error', 'تعذر الاتصال بالخادم.');
      })
      .then(function () {
        setBusy(false);
        input.focus();
      });
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    send(input.value);
  });

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send(input.value);
    }
  });

  if (starters) {
    starters.addEventListener('click', function (e) {
      var btn = e.target.closest('button');
      if (btn) send(btn.textContent);
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener('click', function () {
      if (busy) return;
      remember(null);
      log.innerHTML = greeting;
      if (starters) starters.hidden = false;
      input.focus();
    });
  }
})();
