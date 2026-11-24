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

## 2. First attempt: random white keys

My first script followed the usual HID LampArray layout from the spec: all the red
values, then all the green values, then blue, then intensity. Sent as feature
reports it got the keyboard to respond, but only random keys lit up, and only in
white. Marking every update as "complete" and adding small delays between reports
changed nothing, so timing was not the problem.

## 3. The report descriptor shows the real layout

`tools/hid_descriptor.py` dumps the interface's report descriptor
(`docs/k510_report_descriptor.txt`). What it says:

- Every lighting report is a *feature* report. There are no output reports.
- Report 4 (multi-lamp update) is: lamp count (1 byte), flags (1 byte), 8 lamp IDs
  (16-bit each), then 8 x (red, green, blue, intensity) **interleaved per lamp**.
- Reports 1-3 and 6 match the standard (attributes, request, response, control).

My script had grouped the channels, so the keyboard read scrambled data and lit
whichever lamps happened to receive bytes that looked like full white. Interleaving
the channels per lamp fixed it: solid red, green and blue test frames came out right
on all 84 keys.
