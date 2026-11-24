# Dynamic Lighting experiment (dead end)

First idea: drive the keyboard through Windows Dynamic Lighting (the HID LampArray
API exposed to apps via WinRT), using the `winsdk` Python package.

## What I found

- The K510 shows up in Settings > Personalization > Dynamic lighting, so Windows
  can control it.
- LIGHTLINK (a Store app) can drive it, because it is registered as a background
  light controller and appears in the "Background light control" list.
- A plain Python script is not in that list. It connected to the keyboard, captured
  audio, and ran without errors, but the keyboard never changed. Turning
  "Compatible apps in the foreground always control lighting" on or off made no
  difference.

## Bugs on the way

- `find_all_async` needs a second argument or `winsdk` picks the wrong overload.
- `LampArray` has no `is_available` property here.
- `set_colors_for_indices` rejected a Python list of colors ("bytes-like object
  required"), so colors were set one lamp at a time instead.

## Conclusion

Getting control through the Windows API would need the script packaged as a proper
Windows app that declares the lighting extension. Instead, I skipped Windows and
talked to the keyboard's HID lighting interface directly. See `../../docs/FINDINGS.md`.
