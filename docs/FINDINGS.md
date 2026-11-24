# Findings

## 1. The keyboard exposes a standard HID lighting interface

`tools/hid_probe.py` lists the keyboard's HID interfaces. One of them uses usage
page `0x59` (Lighting and Illumination), usage `0x01` (LampArray), on USB
interface 2, and it can be opened by a normal user-mode program.

Reading its feature reports showed:

| Report | Meaning | What the K510 returned |
|---|---|---|
| 1 | LampArray attributes | 84 lamps, keyboard type, minimum update interval about 33 ms (roughly 30 updates per second) |
| 3 | Lamp attributes | position per lamp in micrometres, 255 levels per color channel, programmable |

So per-key control should be possible without going through Windows Dynamic Lighting.
