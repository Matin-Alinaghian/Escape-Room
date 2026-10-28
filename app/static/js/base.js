(function(){
  "use strict";

  // ---------- mobile menu ----------
  var toggle = document.querySelector('.menu-toggle');
  var nav = document.querySelector('.main-nav');
  if (toggle && nav){
    toggle.addEventListener('click', function(){
      var open = toggle.getAttribute('aria-expanded') === 'true';
      toggle.setAttribute('aria-expanded', String(!open));
      nav.style.display = open ? 'none' : 'flex';
      if (!open){
        nav.style.position = 'absolute';
        nav.style.top = '60px';
        nav.style.right = '22px';
        nav.style.left = '22px';
        nav.style.flexDirection = 'column';
        nav.style.backgroundColor = '#0b0b0e';
        nav.style.padding = '14px';
        nav.style.borderBottom = '1px solid #2a2a32';
        nav.style.gap = '2px';
      } else {
        nav.style.position = '';
        nav.style.top = '';
        nav.style.right = '';
        nav.style.left = '';
        nav.style.flexDirection = '';
        nav.style.backgroundColor = '';
        nav.style.padding = '';
        nav.style.borderBottom = '';
        nav.style.gap = '';
      }
    });
    window.addEventListener('resize', function(){
      if (window.innerWidth > 900){
        nav.style.display = '';
        nav.style.position = '';
        nav.style.top = '';
        nav.style.right = '';
        nav.style.left = '';
        nav.style.flexDirection = '';
        nav.style.backgroundColor = '';
        nav.style.padding = '';
        nav.style.borderBottom = '';
        nav.style.gap = '';
        toggle.setAttribute('aria-expanded','false');
      }
    });
  }

  // ---------- auto-dismissing flash notices ----------
  // Success/info notices animate away after ~1s (see .flash-out in the CSS).
  // Drop them from the DOM when that finishes so they cannot be re-read by
  // screen readers or restored by a back-navigation. Errors never animate and
  // stay untouched.
  document.querySelectorAll('.flash > div, .admin-flashes > .flash').forEach(function(el){
    el.addEventListener('animationend', function(ev){
      if (!/flash-out/.test(ev.animationName || '')) return;
      var parent = el.parentElement;
      el.remove();
      if (parent && !parent.children.length && parent.classList.contains('flash')) {
        parent.remove();
      }
    });
  });

  // ---------- FAQ accordion ----------
  var faqs = document.querySelectorAll('.faq-item');
  faqs.forEach(function(item){
    var btn = item.querySelector('.faq-q');
    if (!btn) return;
    btn.addEventListener('click', function(){
      var isOpen = item.classList.contains('open');
      // close others
      faqs.forEach(function(other){
        if (other !== item) other.classList.remove('open');
        var b = other.querySelector('.faq-q');
        if (b) b.setAttribute('aria-expanded','false');
      });
      item.classList.toggle('open', !isOpen);
      btn.setAttribute('aria-expanded', String(!isOpen));
    });
  });

  // ---------- password fields: show/hide eye + rule hint ----------
  // Progressive enhancement: the server-rendered markup stays a plain password
  // input, and the eye only appears once this script wraps it (see .pw-wrap in
  // the stylesheet). The same wrapper is used on the login, sign-up, account
  // and forgot-password cards because they all render `.fld` rows.
  var EYE_OPEN = '<svg class="eye-on" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"></path>' +
    '<circle cx="12" cy="12" r="3"></circle></svg>';
  var EYE_SHUT = '<svg class="eye-off" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M4 5l16 14"></path>' +
    '<path d="M9.6 6.1A9.6 9.6 0 0 1 12 5.5c6.4 0 10 6.5 10 6.5a17 17 0 0 1-3.2 4"></path>' +
    '<path d="M6.4 8.2A16.5 16.5 0 0 0 2 12s3.6 6.5 10 6.5a9.9 9.9 0 0 0 4-.8"></path></svg>';

  document.querySelectorAll('.fld input[type="password"], .field input[type="password"]').forEach(function(input){
    var wrap = document.createElement('span');
    wrap.className = 'pw-wrap';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'pw-toggle';
    btn.setAttribute('aria-label', 'نمایش رمز عبور');
    btn.setAttribute('aria-pressed', 'false');
    btn.innerHTML = EYE_OPEN + EYE_SHUT;
    wrap.appendChild(btn);

    btn.addEventListener('click', function(){
      var revealed = input.type === 'password';
      input.type = revealed ? 'text' : 'password';
      btn.classList.toggle('on', revealed);
      btn.setAttribute('aria-pressed', String(revealed));
      btn.setAttribute('aria-label', revealed ? 'پنهان کردن رمز عبور' : 'نمایش رمز عبور');
      input.focus();
    });
  });

  // A hint that states a rule ("at least 10 characters") turns green as soon
  // as the field satisfies it, so the form answers while you type.
  document.querySelectorAll('.fld input[data-min-len]').forEach(function(input){
    var min = parseInt(input.getAttribute('data-min-len'), 10) || 0;
    // Search the whole row: the password wrapper above already inserted the
    // eye button between this input and its hint.
    var row = input.closest('.fld') || input.parentNode;
    var hint = row.querySelector('.hint');
    if (!hint) return;
    var sync = function(){ hint.classList.toggle('ok', input.value.length >= min); };
    input.addEventListener('input', sync);
    sync();
  });

  // ---------- gallery thumb switching ----------
  var thumbs = document.querySelectorAll('.gallery-thumb');
  if (thumbs.length){
    var mainImg = document.querySelector('.gallery-main-img');
    thumbs.forEach(function(t){
      t.addEventListener('click', function(){
        thumbs.forEach(function(x){ x.classList.remove('active'); });
        t.classList.add('active');
        var src = t.getAttribute('data-img') || t.querySelector('img').getAttribute('src');
        if (mainImg && src) mainImg.setAttribute('src', src);
      });
    });
  }

  // ---------- average-rating meters animate in ----------
  // The CSS sweeps every .star-fill from zero to its inline --pct. Meters that
  // are already on screen when the page loads just play; the ones further down
  // are held at zero and released the moment they scroll into view, so nobody
  // scrolls down onto a meter that has already finished animating off-screen.
  // The exact percentage is untouched, so a 4.75 average still stops with the
  // fifth star exactly 75% lit.
  //
  // Two things the release must not depend on: the lit layer itself (a held one
  // is parked at width:0, and an observer never reports a zero-area target, so
  // watching it would hide the rating for good), and the observer delivering at
  // all (some embedded browsers, and any context that stops painting frames,
  // are silent). So the meter — sized by its dim track — is what gets observed,
  // and a rect sweep behind the observer runs on scroll, on resize, and on a
  // plain timer, releasing anything the observer never reported.
  (function(){
    var meters = document.querySelectorAll('.stars.star-meter');
    if (!meters.length) return;
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    var held = [];
    var viewportH = window.innerHeight || document.documentElement.clientHeight;
    meters.forEach(function(meter){
      var fill = meter.querySelector('.star-fill');
      if (!fill) return;
      if (meter.getBoundingClientRect().top < viewportH) return;  // visible now: let it play
      fill.classList.add('meter-hold');
      held.push(meter);
    });
    if (!held.length) return;

    var io = null;
    var ticking = false;
    var watchdog = null;

    function release(meter){
      var fill = meter.querySelector('.star-fill');
      if (fill) fill.classList.remove('meter-hold');
      var i = held.indexOf(meter);
      if (i >= 0) held.splice(i, 1);
      if (io) io.unobserve(meter);
      if (!held.length) teardown();
    }

    function sweep(){
      ticking = false;
      var h = window.innerHeight || document.documentElement.clientHeight;
      held.slice().forEach(function(meter){
        var r = meter.getBoundingClientRect();
        if (r.top < h && r.bottom > 0) release(meter);
      });
    }

    function onScroll(){
      if (ticking) return;
      ticking = true;
      if (window.requestAnimationFrame) requestAnimationFrame(sweep);
      else sweep();
    }

    function teardown(){
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
      document.removeEventListener('visibilitychange', onVisible);
      if (watchdog) { clearInterval(watchdog); watchdog = null; }
      if (io) { io.disconnect(); io = null; }
    }

    function onVisible(){
      if (document.visibilityState === 'visible') sweep();  // rAF may be idle
    }

    if ('IntersectionObserver' in window){
      io = new IntersectionObserver(function(entries){
        entries.forEach(function(entry){
          if (entry.isIntersecting) release(entry.target);
        });
      }, {threshold: 0});
      held.forEach(function(meter){ io.observe(meter); });
    }

    window.addEventListener('scroll', onScroll, {passive: true});
    window.addEventListener('resize', onScroll);
    document.addEventListener('visibilitychange', onVisible);
    onScroll();  // catches meters already in view in a shorter viewport

    // The observer and the scroll listener both ride on the browser painting
    // frames, and an occluded or throttled tab may deliver neither — which is
    // how a rating ends up parked, invisible, at width 0. A plain timer is not
    // tied to rendering, so it is the net under the net. It stops itself the
    // moment nothing is held any more.
    watchdog = setInterval(sweep, 400);
  })();
})();
