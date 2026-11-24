"""
Audio-reactive lighting for Windows Dynamic Lighting (HID LampArray) keyboards.

Captures whatever audio your PC is playing (WASAPI loopback), splits it into
frequency bands, and draws a spectrum visualizer across the keyboard:
bass on the left, treble on the right, louder = taller columns.

Setup (Windows 10/11, Python 3.10-3.12 recommended):
    pip install winsdk numpy pyaudiowpatch

Run:
    python audio_lamp.py              # picks the first Legion device, else device 0
    python audio_lamp.py --device 1   # choose a device by number
    python audio_lamp.py --flip-y     # if the effect is upside down (rows inverted)
    python audio_lamp.py --list       # just list lighting devices

Press Ctrl+C to stop.
"""

import argparse
import asyncio
import colorsys
import threading
import time

import numpy as np
import pyaudiowpatch as pyaudio
from winsdk.windows.devices.enumeration import DeviceInformation
from winsdk.windows.devices.lights import LampArray
from winsdk.windows.ui import Color

N_BANDS = 24          # number of frequency bands across the keyboard
FPS = 30              # lighting updates per second
FMIN, FMAX = 40, 14000
BUF_SIZE = 4096       # samples used for each FFT


def rgb(r, g, b):
    c = Color()
    c.a = 255
    c.r = int(max(0, min(255, r)))
    c.g = int(max(0, min(255, g)))
    c.b = int(max(0, min(255, b)))
    return c


class LoopbackCapture:
    """Captures system audio output via WASAPI loopback."""

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
                return np.zeros(BUF_SIZE, dtype=np.float32)  # silence
            return self.buf.copy()

    def close(self):
        self.stream.stop_stream()
        self.stream.close()
        self.pa.terminate()


def make_band_fn(rate):
    edges = np.geomspace(FMIN, FMAX, N_BANDS + 1)
    freqs = np.fft.rfftfreq(BUF_SIZE, 1.0 / rate)
    window = np.hanning(BUF_SIZE)
    tilt = np.linspace(1.0, 4.0, N_BANDS)  # treble carries less energy; boost it

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


async def pick_device(args):
    infos = await DeviceInformation.find_all_async(LampArray.get_device_selector(), [])
    if not infos:
        print("No Dynamic Lighting (LampArray) devices found.")
        return None
    for i, d in enumerate(infos):
        print(f"  [{i}] {d.name}")
    if args.list:
        return None

    idx = args.device
    if idx is None:
        idx = next((i for i, d in enumerate(infos) if "legion" in d.name.lower()), 0)
    print(f"Using device [{idx}]: {infos[idx].name}")
    return await LampArray.from_id_async(infos[idx].id)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", type=int, default=None)
    ap.add_argument("--flip-y", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    la = await pick_device(args)
    if la is None:
        return

    la.is_enabled = True

    # Lamp positions -> normalized x (frequency) and y (height)
    lamps = []
    for i in range(la.lamp_count):
        p = la.get_lamp_info(i).position
        lamps.append((i, p.x, p.y))
    xs = np.array([l[1] for l in lamps])
    ys = np.array([l[2] for l in lamps])
    xn = (xs - xs.min()) / max(xs.max() - xs.min(), 1e-6)
    yn = (ys - ys.min()) / max(ys.max() - ys.min(), 1e-6)
    # Height measured from the bottom row (assumes y grows downward)
    height = yn if args.flip_y else 1.0 - yn
    band_idx = np.minimum((xn * N_BANDS).astype(int), N_BANDS - 1)
    indices = [l[0] for l in lamps]
    hues = xn * 0.8  # red -> purple across the keyboard

    cap = LoopbackCapture()
    band_levels = make_band_fn(cap.rate)

    peak = 1e-3
    smooth = np.zeros(N_BANDS)
    last_avail = None
    print("Running. Press Ctrl+C to stop.")

    try:
        while True:
            t0 = time.time()

            state = (getattr(la, "is_connected", None), la.is_enabled)
            if state != last_avail:
                last_avail = state
                print("Keyboard connected / enabled:", state)

            lv = band_levels(cap.snapshot())
            peak = max(peak * 0.995, lv.max(), 1e-3)   # slow auto-gain
            norm = np.sqrt(np.clip(lv / peak, 0, 1))
            smooth = np.maximum(norm, smooth * 0.82)   # fast attack, slow fall

            level = smooth[band_idx]
            bright = np.clip((level - height) * 5.0, 0.0, 1.0)
            bright = np.maximum(bright, 0.03)          # faint glow when idle

            colors = []
            for h, b in zip(hues, bright):
                r, g, bl = colorsys.hsv_to_rgb(h, 1.0, b)
                colors.append(rgb(r * 255, g * 255, bl * 255))

            la.set_colors_for_indices(colors, indices)
            await asyncio.sleep(max(0.0, 1.0 / FPS - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        cap.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
