# K510 Audio Visualizer

Audio-reactive RGB lighting for the **Lenovo Legion K510 Mini Pro** keyboard on Windows.
It listens to whatever your PC is playing and drives all 84 keys individually: bass
flashes, beat-shifting colors, and four effect modes you can switch on the fly.

It talks to the keyboard directly over HID, so it does not need Lenovo's software,
and it works with music from any app (Spotify, YouTube, games, local files).

## Modes

| Mode | What it does |
|---|---|
| `bars` | Spectrum bars, bass on the left and treble on the right, plus a whole-keyboard flash on bass hits |
| `ripples` | Each beat sends a ring of color expanding from a random key |
| `sparkles` | A calm glow that breathes with the volume, with keys twinkling on treble |
| `aurora` | Slow flowing color waves that move faster and brighter as the music gets louder |

## Setup

Requires Windows 10/11 and Python 3.12 (the version I tested with).

```
py -3.12 -m pip install -r requirements.txt
```

Before running:

1. In **Settings > Personalization > Dynamic lighting**, turn **off** "Use Dynamic Lighting on my devices" so Windows doesn't fight the script.
2. Close Lenovo's keyboard software and any other RGB app (for example LIGHTLINK).

## Usage

```
py -3.12 k510_visualizer.py --test            # solid red, green, blue: checks that control works
py -3.12 k510_visualizer.py                   # bars mode
py -3.12 k510_visualizer.py --mode ripples    # or sparkles, aurora
py -3.12 k510_visualizer.py --flip-y          # if the bars grow from the top
```

Press **Ctrl+C** to stop. The keyboard goes back to its normal lighting.

### Hotkeys while running (work from any window)

| Keys | Action |
|---|---|
| Ctrl+Alt+Right / Left | next / previous mode |
| Ctrl+Alt+1 / 2 / 3 / 4 | bars / ripples / sparkles / aurora |

## Tweaking the look

The settings are at the top of `k510_visualizer.py`: bar height (`BAR_SCALE`), how
calm or snappy the motion is (`ATTACK`, `DECAY`), beat sensitivity (`BEAT_RATIO`),
flash strength (`FLASH_STRENGTH`, `FLASH_DECAY`), color shift per beat (`HUE_STEP`),
idle brightness (`IDLE_GLOW`), and per-mode values for ripples, sparkles and aurora.

## Troubleshooting

- **Nothing happens:** make sure Dynamic Lighting is off and no other RGB software is running. Try `--test` first.
- **Bars grow from the top:** add `--flip-y`.
- **No reaction to audio:** the script listens to your default output device. If you switch to headphones or another device, restart it.
- **Hotkeys do nothing:** try running the terminal as Administrator once. The startup message says whether hotkeys are active.

## Compatibility

Tested only on the Lenovo Legion K510 Mini Pro (USB `17EF:619A`) on Windows. Other keyboards
that use the standard HID lighting interface (usage page `0x59`) might work, but this
keyboard expects a specific message layout (see `docs/FINDINGS.md`), and other models may
differ. If you want to try another keyboard, run `tools/hid_probe.py` and
`tools/hid_descriptor.py` and open an issue with the output.

## How it works

- Audio comes from WASAPI loopback (`pyaudiowpatch`) and is turned into 24 log-spaced frequency bands with an FFT.
- A bass-band beat detector drives the flash, the color shift and the ripples.
- Lighting goes out as HID feature reports to the keyboard's lighting interface, 8 lamps per report, at about 30 frames per second.

The full story of how this was figured out, including a Windows Dynamic Lighting dead end
and a descriptor bug that produced random white keys, is in `docs/FINDINGS.md`,
`experiments/dynamic_lighting/NOTES.md` and the commit history.

## About the history

I built this together with Claude (Anthropic) as a debugging exercise. The commit history
was reconstructed afterwards from my development log so the repo reads as the story of how
it got working. The commit dates are the date the repo was created, not the original
sessions.

## Disclaimer

This sends raw lighting reports to your keyboard. As far as I can tell it only uses the
standard lighting-control reports (no firmware or storage writes), but it comes with no
warranty. Not affiliated with or endorsed by Lenovo.

## License

MIT, see `LICENSE`.
