/* Ambient sound design for the escape-room site.
 *
 * A looping background track (static/audio/theme.mp3) plus short UI ticks on
 * interactive elements, synthesised inline with the Web Audio API.
 *
 * Behaviour
 *   - ON BY DEFAULT, and it starts by itself. The track is attempted the
 *     moment every page loads, so a browser that allows autoplay is playing
 *     before the visitor touches anything. A browser that refuses sound until
 *     the first interaction (Chrome, Firefox, Safari, every mobile browser)
 *     starts it on the visitor's first pointer, touch, key, wheel or scroll —
 *     no button has to be found or clicked first.
 *   - The visitor can still switch it off with the public "صدا" toggle, and
 *     that choice ("0") persists in localStorage, which is what keeps the
 *     music from coming back while they do not want it.
 *   - The admin's server-side preference arrives as data attributes on the
 *     toggle button: data-sound-enabled / data-sound-volume (set by
 *     app/__init__.py's context processor). When the admin turns sound off,
 *     the control is hidden and playback will not start.
 *   - The track ships with the site: app/static/audio/theme.mp3 is copied to
 *     the static mirror like every other asset and served from this app's own
 *     origin. No CDN or any other host is ever contacted for it. Should the
 *     file be missing or undecodable anyway, a synthesised room bed takes over
 *     instead of leaving the visitor in silence.
 *   - The visitor's own on/off + volume persist in localStorage, clamped to
 *     the server-side volume ceiling.
 *   - The track loops forever (loop=true). Its volume is faded on the media
 *     element itself, so a suspended AudioContext (the usual reason an
 *     audible-looking <audio> is actually silent) can never swallow it.
 *
 * Continuity across pages
 *   This is a multi-page site: every nav click throws the document (and the
 *   <audio> element with it) away. So the *fact* that the track was playing
 *   and the second it had reached are mirrored into sessionStorage, which
 *   survives navigation inside one tab. The next page seeks straight back to
 *   that second and keeps going at full volume instead of starting over —
 *   walking from خانه to قوانین does not restart the music.
 */

(function () {
  "use strict";

  var LS = {
    ENABLED: "escape-room.audio-enabled",
    VOLUME: "escape-room.audio-volume",
  };

  // Per-tab state (cleared when the tab closes): what the track is doing *right
  // now*. localStorage is the visitor's lasting choice, this is the playback.
  var SS = {
    PLAYING: "escape-room.audio-playing",
    TIME: "escape-room.audio-time",
  };

  // Returns null for "no value stored", so callers can fall back to the
  // admin's ceiling. Number(null) is 0, which is why the empty case is spelled
  // out here: without it a visitor with no saved level would be handed a zero
  // and the track would play at no volume at all.
  var clamp01 = function (v) {
    if (v === null || v === undefined || v === "") return null;
    v = Number(v);
    if (!isFinite(v)) return null;
    return Math.min(1, Math.max(0, v));
  };

  // Server-side prefs from the toggle button's data attributes.
  function readServerPrefs() {
    var btn = document.getElementById("audioToggle");
    var out = { enabled: true, volume: 0.18 };
    if (btn) {
      out.enabled = btn.getAttribute("data-sound-enabled") !== "0";
      var v = clamp01(btn.getAttribute("data-sound-volume"));
      if (v !== null) out.volume = v;
    }
    return out;
  }

  var server = { enabled: true, volume: 0.18 }; // refreshed on init

  // ----------------------------------------------------------------------
  //  localStorage helpers (fail soft in private mode)
  // ----------------------------------------------------------------------

  function lsGet(key) {
    try {
      return localStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }

  function lsSet(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (e) {
      /* fail soft */
    }
  }

  // sessionStorage can throw too (Safari private mode, blocked storage).
  function ssGet(key) {
    try {
      return sessionStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }

  function ssSet(key, value) {
    try {
      sessionStorage.setItem(key, value);
    } catch (e) {
      /* fail soft */
    }
  }

  // ----------------------------------------------------------------------
  //  Playback position across page loads
  // ----------------------------------------------------------------------

  var lastSavedAt = 0;

  function savePosition() {
    if (!audioEl) return;
    var t = 0;
    try {
      t = audioEl.currentTime;
    } catch (e) {
      return;
    }
    if (isFinite(t) && t > 0) ssSet(SS.TIME, t.toFixed(2));
  }

  function markPlaying(on) {
    ssSet(SS.PLAYING, on ? "1" : "0");
    if (!on) savePosition(); // remember where it stopped
  }

  // Where the previous page got to, or 0 when this tab has no music history.
  function savedTime() {
    var t = parseFloat(ssGet(SS.TIME) || "");
    return isFinite(t) && t > 0 ? t : 0;
  }

  // `pagehide` is the reliable last call on mobile (Safari often skips
  // `beforeunload`); the visibility check covers tab switches and bfcache.
  function wirePageLifecycle() {
    var save = function () {
      savePosition();
    };
    window.addEventListener("pagehide", save);
    window.addEventListener("beforeunload", save);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") save();
    });
  }

  // ----------------------------------------------------------------------
  //  Audio engine: looping theme track via an <audio> element
  // ----------------------------------------------------------------------

  // Resolve the static root from the page's own stylesheet link so the track
  // loads from nested URLs (/rooms/<slug>) and from the static mirror alike.
  function staticBase() {
    var link = document.querySelector('link[href*="css/base.css"]');
    if (link) {
      var href = link.getAttribute("href") || "";
      var i = href.indexOf("css/base.css");
      if (i >= 0) return href.slice(0, i); // e.g. "/static/" or "../static/"
    }
    return "/static/";
  }

  var TRACK_SRC = staticBase() + "audio/theme.mp3";
  var audioEl = null;
  var ctx = null;
  var master = null;
  var bedNodes = []; // everything stop() must tear down
  // Set when the local track cannot be decoded: the synthesised bed plays
  // instead, so the room is never silent.
  var trackBroken = false;
  // Where the page this tab just left had got to; 0 on a fresh tab.
  var handOffAt = 0;

  function ensureContext() {
    if (ctx) return ctx;
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    ctx = new Ctx();
    master = ctx.createGain();
    // The bed and the UI ticks ride this gain; it opens at the ceiling so a
    // tick fired right after the first gesture is audible.
    master.gain.value = currentCeiling();
    master.connect(ctx.destination);
    return ctx;
  }

  function ensureTrack() {
    if (audioEl) {
      // Reuse the element; browsers log "interrupted play()" warnings only
      // when restart is forced mid-load.
      return audioEl;
    }
    audioEl = new Audio();
    audioEl.src = TRACK_SRC;
    audioEl.loop = true; // مدام تکرار می‌شود
    audioEl.preload = "auto";
    audioEl.volume = 0; // rampVolume() brings it up once playback starts

    // Keep the tab's copy of "where the music is" current: `timeupdate` fires
    // about four times a second, one small write per second is plenty.
    audioEl.addEventListener("timeupdate", function () {
      var now = Date.now();
      if (now - lastSavedAt < 1000) return;
      lastSavedAt = now;
      savePosition();
    });
    // Borrowed (not "pause"): a pause event also fires while the page is being
    // torn down, and that must never be mistaken for the visitor switching off.
    audioEl.addEventListener("playing", function () {
      markPlaying(true);
    });
    // The file travels with the site (static/audio/theme.mp3), so a decode or
    // fetch failure means the asset itself is missing. Fall back to the
    // synthesised bed rather than leaving the room silent.
    audioEl.addEventListener("error", function () {
      if (!enabled()) return;
      trackBroken = true;
      try { audioEl.pause(); } catch (e) { /* already idle */ }
      if (ctx && ctx.state === "running") startBed();
      else armGestureWake(); // the bed needs a gesture too
    });
    return audioEl;
  }

  // The track is played straight through the <audio> element and its volume is
  // faded here, not on a Web Audio gain. That is what makes "starts when you
  // arrive" true everywhere: a suspended AudioContext silently mutes anything
  // routed through it, and the element is audible the instant play() resolves.
  var fadeRaf = null;
  var fadeTimer = null;

  function setTrackVolume(v) {
    if (!audioEl) return;
    try {
      audioEl.volume = Math.min(1, Math.max(0, v));
    } catch (e) {
      // iOS keeps a media element's volume read-only; there the track simply
      // starts at the level the system control is set to.
    }
  }

  function rampVolume(target, seconds) {
    if (fadeRaf) { cancelAnimationFrame(fadeRaf); fadeRaf = null; }
    if (fadeTimer) { clearTimeout(fadeTimer); fadeTimer = null; }
    if (!audioEl) return;
    if (!(seconds > 0)) { setTrackVolume(target); return; }
    var from = audioEl.volume || 0;
    var startMs = Date.now();
    var step = function () {
      var k = Math.min(1, (Date.now() - startMs) / (seconds * 1000));
      setTrackVolume(from + (target - from) * k);
      fadeRaf = k < 1 ? requestAnimationFrame(step) : null;
    };
    fadeRaf = requestAnimationFrame(step);
    // requestAnimationFrame is throttled in a background tab; this makes sure
    // the ceiling is reached even when the page is never painted again.
    fadeTimer = setTimeout(function () { setTrackVolume(target); }, seconds * 1000 + 120);
  }

  function isPlaying() {
    return !!audioEl && !audioEl.paused && !audioEl.ended;
  }

  function currentCeiling() {
    // The visitor's saved volume, never above the admin's server-side cap.
    var raw = clamp01(lsGet(LS.VOLUME));
    var v = raw === null ? server.volume : raw;
    return Math.min(v, server.volume);
  }

  // A 2s pink-ish noise buffer with tapered ends so the loop never clicks.
  function makeNoiseBuffer() {
    var buf = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
    var d = buf.getChannelData(0);
    var last = 0;
    for (var i = 0; i < d.length; i++) {
      var white = Math.random() * 2 - 1;
      last = 0.986 * last + white * 0.05; // one-pole lowpass -> warm tone
      d[i] = last * 2.4;
    }
    // taper the seam
    var fade = Math.floor(ctx.sampleRate * 0.02);
    for (var j = 0; j < fade; j++) {
      var g = j / fade;
      d[j] *= g;
      d[d.length - 1 - j] *= g;
    }
    return buf;
  }

  // The one way playback starts: optionally pick the position the page before
  // handed over, fade the element in, and play. `fade` is in seconds: 2 on the
  // very first entry so the track eases in, 0 when a previous page handed
  // playback over — a fade on every navigation would sound like the track
  // restarts, which is exactly what the continuity work avoids.
  function play(fade, seekTo) {
    if (trackBroken) { startBed(); return true; }

    var el = ensureTrack();
    if (!el) return false;

    if (seekTo > 0) {
      lastResume = {
        from: seekTo,
        atMs: Math.round(
          window.performance && performance.now ? performance.now() : Date.now()
        ),
      };
      // Seeking before metadata arrives is ignored, so only ask once the
      // browser knows the length (a position past the end rolls back to 0,
      // which covers a track swapped for a shorter one).
      var dur = el.duration;
      try {
        el.currentTime = isFinite(dur) && seekTo >= dur - 0.5 ? 0 : seekTo;
      } catch (e) {
        /* keep the current position */
      }
    }

    var wasPlaying = isPlaying();
    if (!wasPlaying && fade > 0) setTrackVolume(0);
    rampVolume(currentCeiling(), wasPlaying ? 0 : fade);

    var p = el.play();
    if (p && p.catch) {
      p.catch(function () {
        // Autoplay policy: this document has not been interacted with yet. The
        // visitor's sound is still on, so the first gesture of any kind brings
        // the track in instead of losing it.
        if (enabled()) armGestureWake();
      });
    }
    return true;
  }

  // ----------------------------------------------------------------------
  //  Starting without a gesture, and the gesture fallback
  // ----------------------------------------------------------------------

  // What the last page load picked the track up from, and how far into that
  // page it happened. Exposed through the `_test` hooks: continuity is
  // otherwise invisible once the track is a few seconds in, and "the music
  // restarted" is impossible to tell apart from "the track looped" without it.
  var lastResume = null;

  // Every first interaction a browser may accept as the gesture that unlocks
  // sound. Wheel and scroll are not activation for Chrome, but engines differ
  // and listening for them costs nothing. They are captured (true) so the very
  // first press reaches us even if a handler on the page stops propagation.
  var GESTURE_EVENTS = [
    "pointerdown", "pointerup", "mousedown", "touchstart", "touchend",
    "keydown", "click", "wheel", "scroll",
  ];

  var gestureArmed = false;

  // Autoplay policies refuse sound in a document the visitor has not touched.
  // The track is attempted on load anyway (see init); this is the net that
  // catches the refusal, so the music arrives with the visitor's first touch,
  // key press or scroll instead of waiting to be switched on.
  function armGestureWake() {
    if (gestureArmed) return;
    gestureArmed = true;
    GESTURE_EVENTS.forEach(function (name) {
      document.addEventListener(name, wakeOnGesture, { capture: true, passive: true });
    });
  }

  function disarmGestureWake() {
    if (!gestureArmed) return;
    gestureArmed = false;
    GESTURE_EVENTS.forEach(function (name) {
      document.removeEventListener(name, wakeOnGesture, true);
    });
  }

  function wakeOnGesture(ev) {
    // A press on the sound control is about the control, not a request to start
    // the music: the toggle wires that itself.
    if (ev && ev.target && ev.target.closest && ev.target.closest(".sound-bar")) return;
    if (isPlaying() || !enabled()) {
      disarmGestureWake();
      return;
    }
    disarmGestureWake();
    // The shared AudioContext (UI ticks, room bed) needs the gesture as well,
    // so resume it here rather than leaving it suspended behind the track.
    var c = ensureContext();
    if (c && c.resume) {
      try { c.resume(); } catch (e) { /* already running */ }
    }
    // From silence: a refused attempt may have left the element at full
    // volume, and the track should ease in rather than burst in.
    setTrackVolume(0);
    play(1.2, handOffAt);
  }

  // A browser can also stop the track without telling the page: a tab restored
  // from the back/forward cache, or one Chrome paused while it was in the
  // background. Playback is picked up again when it matters, and only while
  // the visitor's sound is still on.
  function wireWakeUps() {
    var wake = function () {
      if (!enabled() || !server.enabled || isPlaying()) return;
      play(0.6, 0);
    };
    window.addEventListener("pageshow", wake);
    window.addEventListener("focus", wake);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") wake();
    });
  }

  // The synthesised stand-in for the theme track: no file, no network, just a
  // low drone and room tone, exactly like the ambience this site started with.
  // It only plays when the local mp3 cannot be decoded (the "error" listener in
  // ensureTrack), so a missing asset never leaves the room in silence.
  function startBed() {
    if (!ctx) return;
    if (ctx.resume) {
      // Safe to call when it is already running; the nodes below are scheduled
      // either way and sound the moment the context starts.
      try { ctx.resume(); } catch (e) { /* already running */ }
    }
    stopBed(); // never stack two beds on top of each other

    // -- drone: two slightly detuned low sines --------------------------
    var droneGain = ctx.createGain();
    droneGain.gain.value = 0;
    droneGain.connect(master);
    bedNodes.push({ node: droneGain, kind: "gain" });

    for (var i = 0; i < 2; i++) {
      var osc = ctx.createOscillator();
      osc.type = "sine";
      osc.frequency.value = 57 + i * 0.7;
      var g = ctx.createGain();
      g.gain.value = 0.5;
      osc.connect(g);
      g.connect(droneGain);
      osc.start();
      // slow drift so the bed is never static
      var t0 = ctx.currentTime;
      osc.frequency.linearRampToValueAtTime(57 + i * 0.7, t0 + 14);
      osc.frequency.linearRampToValueAtTime(57.4 + i * 0.7, t0 + 28);
      osc.frequency.linearRampToValueAtTime(57 + i * 0.7, t0 + 42);
      bedNodes.push({ node: osc, kind: "osc" });
    }

    // -- room tone: looping noise through a swaying lowpass --------------
    var noise = ctx.createBufferSource();
    noise.buffer = makeNoiseBuffer();
    noise.loop = true;

    var toneGain = ctx.createGain();
    toneGain.gain.value = 0.05;

    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 320;
    lp.Q.value = 1.1;

    // series routing: noise -> lowpass -> toneGain -> master, so the
    // swaying filter actually shapes the room tone
    noise.connect(lp);
    lp.connect(toneGain);
    toneGain.connect(master);

    noise.start();
    bedNodes.push({ node: noise, kind: "src" });
    bedNodes.push({ node: lp, kind: "gain" });
    bedNodes.push({ node: toneGain, kind: "gain" });

    // swaying filter LFO
    var lfo = ctx.createOscillator();
    lfo.frequency.value = 0.07;
    var lfoAmt = ctx.createGain();
    lfoAmt.gain.value = 90;
    lfo.connect(lfoAmt);
    lfoAmt.connect(lp.frequency);
    lfo.start();
    bedNodes.push({ node: lfo, kind: "osc" });

    // -- fade the whole bed in -------------------------------------------
    var t = ctx.currentTime;
    master.gain.cancelScheduledValues(t);
    master.gain.setValueAtTime(0, t);
    master.gain.linearRampToValueAtTime(currentCeiling() * 0.9, t + 2.5);

    // Distant drips keep the bed from sounding like a held chord.
    scheduleNextDrip();
  }

  function stopBed() {
    var t = ctx ? ctx.currentTime : 0;
    bedNodes.forEach(function (n) {
      try {
        if (n.kind === "osc" || n.kind === "src") n.node.stop(t);
      } catch (e) {
        /* already stopped */
      }
    });
    bedNodes.forEach(function (n) {
      try {
        n.node.disconnect();
      } catch (e) {
        /* already disconnected */
      }
    });
    bedNodes = [];
  }

  // One distant drip: a short noise burst through a sweeping bandpass.
  function scheduleDrip(when) {
    var dur = 0.08 + Math.random() * 0.25;
    var src = ctx.createBufferSource();
    var buf = ctx.createBuffer(1, Math.ceil(ctx.sampleRate * (dur + 0.05)), ctx.sampleRate);
    var d = buf.getChannelData(0);
    for (var j = 0; j < d.length; j++) {
      d[j] = (Math.random() * 2 - 1) * Math.pow(1 - j / d.length, 0.7);
    }
    src.buffer = buf;

    var bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.frequency.setValueAtTime(1600 + Math.random() * 1200, when);
    bp.frequency.exponentialRampToValueAtTime(300, when + dur);
    bp.Q.value = 6;

    var g = ctx.createGain();
    g.gain.setValueAtTime(0, when);
    g.gain.linearRampToValueAtTime(0.5, when + 0.006);
    g.gain.exponentialRampToValueAtTime(0.001, when + dur);

    src.connect(bp);
    bp.connect(g);
    g.connect(master);
    src.start(when);
    src.stop(when + dur + 0.05);
  }

  // One UI tick: a short filtered click, an octave above the bed.
  function scheduleTick(when) {
    var osc = ctx.createOscillator();
    osc.type = "sine";
    osc.frequency.value = 1200 + Math.random() * 600;
    var g = ctx.createGain();
    g.gain.setValueAtTime(0, when);
    g.gain.linearRampToValueAtTime(0.06, when + 0.002);
    g.gain.exponentialRampToValueAtTime(0.001, when + 0.05);
    osc.connect(g);
    g.connect(master);
    osc.start(when);
    osc.stop(when + 0.06);
  }

  // ----------------------------------------------------------------------
  //  Event loop: drips while enabled
  // ----------------------------------------------------------------------

  var dripTimer = null;

  function scheduleNextDrip() {
    if (!ctx || !enabled()) return;
    dripTimer = setTimeout(function () {
      if (!ctx || !enabled()) return;
      scheduleDrip(ctx.currentTime + 0.05);
      scheduleNextDrip(); // 4–11s apart, like a distant dripping pipe
    }, 4000 + Math.random() * 7000);
  }

  // ----------------------------------------------------------------------
  //  Public API
  // ----------------------------------------------------------------------

  // ON unless the visitor switched it off. Sound therefore starts by itself on
  // arrival, and only an explicit "off" (stored as "0") keeps it quiet.
  function enabled() {
    return lsGet(LS.ENABLED) !== "0";
  }

  function getVolume() {
    var v = clamp01(lsGet(LS.VOLUME));
    return v === null ? server.volume : v;
  }

  function setVolume(v) {
    var c = clamp01(v);
    if (c === null) return getVolume();
    c = Math.min(c, server.volume); // never above the admin's ceiling
    lsSet(LS.VOLUME, String(c));
    rampVolume(c, 0); // the track, straight on the media element
    if (ctx && master) {
      var t = ctx.currentTime;
      master.gain.cancelScheduledValues(t);
      master.gain.setTargetAtTime(c, t, 0.15);
    }
    return c;
  }

  function start() {
    if (!server.enabled) return false;
    // The toggle click is a real gesture, so the shared context resumes and the
    // track starts straight away — no autoplay fallback needed on this path.
    var c = ensureContext();
    if (c && c.resume) {
      try { c.resume(); } catch (e) { /* already running */ }
    }
    return play(1.2, 0);
  }

  function stop() {
    markPlaying(false); // the next page must not pick the track back up
    if (dripTimer) {
      clearTimeout(dripTimer);
      dripTimer = null;
    }
    if (ctx) {
      var t = ctx.currentTime;
      master.gain.cancelScheduledValues(t);
      master.gain.setValueAtTime(master.gain.value, t);
      master.gain.linearRampToValueAtTime(0, t + 0.3);
    }
    disarmGestureWake(); // a later press must not undo the visitor's choice
    if (audioEl) {
      // Fade the element out, then pause (not reset) so "resume" picks up where
      // it left off; the loop attribute keeps it going while enabled.
      rampVolume(0, 0.3);
      setTimeout(function () {
        try { audioEl.pause(); } catch (e) { /* noop */ }
        if (ctx) {
          try { ctx.close(); } catch (e) { /* already closed */ }
        }
        stopBed();
        ctx = null;
        master = null;
        audioEl = null;
      }, 400);
    }
    lsSet(LS.ENABLED, "0");
  }

  function enable() {
    lsSet(LS.ENABLED, "1");
    ssSet(SS.PLAYING, "1"); // so an immediate navigation still continues it
    return start();
  }

  function disable() {
    stop();
  }

  function toggle() {
    return enabled() ? disable() : enable();
  }

  function getEnabledState() {
    return { enabled: enabled(), volume: getVolume(), serverEnabled: server.enabled };
  }

  var api = {
    enable: enable,
    disable: disable,
    toggle: toggle,
    setVolume: setVolume,
    getVolume: getVolume,
    enabled: enabled,
    getEnabledState: getEnabledState,
    _test: {
      scheduleDrip: function (t) {
        if (ctx) scheduleDrip(t || ctx.currentTime + 0.05);
      },
      scheduleTick: function (t) {
        if (ctx) scheduleTick(t || ctx.currentTime + 0.01);
      },
      track: function () { return audioEl; },
      // { from, atMs } of the page-load resume, or null when this page started
      // the track from the beginning.
      lastResume: function () { return lastResume; },
      // Drives the autoplay fallback on demand, so the "blocked, then a
      // gesture" path can be exercised without a browser that refuses sound.
      armGestureWake: armGestureWake,
      gestureArmed: function () { return gestureArmed; },
      isPlaying: isPlaying,
      trackBroken: function () { return trackBroken; },
      startBed: startBed,
      play: play,
    },
  };

  // ----------------------------------------------------------------------
  //  DOM wiring
  // ----------------------------------------------------------------------

  function reflectState() {
    var btn = document.getElementById("audioToggle");
    if (!btn) return;
    var on = enabled();
    btn.setAttribute("aria-pressed", String(on));
    btn.classList.toggle("is-on", on);
    var bar = btn.closest(".sound-bar");
    if (bar) bar.classList.toggle("is-on", on);
  }

  function wirePublicToggle() {
    var btn = document.getElementById("audioToggle");
    if (!btn) return;
    btn.addEventListener("click", function () {
      if (enabled()) {
        disable();
      } else {
        if (!server.enabled) {
          // Admin has sound off: show feedback, do not start the engine.
          btn.classList.add("is-denied");
          setTimeout(function () {
            btn.classList.remove("is-denied");
          }, 600);
          return;
        }
        enable();
      }
      reflectState();
    });
  }

  function wireVolumeSlider() {
    var input = document.getElementById("sound-volume");
    if (!input) return;
    var apply = function () {
      var v = parseFloat(input.value);
      if (!isFinite(v)) return;
      setVolume(v);
    };
    input.addEventListener("input", apply);
    input.addEventListener("change", apply);
  }

  // UI ticks on interactive elements — only while sound is on.
  function wireTicks() {
    document.addEventListener("click", function (ev) {
      if (!ctx || !enabled()) return;
      var el = ev.target.closest(
        "a, button, .gallery-thumb, details, summary, [role='button']"
      );
      if (el && !el.disabled) scheduleTick(ctx.currentTime + 0.01);
    }, true);
  }

  function init() {
    server = readServerPrefs();
    wirePublicToggle();
    wireVolumeSlider();
    wireTicks();
    wirePageLifecycle();
    reflectState();

    // If the admin disabled sound, hide the control entirely.
    if (!server.enabled) {
      var bar = document.querySelector(".sound-bar");
      if (bar) bar.hidden = true;
      return;
    }

    // Sound stays off only because the visitor asked for that on an earlier
    // page; the toggle already reads "off" and nothing starts here.
    if (!enabled()) return;

    // Where the page this tab just left had got to (0 on a fresh tab), so the
    // music carries on instead of starting over on every nav click.
    handOffAt = ssGet(SS.PLAYING) === "1" ? savedTime() : 0;

    // Start right now. A browser that allows autoplay already has music by the
    // time the page settles; one that refuses arms the first-gesture wake-up
    // below and starts on the visitor's first touch, key or scroll.
    play(handOffAt > 0 ? 0 : 2, handOffAt);
    if (!isPlaying()) armGestureWake();

    // Wired a beat later so the attempt above has settled: an immediate
    // focus/pageshow would otherwise look like a track that had stopped.
    setTimeout(wireWakeUps, 1500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.EscapeRoomAudio = api;
})();
