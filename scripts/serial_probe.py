#!/usr/bin/env python3
"""Listen on a serial port and print what arrives (diagnostics for the ESP32 bridge).

    python scripts/serial_probe.py COM4 --baud 115200 --seconds 8
    python scripts/serial_probe.py COM4 --baud 19200 --seconds 8
    python scripts/serial_probe.py COM4 --baud 115200 --send "@info"

Opening the port may toggle DTR/RTS; both are dropped right after open so the
ESP32-S3 native USB does not see a reset request.
"""
import argparse
import sys
import time

import serial


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--send", default=None, help="send this line once after opening")
    a = ap.parse_args()

    try:
        s = serial.Serial(a.port, a.baud, timeout=0.1, dsrdtr=False, rtscts=False)
    except Exception as e:
        print("open %s failed: %s" % (a.port, e))
        return 1
    try:
        s.dtr = False
        s.rts = False
    except Exception:
        pass

    if a.send:
        s.write(a.send.encode() + b"\n")
        s.flush()
        print(">> %s" % a.send)

    t0 = time.time()
    total = 0
    while time.time() - t0 < a.seconds:
        b = s.read(256)
        if not b:
            continue
        total += len(b)
        print("%7.2fs  %3d B  %r" % (time.time() - t0, len(b), b))
        sys.stdout.flush()

    s.close()
    print("-- %d bytes in %.1f s at %d baud" % (total, a.seconds, a.baud))
    return 0


if __name__ == "__main__":
    sys.exit(main())
