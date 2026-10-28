(function(){
  "use strict";

  // ---------- confirm destructive submits ----------
  document.addEventListener('submit', function(ev){
    var message = ev.target.getAttribute('data-confirm');
    if (message && !window.confirm(message)){
      ev.preventDefault();
    }
  });

  // ---------- mobile sidebar ----------
  var burger = document.getElementById('adminBurger');
  var side = document.getElementById('adminSide');
  var backdrop = document.getElementById('adminBackdrop');
  function setSidebar(open){
    if (side) side.classList.toggle('open', open);
    if (backdrop) backdrop.classList.toggle('show', open);
    if (burger) burger.setAttribute('aria-expanded', String(open));
  }
  if (burger && side){
    burger.addEventListener('click', function(){
      setSidebar(!side.classList.contains('open'));
    });
  }
  if (backdrop){
    backdrop.addEventListener('click', function(){ setSidebar(false); });
  }
  document.addEventListener('keydown', function(ev){
    if (ev.key === 'Escape' && side && side.classList.contains('open')){
      setSidebar(false);
      if (burger) burger.focus();
    }
  });
  // Close the drawer when the viewport grows back to desktop.
  if (window.matchMedia){
    var mq = window.matchMedia('(min-width: 821px)');
    var onMq = function(){ if (mq.matches) setSidebar(false); };
    if (mq.addEventListener) mq.addEventListener('change', onMq);
    else if (mq.addListener) mq.addListener(onMq);
  }

  // ---------- permission rows ----------
  document.querySelectorAll('[data-toggle-row]').forEach(function(btn){
    btn.addEventListener('click', function(){
      var row = document.getElementById(btn.getAttribute('data-toggle-row'));
      if (row) row.hidden = !row.hidden;
    });
  });

  // ---------- permission bulk select ----------
  // The super administrator grants permissions from the checklist below each
  // administrator; these buttons tick or clear every box at once.
  document.addEventListener('click', function(ev){
    var btn = ev.target.closest('[data-check-all]');
    if (!btn) return;
    var form = btn.closest('form');
    if (!form) return;
    var on = btn.getAttribute('data-check-all') === '1';
    form.querySelectorAll('input[type="checkbox"][name="permissions"]').forEach(function(box){
      box.checked = on;
    });
  });

  // ---------- copy media path ----------
  document.querySelectorAll('[data-copy]').forEach(function(btn){
    btn.addEventListener('click', function(){
      var value = btn.getAttribute('data-copy');
      var done = function(){
        var old = btn.textContent;
        btn.textContent = 'کپی شد';
        setTimeout(function(){ btn.textContent = old; }, 1400);
      };
      if (navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(value).then(done, done);
      } else {
        var input = document.createElement('input');
        input.value = value;
        document.body.appendChild(input);
        input.select();
        try { document.execCommand('copy'); } catch (e) {}
        document.body.removeChild(input);
        done();
      }
    });
  });

  // ---------- auto-submit filters ----------
  document.querySelectorAll('.filter-bar select[name]').forEach(function(sel){
    sel.addEventListener('change', function(){
      var form = sel.closest('form');
      if (form) form.submit();
    });
  });

  // ---------- the dashboard clock ----------
  // The hour belongs to the shop, not to the browser: the server hands over the
  // exact instant it rendered the page, and this keeps the clock counting from
  // there, so a tab left open overnight can never show yesterday's hour. The
  // two Jalali labels come from the server as well (the only side that knows
  // the calendar); the date rolls over on its own when Tehran passes midnight.
  (function(){
    var clock = document.getElementById('adminClock');
    if (!clock) return;
    var timeEl = document.getElementById('adminClockTime');
    var dateEl = document.getElementById('adminClockDate');
    var serverNow = new Date(clock.getAttribute('data-now') || '');
    if (isNaN(serverNow.getTime())) return;

    var drift = serverNow.getTime() - Date.now();
    var todayLabel = clock.getAttribute('data-today-label') || '';
    var tomorrowLabel = clock.getAttribute('data-tomorrow-label') || '';
    var startDate = clock.getAttribute('data-date') || '';
    var formatter = new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Tehran', hour: '2-digit', minute: '2-digit', hour12: false
    });
    var dayFormatter = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Tehran', year: 'numeric', month: '2-digit', day: '2-digit'
    });

    function tick(){
      var now = new Date(Date.now() + drift);
      if (timeEl) timeEl.textContent = formatter.format(now);
      if (dateEl && startDate){
        dateEl.textContent = dayFormatter.format(now) === startDate
          ? todayLabel : (tomorrowLabel || todayLabel);
      }
    }
    tick();
    setInterval(tick, 15000);
    document.addEventListener('visibilitychange', function(){
      if (!document.hidden) tick();
    });
  })();
})();