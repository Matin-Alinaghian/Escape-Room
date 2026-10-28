/* Featured-scenario carousel with a paper-turn transition.
 *
 * One card is visible at a time; prev/next (or swipe/keyboard/dots) trigger a
 * page-flip: the outgoing sheet folds away while the incoming one unfolds.
 * When the ambient sound engine is enabled (window.EscapeRoomAudio) a soft
 * paper-rustle tick fires in sync with the turn. Autoplay pauses on hover,
 * touch and keyboard interaction, and only runs while the tab is visible.
 * Everything works with sound disabled and respects prefers-reduced-motion.
 */
(function () {
  "use strict";

  var stage = document.getElementById("featStage");
  if (!stage) return;

  var cards = Array.prototype.slice.call(stage.querySelectorAll(".featured-card"));
  var dots = Array.prototype.slice.call(document.querySelectorAll(".featured-dot"));
  var prevBtn = document.getElementById("featPrev");
  var nextBtn = document.getElementById("featNext");
  if (cards.length < 2) return;

  var current = Math.max(0, cards.findIndex(function (c) { return c.classList.contains("is-active"); }));
  var turning = false;
  var paused = false;
  var timer = null;
  var AUTOPLAY_MS = 7000;

  var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function cardAt(i) {
    return cards[(i + cards.length) % cards.length];
  }

  function reflect(index) {
    cards.forEach(function (card, i) {
      var active = i === index;
      card.classList.toggle("is-active", active);
      card.setAttribute("aria-hidden", active ? "false" : "true");
      card.tabIndex = active ? 0 : -1;
    });
    dots.forEach(function (dot, i) {
      dot.classList.toggle("is-active", i === index);
      dot.setAttribute("aria-selected", i === index ? "true" : "false");
    });
  }

  function paperSound() {
    var audio = window.EscapeRoomAudio;
    if (audio && typeof audio.getEnabledState === "function" && audio.getEnabledState().enabled) {
      if (audio._test && typeof audio._test.scheduleTick === "function") {
        audio._test.scheduleTick();
      }
    }
  }

  function turn(delta) {
    if (turning) return;
    var nextIndex = (current + delta + cards.length) % cards.length;
    if (nextIndex === current) return;

    var outgoing = cards[current];
    var incoming = cardAt(nextIndex);

    if (reducedMotion) {
      current = nextIndex;
      reflect(current);
      paperSound();
      return;
    }

    turning = true;
    paperSound();

    // Paper-turn: the outgoing sheet folds down/away, the incoming unfolds.
    outgoing.classList.add(delta > 0 ? "turn-out-left" : "turn-out-right");
    incoming.classList.add(delta > 0 ? "turn-in-right" : "turn-in-left");

    window.setTimeout(function () {
      current = nextIndex;
      reflect(current);
      outgoing.classList.remove("turn-out-left", "turn-out-right");
      incoming.classList.remove("turn-in-right", "turn-in-left");
      turning = false;
    }, 480);
  }

  function goTo(index) {
    if (index === current || turning) return;
    turn(index > current ? 1 : -1);
  }

  function stopAutoplay() {
    if (timer) {
      window.clearInterval(timer);
      timer = null;
    }
  }

  function startAutoplay() {
    if (reducedMotion || timer || paused) return;
    timer = window.setInterval(function () {
      if (!document.hidden && !turning) turn(1);
    }, AUTOPLAY_MS);
  }

  // ---- user controls -------------------------------------------------
  if (nextBtn) nextBtn.addEventListener("click", function () { turn(1); });
  if (prevBtn) prevBtn.addEventListener("click", function () { turn(-1); });

  dots.forEach(function (dot, i) {
    dot.addEventListener("click", function () { goTo(i); });
  });

  // Keyboard: arrows when the stage or a control has focus.
  stage.addEventListener("keydown", function (ev) {
    if (ev.key === "ArrowLeft") { ev.preventDefault(); turn(1); }   // RTL: left = next
    if (ev.key === "ArrowRight") { ev.preventDefault(); turn(-1); }
  });

  // Touch swipe.
  var touchX = null;
  stage.addEventListener("touchstart", function (ev) {
    touchX = ev.touches[0].clientX;
    stopAutoplay();
  }, { passive: true });
  stage.addEventListener("touchend", function (ev) {
    if (touchX === null) return;
    var dx = ev.changedTouches[0].clientX - touchX;
    if (Math.abs(dx) > 42) turn(dx > 0 ? -1 : 1);  // swipe right = previous (RTL)
    touchX = null;
    startAutoplay();
  }, { passive: true });

  // Pause on any interaction; resume only after quiet idle time.
  stage.addEventListener("mouseenter", function () { paused = true; stopAutoplay(); });
  stage.addEventListener("mouseleave", function () { paused = false; startAutoplay(); });
  stage.addEventListener("focusin", function () { paused = true; stopAutoplay(); });
  stage.addEventListener("focusout", function () { paused = false; startAutoplay(); });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stopAutoplay();
    else startAutoplay();
  });

  startAutoplay();
})();
