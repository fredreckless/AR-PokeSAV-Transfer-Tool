"""
ar_dump.py - read-only flash dumper for Datel Action Replay USB devices.

Based on the USB protocol reverse-engineered by blasty (2010) and
derrek/profi200 (ardsiflash.c, 2017) for the Action Replay DSi:
  VID 0x1C1A (Datel), PID 0x0100, bulk OUT 0x01 / IN 0x81,
  handshake 0x69, page read 0x72 + 32-bit little-endian offset,
  4 KB pages, 2 MB flash.

This script ONLY READS. It never sends the erase (0x65) or write (0x70)
commands, so it cannot change anything stored on the device.

Setup on Windows 11:
  1. pip install pyusb libusb-package
  2. Plug in the Action Replay, open Zadig (zadig.akeo.ie),
     Options > List All Devices, pick the Datel device, choose WinUSB,
     click "Install Driver".
  3. python ar_dump.py              (describes the device; dumps to
                                     ar_flash.bin if its protocol is known)
     python ar_dump.py --verify     (reads twice and compares)
     python ar_dump.py --list       (lists every USB device it can see)
"""

import argparse
import sys

try:
    import usb.core
    import usb.util
except ImportError:
    sys.exit("pyusb is missing. Run:  pip install pyusb libusb-package")

try:
    import libusb_package
    BACKEND = libusb_package.get_libusb1_backend()
except ImportError:
    BACKEND = None  # fall back to any libusb pyusb can find

# (vid, pid): (name, protocol_confirmed)
KNOWN_DEVICES = {
    (0x1C1A, 0x0100): ("Action Replay DSi", True),
    (0x05FD, 0xDAAE): ("Action Replay MAX DUO / GameShark (InterAct/Datel)", False),
}
DATEL_VIDS = {vid for vid, _ in KNOWN_DEVICES}
EP_OUT = 0x01
EP_IN = 0x81
PAGE_SIZE = 0x1000
FLASH_SIZE = 0x200000
TIMEOUT_MS = 5000


def list_devices():
    print("USB devices visible to libusb:")
    found = False
    for dev in usb.core.find(find_all=True, backend=BACKEND):
        found = True
        tag = "  <-- Datel" if dev.idVendor in DATEL_VIDS else ""
        print(f"  VID {dev.idVendor:04X}  PID {dev.idProduct:04X}{tag}")
    if not found:
        print("  (none - is libusb installed? try: pip install libusb-package)")


def open_device():
    devs = list(usb.core.find(find_all=True, backend=BACKEND,
                              custom_match=lambda d: d.idVendor in DATEL_VIDS))
    if not devs:
        print("No Datel device found.\n")
        list_devices()
        print("\nIf your Action Replay is listed under a different VID, or not at all,"
              "\ncheck Zadig has installed WinUSB for it and that it is in USB mode.")
        sys.exit(1)

    dev = devs[0]
    name, confirmed = KNOWN_DEVICES.get((dev.idVendor, dev.idProduct),
                                        ("unknown model", False))
    print(f"Found Datel device: VID {dev.idVendor:04X} PID {dev.idProduct:04X} - {name}")

    try:
        dev.set_configuration(1)
    except usb.core.USBError as e:
        print(f"Note: set_configuration said: {e} (often harmless)")
    usb.util.claim_interface(dev, 0)
    return dev, confirmed


def probe(dev):
    """Describe the device without sending it any commands."""
    print(f"  USB {dev.bcdUSB >> 8}.{(dev.bcdUSB >> 4) & 0xF}, "
          f"class {dev.bDeviceClass:02X}, max packet {dev.bMaxPacketSize0}")
    for attr, label in (("iManufacturer", "Manufacturer"), ("iProduct", "Product"),
                        ("iSerialNumber", "Serial")):
        idx = getattr(dev, attr)
        if idx:
            try:
                print(f"  {label}: {usb.util.get_string(dev, idx)}")
            except Exception as e:
                print(f"  {label}: (unreadable: {e})")
    for cfg in dev:
        print(f"  Configuration {cfg.bConfigurationValue}")
        for intf in cfg:
            print(f"    Interface {intf.bInterfaceNumber} alt {intf.bAlternateSetting}, "
                  f"class {intf.bInterfaceClass:02X}")
            for ep in intf:
                kind = ["control", "isochronous", "bulk", "interrupt"][ep.bmAttributes & 3]
                d = "IN " if ep.bEndpointAddress & 0x80 else "OUT"
                print(f"      Endpoint 0x{ep.bEndpointAddress:02X} {d} {kind}, "
                      f"max packet {ep.wMaxPacketSize}")


def handshake(dev):
    dev.write(EP_OUT, bytes([0x69, 0x00, 0x00, 0x00]), TIMEOUT_MS)
    resp = bytes(dev.read(EP_IN, 3, TIMEOUT_MS))
    print(f"Handshake reply: {resp.hex(' ')}")
    if len(resp) != 3:
        sys.exit("Unexpected handshake reply - stopping here to be safe.")


def read_page(dev, offset):
    cmd = bytes([0x72]) + offset.to_bytes(4, "little")
    dev.write(EP_OUT, cmd, TIMEOUT_MS)
    data = bytes(dev.read(EP_IN, PAGE_SIZE, TIMEOUT_MS))
    if len(data) != PAGE_SIZE:
        raise IOError(f"Short read at 0x{offset:06X}: {len(data)} bytes")
    return data


def read_flash(dev, size):
    out = bytearray()
    for offset in range(0, size, PAGE_SIZE):
        out += read_page(dev, offset)
        print(f"\rReading 0x{offset + PAGE_SIZE:06X} / 0x{size:06X}", end="", flush=True)
    print()
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(description="Read-only Action Replay flash dumper")
    ap.add_argument("output", nargs="?", default="ar_flash.bin")
    ap.add_argument("--size", type=lambda s: int(s, 0), default=FLASH_SIZE,
                    help="bytes to read (default 0x200000)")
    ap.add_argument("--verify", action="store_true",
                    help="read the flash twice and check both reads match")
    ap.add_argument("--list", action="store_true", help="list USB devices and exit")
    ap.add_argument("--try-dsi-protocol", action="store_true",
                    help="on a device whose protocol is unconfirmed, try the AR DSi"
                         " read commands anyway")
    args = ap.parse_args()

    if args.list:
        list_devices()
        return

    size = (args.size + PAGE_SIZE - 1) // PAGE_SIZE * PAGE_SIZE
    dev, confirmed = open_device()
    print("Device details:")
    probe(dev)
    if not confirmed and not args.try_dsi_protocol:
        print("\nThis model's protocol isn't confirmed yet, so I've only looked and"
              "\nsent it nothing. Please share the details above.")
        usb.util.release_interface(dev, 0)
        usb.util.dispose_resources(dev)
        return
    try:
        handshake(dev)
        data = read_flash(dev, size)
        if args.verify:
            print("Reading again to verify...")
            if read_flash(dev, size) != data:
                sys.exit("The two reads differ - the connection may be unreliable."
                         " Nothing was saved.")
            print("Both reads match.")
    finally:
        usb.util.release_interface(dev, 0)
        usb.util.dispose_resources(dev)

    with open(args.output, "wb") as f:
        f.write(data)
    blank = data.count(0xFF) / len(data) * 100
    print(f"Saved {len(data):,} bytes to {args.output} ({blank:.0f}% blank/erased).")


if __name__ == "__main__":
    main()
