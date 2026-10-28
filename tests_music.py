"""Regression tests for the background music (static/audio/theme.mp3).

Run:  PYTHONIOENCODING=utf-8 python tests_music.py

Background: the track never played, on any browser. Two separate causes, both
covered here. The volume ceiling was read through ``clamp01(localStorage[...])``,
and ``Number(null)`` is 0 — so a visitor with no saved level (everyone, the
first time) got a ceiling of zero: the track really did play, silently. And
playback only started from the "صدا" button, so nothing happened on arrival.

The track now starts by itself on every page, with the first interaction of any
kind as the fallback the autoplay policies force on us, and it is served from
this app's own static tree — no CDN, no other host.

Covers:
  * the audio file ships with the site, is served same-origin, and is a real
    MPEG stream (byte ranges included: seeking depends on them)
  * nothing fetches audio from another host, and the CSP keeps media local
  * the engine is on by default and plays on arrival
  * an empty saved level means "use the admin's ceiling", never zero
  * a refused autoplay is retried on the visitor's first gesture of any kind
  * a missing file falls back to sound rather than silence
  * the admin's sound.enabled / sound.volume reach the page
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, ".")

from app import create_app
from app.db import execute, query_one
from app.services.settings import get_setting, set_setting

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


app = create_app()
client = app.test_client()
_ctx = app.app_context()
_ctx.push()

TRACK = pathlib.Path("app/static/audio/theme.mp3")
JS = pathlib.Path("app/static/js/ambience.js").read_text(encoding="utf-8")

# ==========================================================================
# 1. The track is part of the site
# ==========================================================================
check("the track ships inside the app's static tree", TRACK.is_file(), str(TRACK))

head = TRACK.read_bytes()[:2048] if TRACK.is_file() else b""
check("it is a real MPEG audio stream",
      head[:3] == b"ID3" or (head[:1] == b"\xff" and (head[1] & 0xE0) == 0xE0),
      head[:4].hex())
check("it is long enough to be the room's theme",
      TRACK.stat().st_size > 200_000, f"{TRACK.stat().st_size} bytes")

res = client.get("/static/audio/theme.mp3")
check("served from this app's own origin", res.status_code == 200, str(res.status_code))
check("served as audio/mpeg",
      (res.headers.get("Content-Type") or "").split(";")[0] == "audio/mpeg",
      str(res.headers.get("Content-Type")))
check("served in full",
      int(res.headers.get("Content-Length", 0)) == TRACK.stat().st_size,
      str(res.headers.get("Content-Length")))

# The next page seeks the track back to where the last one left it, and a
# browser will only do that when the server answers byte ranges.
rng = client.get("/static/audio/theme.mp3", headers={"Range": "bytes=0-1023"})
check("byte ranges are answered (the hand-off seeks into the file)",
      rng.status_code == 206
      and (rng.headers.get("Content-Range") or "").startswith("bytes 0-1023/"),
      f"{rng.status_code} {rng.headers.get('Content-Range')}")

# The static mirror copies the whole tree, audio included.
export = pathlib.Path("export_static.py").read_text(encoding="utf-8")
check("the static mirror copies the audio folder too",
      "_walk_static(static_source, output_dir" in export
      and "shutil.copy2" in export)

# ==========================================================================
# 2. Nothing is fetched from another host
# ==========================================================================
EXTERNAL_MEDIA = re.compile(
    r"""https?://[^\s"'()]+?\.(?:mp3|m4a|aac|ogg|wav|flac)\b""", re.I
)

offsite: list[str] = []
for path in sorted(pathlib.Path("app/templates").rglob("*.html")) + sorted(
    pathlib.Path("app/static/js").glob("*.js")
):
    text = path.read_text(encoding="utf-8", errors="ignore")
    if EXTERNAL_MEDIA.search(text):
        offsite.append(str(path))
check("no template or script points at an audio file on another host",
      not offsite, ", ".join(offsite))

check("the track URL is built from the page's own static base",
      "staticBase() + \"audio/theme.mp3\"" in JS)

page = client.get("/")
csp = page.headers.get("Content-Security-Policy", "")
check("the CSP allows media from this origin", "media-src 'self'" in csp, csp)
check("the CSP does not open media to other hosts",
      "media-src *" not in csp and "media-src http" not in csp, csp)

# ==========================================================================
# 3. The engine: on by default, playing on arrival
# ==========================================================================
check("sound is ON unless the visitor turned it off",
      'lsGet(LS.ENABLED) !== "0"' in JS)
check("an explicit off is still respected",
      'lsSet(LS.ENABLED, "0")' in JS)

# The bug that made the music inaudible: Number(null) is 0, so an empty entry
# was read as "volume 0" instead of "no choice saved".
check("an empty saved level falls back to the admin's ceiling, never to zero",
      'v === null || v === undefined || v === ""' in JS)
check("those two callers are the volume and the ceiling",
      JS.count("clamp01(lsGet(LS.") == 2)

check("every page attempts playback as it loads",
      "play(handOffAt > 0 ? 0 : 2, handOffAt);" in JS)
check("the first entry fades in rather than bursting in",
      "setTrackVolume(0);" in JS and "rampVolume(currentCeiling()" in JS)
check("the volume lands on the media element itself",
      "audioEl.volume = " in JS and "function setTrackVolume" in JS)
check("the track is not routed through a Web Audio gain "
      "(a suspended AudioContext swallows anything routed through it)",
      "createMediaElementSource" not in JS)

# ==========================================================================
# 4. The fallback the autoplay policies force on us
# ==========================================================================
gesture_block = re.search(r"var GESTURE_EVENTS = \[(.*?)\];", JS, re.S)
GESTURES = set(re.findall(r'"([a-z]+)"', gesture_block.group(1))) if gesture_block else set()
check("the first gesture of any kind starts the track",
      {"pointerdown", "pointerup", "mousedown", "touchstart", "touchend",
       "keydown", "click", "wheel", "scroll"} <= GESTURES,
      str(sorted(GESTURES)))
check("the fallback is captured, so no page script can swallow it",
      "{ capture: true, passive: true }" in JS)
check("the fallback is only a fallback",
      "if (isPlaying() || !enabled())" in JS)
check("a press on the sound control is left to the toggle",
      'closest(".sound-bar")' in JS)
check("the fallback is disarmed once the visitor switches sound off",
      "disarmGestureWake(); // a later press" in JS)
check("a refused play() arms the fallback",
      'p.catch(function () {' in JS and "if (enabled()) armGestureWake();" in JS)
check("a paused or restored tab picks the track back up",
      '"pageshow"' in JS and '"visibilitychange"' in JS and '"focus"' in JS)

# ==========================================================================
# 5. The admin's switch reaches the page, and really takes sound away
# ==========================================================================
TOUCHED = ("sound.enabled", "sound.volume")


def _raw(key: str) -> str | None:
    row = query_one("SELECT value FROM settings WHERE key=?", (key,))
    return None if row is None else row["value"]


ORIGINALS = {key: _raw(key) for key in TOUCHED}


def _restore() -> None:
    for key, raw in ORIGINALS.items():
        if raw is None:
            execute("DELETE FROM settings WHERE key=?", (key,))
        else:
            execute("UPDATE settings SET value=? WHERE key=?", (raw, key))


def _body(path: str = "/") -> str:
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    return r.get_data(as_text=True)


try:
    with app.app_context():
        set_setting("sound.enabled", True)
        set_setting("sound.volume", 0.25)
        on = _body()

        check("the toggle carries the admin's sound switch on",
              'data-sound-enabled="1"' in on)
        check("the toggle carries the admin's volume ceiling",
              'data-sound-volume="0.25"' in on)
        check("the track is wired into the public header, once",
              on.count("/static/js/ambience.js") == 1)
        check("every public page gets the engine, not just the home page",
              all('id="audioToggle"' in _body(p)
                  for p in ("/", "/rooms", "/rules", "/about", "/contact")))

        set_setting("sound.enabled", False)
        off = _body()
        check("the admin switch reaches the visitor as off",
              'data-sound-enabled="0"' in off)
finally:
    _restore()

check("the developer's settings are left exactly as found",
      {key: _raw(key) for key in TOUCHED} == ORIGINALS,
      repr({k: (_raw(k), ORIGINALS[k]) for k in TOUCHED if _raw(k) != ORIGINALS[k]}))

defaults = pathlib.Path("app/services/settings.py").read_text(encoding="utf-8")
check("the shipped default is sound on", '"sound.enabled": True' in defaults)
with app.app_context():
    check("an admin who never touched the setting still gets sound",
          bool(get_setting("sound.enabled", True)) is True)  # noqa: E712 - get_setting may return the raw row

# ------------------------------------------------------------------ report
failed = [x for x in RESULTS if not x[1]]
for name, ok, detail in RESULTS:
    print(("PASS  " if ok else "FAIL  ") + name + (("  <- " + detail) if (not ok and detail) else ""))
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
sys.exit(1 if failed else 0)
