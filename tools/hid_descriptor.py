"""
Read-only: dumps the lighting interface's HID report descriptor (if available)
and the report sizes Windows reports for it. Does not change any lighting.

Run:  py -3.12 hid_descriptor.py
Output is printed and saved to hid_descriptor_output.txt
"""

import ctypes
import sys
from ctypes import wintypes

import hid

lines = []


def out(s=""):
    print(s)
    lines.append(s)


def hexdump(data, per_line=16):
    data = bytes(data)
    for i in range(0, len(data), per_line):
        out("  " + " ".join(f"{b:02X}" for b in data[i:i + per_line]))


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


def windows_caps(path):
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    hidd = ctypes.WinDLL("hid", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    handle = k32.CreateFileW(path, 0, 3, None, 3, 0, None)  # no access needed, share read/write
    if handle in (None, wintypes.HANDLE(-1).value, 0xFFFFFFFFFFFFFFFF):
        out(f"  CreateFile failed (error {ctypes.get_last_error()})")
        return
    preparsed = ctypes.c_void_p()
    hidd.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    if not hidd.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
        out("  HidD_GetPreparsedData failed")
        k32.CloseHandle(handle)
        return
    caps = HIDP_CAPS()
    hidd.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
    hidd.HidP_GetCaps(preparsed, ctypes.byref(caps))
    for name, _ in HIDP_CAPS._fields_:
        if name != "Reserved":
            out(f"  {name}: {getattr(caps, name)}")
    hidd.HidD_FreePreparsedData(preparsed)
    k32.CloseHandle(handle)


def main():
    cands = [d for d in hid.enumerate(0, 0) if d["usage_page"] == 0x59 and d["usage"] == 0x01]
    if not cands:
        out("No lighting interface found.")
        return
    d = cands[0]
    out(f"Device: {d.get('product_string')}  VID={d['vendor_id']:04X} PID={d['product_id']:04X}")

    out("\nWindows report sizes (byte lengths include the report ID):")
    try:
        windows_caps(d["path"].decode())
    except Exception as e:
        out(f"  could not read: {e}")

    out("\nReport descriptor:")
    h = hid.device()
    h.open_path(d["path"])
    try:
        desc = h.get_report_descriptor()
        out(f"  ({len(desc)} bytes)")
        hexdump(desc)
    except AttributeError:
        out("  This hidapi version can't read the descriptor.")
    except Exception as e:
        out(f"  Could not read descriptor: {e}")
    h.close()


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Run this on Windows.")
        sys.exit(1)
    main()
    with open("hid_descriptor_output.txt", "w") as f:
        f.write("\n".join(lines))
    print("\nSaved to hid_descriptor_output.txt")
