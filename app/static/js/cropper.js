/* ==========================================================================
   MiniCropper — a small, dependency-free image cropper (CSP-safe).
   Drag to pan, wheel/pinch to zoom, aspect presets keep the frame locked.
   The crop rect is always clamped inside the image and reported in NATURAL
   image pixels, so the server-side Pillow crop matches exactly what the
   admin framed — at full source resolution, never the preview's.
   ========================================================================== */
(function () {
  "use strict";

  function MiniCropper(imgEl, opts) {
    this.img = imgEl;
    this.opts = opts || {};
    this.scale = 1;
    this.minScale = 0.05;
    this.ox = 0; // image centre offset inside viewport
    this.oy = 0;
    this.dragging = false;
    this.lastX = 0;
    this.lastY = 0;
    this.pinchDist = 0;
    this._bind();
    this._layout();
  }

  MiniCropper.prototype._bind = function () {
    var self = this;
    var v = this.opts.viewport;
    v.style.touchAction = "none";

    v.addEventListener("pointerdown", function (e) {
      self.dragging = true;
      self.lastX = e.clientX;
      self.lastY = e.clientY;
      v.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    v.addEventListener("pointermove", function (e) {
      if (!self.dragging) return;
      self.ox += e.clientX - self.lastX;
      self.oy += e.clientY - self.lastY;
      self.lastX = e.clientX;
      self.lastY = e.clientY;
      self._layout();
    });
    v.addEventListener("pointerup", function (e) {
      self.dragging = false;
      try { v.releasePointerCapture(e.pointerId); } catch (err) {}
    });
    v.addEventListener("wheel", function (e) {
      e.preventDefault();
      var factor = e.deltaY < 0 ? 1.08 : 1 / 1.08;
      self._zoom(factor);
    }, { passive: false });

    v.addEventListener("touchstart", function (e) {
      if (e.touches.length === 2) {
        self.pinchDist = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
      }
    }, { passive: true });
    v.addEventListener("touchmove", function (e) {
      if (e.touches.length === 2 && self.pinchDist > 0) {
        e.preventDefault();
        var d = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
        self._zoom(d / self.pinchDist);
        self.pinchDist = d;
      }
    }, { passive: false });
    v.addEventListener("touchend", function () { self.pinchDist = 0; });

    window.addEventListener("resize", function () { self.fitFrame(); });
  };

  MiniCropper.prototype._zoom = function (factor) {
    this.scale = Math.max(this.minScale, Math.min(12, this.scale * factor));
    this._layout();
  };

  MiniCropper.prototype._layout = function () {
    var frame = this.opts.frame;      // the visible crop frame
    var nat = this.natural || {};
    var nw = nat.width || this.img.naturalWidth || 1;
    var nh = nat.height || this.img.naturalHeight || 1;
    var fw = frame.offsetWidth, fh = frame.offsetHeight;
    if (!fw || !fh) return;

    var scale = Math.max(fw / nw, fh / nh);   // cover the frame
    scale *= this.scale;
    var w = nw * scale, h = nh * scale;

    // Clamp translation so the frame is always fully covered by the image.
    var maxX = (w - fw) / 2, maxY = (h - fh) / 2;
    this.ox = Math.max(-maxX, Math.min(maxX, this.ox));
    this.oy = Math.max(-maxY, Math.min(maxY, this.oy));

    this.img.style.width = w + "px";
    this.img.style.height = h + "px";
    this.img.style.left = (fw / 2 + this.ox - w / 2) + "px";
    this.img.style.top = (fh / 2 + this.oy - h / 2) + "px";
  };

  /** Re-fit after the frame size changes (tab switch, viewport resize). */
  MiniCropper.prototype.fitFrame = function () {
    this._layout();
  };

  /** Replace the source image and reset the view. */
  MiniCropper.prototype.setImage = function (src) {
    var self = this;
    this.img.onload = function () {
      self.natural = { width: self.img.naturalWidth, height: self.img.naturalHeight };
      self.scale = 1;
      self.ox = 0;
      self.oy = 0;
      // Initial scale: smallest zoom that covers the frame.
      var frame = self.opts.frame;
      var nw = self.natural.width, nh = self.natural.height;
      var need = Math.max(frame.offsetWidth / nw, frame.offsetHeight / nh);
      var base = Math.max(frame.offsetWidth / nw, frame.offsetHeight / nh);
      self.scale = 1; // _layout computes cover scale itself; this.zoom is multiplier
      self.zoomMultiplier = 1;
      self._layout();
      if (self.opts.onReady) self.opts.onReady(self.natural.width, self.natural.height);
    };
    this.img.src = src;
  };

  /**
   * Return the crop rect in NATURAL image pixels:
   * {x, y, w, h, displayW, displayH}
   */
  MiniCropper.prototype.getCrop = function () {
    var nat = this.natural || {
      width: this.img.naturalWidth,
      height: this.img.naturalHeight,
    };
    var frame = this.opts.frame;
    var fw = frame.offsetWidth, fh = frame.offsetHeight;
    var nw = nat.width, nh = nat.height;
    var cover = Math.max(fw / nw, fh / nh);        // CSS px per image px at zoom 1
    var pxPerImg = cover * this.scale;
    // Top-left of the image inside the frame (CSS px), may be negative.
    var imgLeft = fw / 2 + this.ox - (nw * pxPerImg) / 2;
    var imgTop = fh / 2 + this.oy - (nh * pxPerImg) / 2;
    var x = -imgLeft / pxPerImg;
    var y = -imgTop / pxPerImg;
    var w = fw / pxPerImg;
    var h = fh / pxPerImg;
    // Clamp inside the image.
    x = Math.max(0, Math.min(x, nw - 1));
    y = Math.max(0, Math.min(y, nh - 1));
    w = Math.max(1, Math.min(w, nw - x));
    h = Math.max(1, Math.min(h, nh - y));
    return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) };
  };

  window.MiniCropper = MiniCropper;
})();
