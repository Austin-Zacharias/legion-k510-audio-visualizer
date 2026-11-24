"""
Read-only diagnostic: finds the HID interfaces of your Lenovo keyboard and
checks whether it exposes the standard HID "Lighting and Illumination"
(LampArray) interface, which uses usage page 0x59.

It does NOT change any lighting. Output is printed and also saved to
hid_probe_output.txt so you can paste it back.

Setup:   py -3.12 -m pip install hidapi
Run:     py -3.12 hid_probe.py
"""

import sys

try:
    import hid
except ImportError:
    print("Missing package. Run:  py -3.12 -m pip install hidapi")
    sys.exit(1)

LENOVO_VID = 0x17EF
lines = []


def out(s=""):
    print(s)
    lines.append(s)


def hexdump(data):
    return " ".join(f"{b:02X}" for b in data)


def looks_like_target(d):
    name = ((d.get("product_string") or "") + " " + (d.get("manufacturer_string") or "")).lower()
    return (
        d["vendor_id"] == LENOVO_VID
        or "legion" in name
        or "k510" in name
        or d.get("usage_page") == 0x59
    )


def main():
    devices = [d for d in hid.enumerate(0, 0) if looks_like_target(d)]
    if not devices:
        out("No matching HID devices found.")
        return

    out(f"Found {len(devices)} matching HID interface(s):\n")
    for i, d in enumerate(devices):
        out(
            f"[{i}] {d.get('product_string')!r}  "
            f"VID={d['vendor_id']:04X} PID={d['product_id']:04X}  "
            f"usage_page=0x{d['usage_page']:04X} usage=0x{d['usage']:04X}  "
            f"interface={d.get('interface_number')}"
        )
        out(f"     path={d['path']!r}")

    lamp = [d for d in devices if d["usage_page"] == 0x59]
    out()
    if not lamp:
        out("RESULT: no interface with usage page 0x59 (Lighting and Illumination) was visible.")
        out("Windows may be hiding it, or the keyboard exposes lighting another way.")
    else:
        out(f"RESULT: found {len(lamp)} lighting (usage page 0x59) interface(s).")

    # Probe feature reports 1-8 on lighting interfaces (read-only).
    for d in lamp:
        out(f"\nProbing feature reports on {d.get('product_string')!r} (usage 0x{d['usage']:04X}):")
        h = hid.device()
        try:
            h.open_path(d["path"])
        except Exception as e:
            out(f"  Could not open: {e}")
            continue
        for rid in range(1, 9):
            try:
                data = h.get_feature_report(rid, 64)
                out(f"  report {rid}: {hexdump(data)}")
            except Exception as e:
                out(f"  report {rid}: not readable ({e})")
        h.close()


if __name__ == "__main__":
    main()
    with open("hid_probe_output.txt", "w") as f:
        f.write("\n".join(lines))
    print("\nSaved to hid_probe_output.txt")
