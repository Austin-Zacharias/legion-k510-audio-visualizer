"""
Audio-reactive lighting for a HID LampArray keyboard (Lenovo Legion K510 Mini Pro),
talking to the keyboard directly over HID instead of through Windows Dynamic Lighting.

Setup:
    py -3.12 -m pip install hidapi numpy pyaudiowpatch keyboard

Before running:
    - In Settings > Personalization > Dynamic lighting, turn OFF
      "Use Dynamic Lighting on my devices" (so Windows doesn't fight the script)
    - Close LIGHTLINK and Lenovo's keyboard software

Run:
    py -3.12 k510_visualizer.py --test       # solid red, green, blue (checks control works)
    py -3.12 k510_visualizer.py              # audio visualizer (bars mode)
    py -3.12 k510_visualizer.py --mode ripples   # rings expand from random keys on each beat
    py -3.12 k510_visualizer.py --mode sparkles  # calm glow with treble-driven twinkles
    py -3.12 k510_visualizer.py --mode aurora    # slow flowing color waves that follow volume
    py -3.12 k510_visualizer.py --flip-y     # if the bars grow from the top
    (--feature is no longer needed; updates always use feature reports)
    --complete-each                         # mark every report as a finished frame
    --delay 2                               # wait 2 ms between reports

Hotkeys while it's running (work from any window):
    Ctrl+Alt+Right / Ctrl+Alt+Left   next / previous mode
    Ctrl+Alt+1 / 2 / 3 / 4           bars / ripples / sparkles / aurora

Press Ctrl+C to stop (the keyboard returns to its normal lighting).
"""

import argparse
import colorsys
import random
import struct
import sys
import threading
import time
import types

import numpy as np

try:
    import hid
except ImportError:
    print("Missing package. Run:  py -3.12 -m pip install hidapi")
    sys.exit(1)

import pyaudiowpatch as pyaudio

# Standard HID LampArray report IDs (confirmed by the probe output)
REPORT_ATTRIBUTES = 1
REPORT_REQUEST = 2
REPORT_RESPONSE = 3
REPORT_MULTI_UPDATE = 4
REPORT_CONTROL = 6

LENOVO_VID = 0x17EF
N_BANDS = 24
FMIN, FMAX = 40, 14000
BUF_SIZE = 4096

# ---- Look & feel (tweak these) ----
ATTACK = 0.60         # how fast bars rise (0-1, lower = calmer)
DECAY = 0.88          # how slowly bars fall (closer to 1 = calmer)
BEAT_RATIO = 1.22     # bass multiplier over average to count as a beat
FLASH_STRENGTH = 0.95 # brightness of the whole-keyboard bass flash (0-1)
FLASH_DECAY = 0.78    # how quickly the flash fades (closer to 1 = longer)
HUE_STEP = 0.07       # how far the color shifts on each beat (0-1 around the color wheel)
HUE_DRIFT = 0.002     # slow constant color drift per frame
HUE_SPREAD = 0.25     # how much the color varies left to right
IDLE_GLOW = 0.02      # faint glow on idle keys (0 = fully off)
BAR_SCALE = 0.95      # bar height (1.0 = full keyboard height reaches top row)

# ripples mode
RIPPLE_SPEED = 380    # how fast rings expand (mm per second)
RIPPLE_WIDTH = 35     # thickness of each ring (mm)
RIPPLE_LIFE = 1.6     # seconds before a ring fades out

# sparkles mode
SPARKLE_RATE = 7.0    # how many twinkles per frame at full treble
SPARKLE_DECAY = 0.84  # how quickly twinkles fade (closer to 1 = longer)

# aurora mode
AURORA_SPEED = 0.12   # base speed of the flowing waves


class Keyboard:
    def __init__(self, use_feature=False, complete_each=False, delay_ms=0.0):
        self.use_feature = use_feature
        self.complete_each = complete_each
        self.delay = delay_ms / 1000.0
        cands = [d for d in hid.enumerate(0, 0) if d["usage_page"] == 0x59 and d["usage"] == 0x01]
        if not cands:
            raise RuntimeError("No HID lighting interface (usage page 0x59) found.")
        cands.sort(key=lambda d: d["vendor_id"] != LENOVO_VID)
        d = cands[0]
        print(f"Opening: {d.get('product_string')} (VID {d['vendor_id']:04X} PID {d['product_id']:04X})")
        self.h = hid.device()
        self.h.open_path(d["path"])

        info = bytes(self.h.get_feature_report(REPORT_ATTRIBUTES, 64))
        self.lamp_count = struct.unpack_from("<H", info, 1)[0]
        interval_us = struct.unpack_from("<I", info, 19)[0]
        self.min_interval = max(interval_us / 1e6, 0.01)
        print(f"Lamps: {self.lamp_count}, min update interval: {self.min_interval * 1000:.1f} ms")

    def set_autonomous(self, on):
        self.h.send_feature_report(bytes([REPORT_CONTROL, 1 if on else 0]))

    def lamp_positions(self):
        """Returns {lamp_id: (x, y, z)} in micrometers."""
        pos = {}
        for lid in range(0, self.lamp_count + 8):
            try:
                self.h.send_feature_report(bytes([REPORT_REQUEST]) + struct.pack("<H", lid))
                r = bytes(self.h.get_feature_report(REPORT_RESPONSE, 64))
            except (OSError, IOError):
                continue
            if len(r) < 15:
                continue
            rid, x, y, z = struct.unpack_from("<HIII", r, 1)
            if rid == lid and rid not in pos:
                pos[rid] = (x, y, z)
            if len(pos) >= self.lamp_count:
                break
        return pos

    def update(self, items):
        """items: list of (lamp_id, r, g, b), values 0-255.
        This keyboard takes lighting updates as FEATURE report 4, 8 lamps per report,
        laid out as: id, count, flags, 8 x lamp id (u16), then 8 x (R, G, B, intensity)."""
        for i in range(0, len(items), 8):
            chunk = items[i:i + 8]
            n = len(chunk)
            last = (i + 8) >= len(items)
            pad = 8 - n
            ids = [c[0] for c in chunk] + [0] * pad
            rgbi = b"".join(bytes([c[1], c[2], c[3], 255]) for c in chunk) + bytes(4 * pad)
            flags = 1 if (last or self.complete_each) else 0
            buf = bytes([REPORT_MULTI_UPDATE, n, flags]) + struct.pack("<8H", *ids) + rgbi
            self.h.send_feature_report(buf)
            if self.delay:
                time.sleep(self.delay)

    def close(self):
        try:
            self.set_autonomous(True)
        except Exception:
            pass
        self.h.close()


class LoopbackCapture:
    def __init__(self):
        self.pa = pyaudio.PyAudio()
        wasapi = self.pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        dev = self.pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if not dev.get("isLoopbackDevice"):
            for lb in self.pa.get_loopback_device_info_generator():
                if dev["name"] in lb["name"]:
                    dev = lb
                    break
            else:
                raise RuntimeError("No loopback device found for the default output.")
        self.rate = int(dev["defaultSampleRate"])
        self.channels = int(dev["maxInputChannels"])
        self.buf = np.zeros(BUF_SIZE, dtype=np.float32)
        self.lock = threading.Lock()
        self.last_data = 0.0
        self.stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=dev["index"],
            frames_per_buffer=1024,
            stream_callback=self._callback,
        )
        print(f"Capturing audio from: {dev['name']} ({self.rate} Hz, {self.channels} ch)")

    def _callback(self, data, frames, time_info, status):
        x = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        x = x.reshape(-1, self.channels).mean(axis=1)
        with self.lock:
            self.buf = np.concatenate((self.buf, x))[-BUF_SIZE:]
            self.last_data = time.time()
        return (None, pyaudio.paContinue)

    def snapshot(self):
        with self.lock:
            if time.time() - self.last_data > 0.25:
                return np.zeros(BUF_SIZE, dtype=np.float32)
            return self.buf.copy()

    def close(self):
        self.stream.stop_stream()
        self.stream.close()
        self.pa.terminate()


def make_band_fn(rate):
    edges = np.geomspace(FMIN, FMAX, N_BANDS + 1)
    freqs = np.fft.rfftfreq(BUF_SIZE, 1.0 / rate)
    window = np.hanning(BUF_SIZE)
    # Balanced frequency weighting: ensure bass sub/kick frequencies are punchy
    tilt = np.linspace(1.8, 1.3, N_BANDS)

    def band_levels(samples):
        spec = np.abs(np.fft.rfft(samples * window))
        out = np.zeros(N_BANDS)
        for i in range(N_BANDS):
            m = (freqs >= edges[i]) & (freqs < edges[i + 1])
            if m.any():
                out[i] = spec[m].max()
            else:
                out[i] = spec[np.searchsorted(freqs, (edges[i] + edges[i + 1]) / 2)]
        return out * tilt

    return band_levels


def run_test(kb, ids):
    for name, rgb in (("red", (255, 0, 0)), ("green", (0, 255, 0)), ("blue", (0, 0, 255))):
        print("Showing", name)
        end = time.time() + 1.5
        while time.time() < end:
            kb.update([(i, *rgb) for i in ids])
            time.sleep(kb.min_interval)


def hsv_arr(h, s, v):
    """Vectorized HSV -> RGB. Inputs broadcast; returns an (n, 3) array of 0-1 floats."""
    h, s, v = np.broadcast_arrays(np.asarray(h, float), np.asarray(s, float), np.asarray(v, float))
    h6 = (h % 1.0) * 6.0
    i = np.floor(h6).astype(int) % 6
    f = h6 - np.floor(h6)
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=1)


class Analyzer:
    """Turns audio snapshots into smoothed band levels, beats, flash and a shifting hue."""

    def __init__(self, rate):
        self.band_levels = make_band_fn(rate)
        self.peaks = np.full(N_BANDS, 1e-3)
        self.smooth = np.zeros(N_BANDS)
        self.bass_avg = 0.0
        self.bass_peak = 1e-3
        self.bass_power = 0.0
        self.hue = 0.0
        self.flash = 0.0
        self.last_beat = 0.0
        self.vol = 0.0  # slow overall loudness, 0-1

    def step(self, samples, now):
        lv = self.band_levels(samples)
        # Per-band auto-gain: loud treble will never crush bass bands
        self.peaks = np.maximum(self.peaks * 0.994, lv)
        self.peaks = np.maximum(self.peaks, 1e-3)
        norm = np.clip(lv / self.peaks, 0.0, 1.0)
        norm = np.sqrt(norm)

        self.smooth = np.where(
            norm > self.smooth,
            self.smooth + (norm - self.smooth) * ATTACK,
            self.smooth * DECAY,
        )

        # Bass band: 40 Hz to ~200 Hz (first 7 bands)
        bass = float(lv[:7].mean())
        self.bass_peak = max(self.bass_peak * 0.994, bass, 1e-3)
        norm_bass = np.clip(bass / self.bass_peak, 0.0, 1.0)

        # Sustained bass drop energy
        self.bass_power = self.bass_power * 0.88 + (norm_bass ** 1.3) * 0.12

        beat = False
        if bass > self.bass_avg * BEAT_RATIO and norm_bass > 0.18 and now - self.last_beat > 0.15:
            beat = True
            self.hue = (self.hue + HUE_STEP) % 1.0
            self.flash = 1.0
            self.last_beat = now

        self.bass_avg = self.bass_avg * 0.92 + bass * 0.08
        self.flash *= FLASH_DECAY
        self.hue = (self.hue + HUE_DRIFT) % 1.0
        self.vol = self.vol * 0.95 + float(np.clip(self.smooth.mean() * 1.5, 0, 1)) * 0.05
        return beat


def render_bars(an, c, beat, now, dt):
    level = an.smooth[c.band_idx] * BAR_SCALE
    bars = np.clip((level - c.height) * 4.0, 0.0, 1.0)
    # Bass rumble boost on the left side during sustained drops
    bass_boost = np.where(c.xn < 0.38, an.bass_power * 0.45, 0.0)
    value = np.clip(np.maximum(bars + bass_boost, an.flash * FLASH_STRENGTH), IDLE_GLOW, 1.0)
    sat = 1.0 - 0.35 * an.flash  # flash goes slightly whiter
    return hsv_arr(an.hue + c.xn * HUE_SPREAD, sat, value)
    sat = 1.0 - 0.35 * an.flash  # flash goes slightly whiter
    return hsv_arr(an.hue + c.xn * HUE_SPREAD, sat, value)


def render_ripples(an, c, beat, now, dt):
    rings = c.state.setdefault("rings", [])
    if beat:
        k = random.randrange(c.n)
        rings.append((now, c.xmm[k], c.ymm[k], an.hue, 0.7 + 0.3 * an.flash))
        del rings[:-8]
    rings[:] = [r for r in rings if now - r[0] < RIPPLE_LIFE]

    rgb = np.tile(np.array(colorsys.hsv_to_rgb(an.hue, 1.0, IDLE_GLOW)), (c.n, 1))
    for t0, ox, oy, hue, amp in rings:
        age = now - t0
        radius = RIPPLE_SPEED * age
        dist = np.hypot(c.xmm - ox, c.ymm - oy)
        val = np.exp(-(((dist - radius) / RIPPLE_WIDTH) ** 2)) * (1.0 - age / RIPPLE_LIFE) * amp
        rgb += val[:, None] * np.array(colorsys.hsv_to_rgb(hue, 1.0, 1.0))
    return np.clip(rgb, 0.0, 1.0)


def render_sparkles(an, c, beat, now, dt):
    spark = c.state.setdefault("spark", np.zeros(c.n))
    spark *= SPARKLE_DECAY
    treble = float(an.smooth[-8:].mean())
    spawn = int(np.random.poisson(SPARKLE_RATE * treble ** 2))
    if beat:
        spawn += 3
    if spawn:
        spark[np.random.randint(0, c.n, spawn)] = 1.0

    base_v = max(IDLE_GLOW, 0.06 + 0.25 * an.vol + 0.2 * an.flash)
    base = hsv_arr(an.hue + c.xn * HUE_SPREAD, 0.9, base_v)
    glow = spark[:, None] * np.array([1.0, 0.95, 0.9])
    return np.clip(base + glow, 0.0, 1.0)


def render_aurora(an, c, beat, now, dt):
    st = c.state
    st["phase"] = st.get("phase", 0.0) + dt * (AURORA_SPEED + 0.5 * an.vol)
    p = st["phase"]
    tau = 2 * np.pi
    hue_base = 0.62 + 0.18 * np.sin(p * 0.35)
    hue = (
        hue_base
        + 0.10 * np.sin(tau * (c.xn * 0.8 + p * 0.4))
        + 0.06 * np.sin(tau * (c.yn * 1.1 - p * 0.3))
    )
    wave = 0.5 + 0.5 * np.sin(tau * (c.xn * 1.1 + p * 0.5) + 2.0 * np.sin(tau * c.yn * 0.7 + p * 1.3))
    loud = 0.35 + 0.65 * min(1.0, an.vol * 1.6)
    value = np.clip((0.08 + 0.92 * wave) * loud + an.flash * 0.25, IDLE_GLOW, 1.0)
    return hsv_arr(hue, 0.85, value)


MODES = {
    "bars": render_bars,
    "ripples": render_ripples,
    "sparkles": render_sparkles,
    "aurora": render_aurora,
}


def setup_hotkeys(state):
    """Registers global hotkeys that change state['mode']. Optional: needs the 'keyboard' package."""
    try:
        import keyboard as kbd
    except ImportError:
        print("Hotkeys disabled (run: py -3.12 -m pip install keyboard)")
        return None

    names = list(MODES)

    def step(delta):
        i = names.index(state["mode"])
        state["mode"] = names[(i + delta) % len(names)]

    def pick(name):
        state["mode"] = name

    try:
        kbd.add_hotkey("ctrl+alt+right", lambda: step(1))
        kbd.add_hotkey("ctrl+alt+left", lambda: step(-1))
        for i, name in enumerate(names[:9], start=1):
            kbd.add_hotkey(f"ctrl+alt+{i}", lambda n=name: pick(n))
    except Exception as e:
        print(f"Hotkeys disabled ({e})")
        return None
    print("Hotkeys: Ctrl+Alt+Right/Left = next/previous mode, Ctrl+Alt+1-4 = pick a mode")
    return kbd


def run_audio(kb, pos, flip_y, mode):
    ids = sorted(pos)
    xs = np.array([pos[i][0] for i in ids], dtype=float) / 1000.0  # mm
    ys = np.array([pos[i][1] for i in ids], dtype=float) / 1000.0
    xn = (xs - xs.min()) / max(xs.max() - xs.min(), 1e-6)
    yn = (ys - ys.min()) / max(ys.max() - ys.min(), 1e-6)
    height = yn if flip_y else 1.0 - yn
    band_idx = np.minimum((xn * N_BANDS).astype(int), N_BANDS - 1)
    c = types.SimpleNamespace(
        n=len(ids), xn=xn, yn=yn, xmm=xs, ymm=ys,
        height=height, band_idx=band_idx, state={},
    )

    state = {"mode": mode}
    current = mode
    kbd = setup_hotkeys(state)
    cap = LoopbackCapture()
    an = Analyzer(cap.rate)
    frame = max(kb.min_interval, 1 / 30)
    last = time.time()
    print(f"Mode: {mode}. Running. Press Ctrl+C to stop.")

    try:
        while True:
            t0 = time.time()
            dt = t0 - last
            last = t0
            if state["mode"] != current:
                current = state["mode"]
                c.state = {}  # reset the new mode's animation state
                print("Mode:", current)
            beat = an.step(cap.snapshot(), t0)
            rgb = np.clip(MODES[current](an, c, beat, t0, dt), 0.0, 1.0)
            vals = (rgb * 255).astype(int)
            items = [(lid, int(r), int(g), int(b)) for lid, (r, g, b) in zip(ids, vals)]
            kb.update(items)
            time.sleep(max(0.0, frame - (time.time() - t0)))
    finally:
        if kbd:
            try:
                kbd.unhook_all_hotkeys()
            except Exception:
                pass
        cap.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--mode", choices=list(MODES), default="bars")
    ap.add_argument("--flip-y", action="store_true")
    ap.add_argument("--feature", action="store_true", help=argparse.SUPPRESS)  # no longer needed
    ap.add_argument("--complete-each", action="store_true", help="flag every report as a complete update")
    ap.add_argument("--delay", type=float, default=0.0, help="milliseconds to wait between reports")
    args = ap.parse_args()

    kb = Keyboard(use_feature=True, complete_each=args.complete_each, delay_ms=args.delay)
    try:
        kb.set_autonomous(False)  # take control from the keyboard's built-in effects
        pos = kb.lamp_positions()
        print(f"Read positions for {len(pos)} lamps")
        if not pos:
            print("Could not read lamp positions.")
            return
        if args.test:
            run_test(kb, sorted(pos))
        else:
            run_audio(kb, pos, args.flip_y, args.mode)
    except KeyboardInterrupt:
        pass
    finally:
        kb.close()


if __name__ == "__main__":
    main()
