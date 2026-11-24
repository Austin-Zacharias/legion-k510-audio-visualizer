"""
Audio-reactive lighting for a HID LampArray keyboard (Lenovo Legion K510 Mini Pro),
talking to the keyboard directly over HID instead of through Windows Dynamic Lighting.

Setup:
    py -3.12 -m pip install hidapi numpy pyaudiowpatch

Before running:
    - In Settings > Personalization > Dynamic lighting, turn OFF
      "Use Dynamic Lighting on my devices" (so Windows doesn't fight the script)
    - Close LIGHTLINK and Lenovo's keyboard software

Run:
    py -3.12 k510_visualizer.py --test       # solid red, green, blue (checks control works)
    py -3.12 k510_visualizer.py              # audio visualizer
    py -3.12 k510_visualizer.py --flip-y     # if the bars grow from the top
    (--feature is no longer needed; updates always use feature reports)
    --complete-each                         # mark every report as a finished frame
    --delay 2                               # wait 2 ms between reports

Press Ctrl+C to stop (the keyboard returns to its normal lighting).
"""

import argparse
import colorsys
import struct
import sys
import threading
import time

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
ATTACK = 0.55         # how fast bars rise (0-1, lower = calmer)
DECAY = 0.90          # how slowly bars fall (closer to 1 = calmer)
BEAT_RATIO = 1.4      # bass must exceed its recent average by this factor to count as a beat
FLASH_STRENGTH = 0.55 # brightness of the whole-keyboard bass flash (0-1)
FLASH_DECAY = 0.88    # how quickly the flash fades (closer to 1 = longer)
HUE_STEP = 0.07       # how far the color shifts on each beat (0-1 around the color wheel)
HUE_DRIFT = 0.002     # slow constant color drift per frame
HUE_SPREAD = 0.25     # how much the color varies left to right
IDLE_GLOW = 0.03      # faint glow on idle keys (0 = fully off)


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
    tilt = np.linspace(1.0, 4.0, N_BANDS)

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


def run_audio(kb, pos, flip_y):
    ids = sorted(pos)
    xs = np.array([pos[i][0] for i in ids], dtype=float)
    ys = np.array([pos[i][1] for i in ids], dtype=float)
    xn = (xs - xs.min()) / max(xs.max() - xs.min(), 1.0)
    yn = (ys - ys.min()) / max(ys.max() - ys.min(), 1.0)
    height = yn if flip_y else 1.0 - yn
    band_idx = np.minimum((xn * N_BANDS).astype(int), N_BANDS - 1)

    cap = LoopbackCapture()
    band_levels = make_band_fn(cap.rate)
    peak = 1e-3
    smooth = np.zeros(N_BANDS)
    bass_avg = 0.0
    hue = 0.0
    flash = 0.0
    last_beat = 0.0
    frame = max(kb.min_interval, 1 / 30)
    print("Running. Press Ctrl+C to stop.")

    try:
        while True:
            t0 = time.time()
            lv = band_levels(cap.snapshot())
            peak = max(peak * 0.995, lv.max(), 1e-3)
            norm = np.sqrt(np.clip(lv / peak, 0, 1))
            smooth = np.where(norm > smooth, smooth + (norm - smooth) * ATTACK, smooth * DECAY)

            # Beat detection on the bass bands
            bass = float(lv[:4].mean())
            if bass > bass_avg * BEAT_RATIO and bass > 0.2 * peak and t0 - last_beat > 0.18:
                hue = (hue + HUE_STEP) % 1.0
                flash = 1.0
                last_beat = t0
            bass_avg = bass_avg * 0.93 + bass * 0.07
            flash *= FLASH_DECAY
            hue = (hue + HUE_DRIFT) % 1.0

            level = smooth[band_idx]
            bars = np.clip((level - height) * 4.0, 0.0, 1.0)
            value = np.clip(np.maximum(bars, flash * FLASH_STRENGTH), IDLE_GLOW, 1.0)
            sat = 1.0 - 0.35 * flash  # flash goes slightly whiter

            items = []
            for lamp_id, x, v in zip(ids, xn, value):
                r, g, bl = colorsys.hsv_to_rgb((hue + x * HUE_SPREAD) % 1.0, sat, float(v))
                items.append((lamp_id, int(r * 255), int(g * 255), int(bl * 255)))
            kb.update(items)

            time.sleep(max(0.0, frame - (time.time() - t0)))
    finally:
        cap.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--flip-y", action="store_true")
    ap.add_argument("--feature", action="store_true", help="send updates as feature reports")
    ap.add_argument("--complete-each", action="store_true", help="flag every report as a complete update")
    ap.add_argument("--delay", type=float, default=0.0, help="milliseconds to wait between reports")
    args = ap.parse_args()

    kb = Keyboard(use_feature=args.feature, complete_each=args.complete_each, delay_ms=args.delay)
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
            run_audio(kb, pos, args.flip_y)
    except KeyboardInterrupt:
        pass
    finally:
        kb.close()


if __name__ == "__main__":
    main()
