#!/usr/bin/env python3
"""PinFinder agent client with a boot delay for the ESP32 bridge.

The ESP32-S3 native USB restarts the chip when the COM port opens, so the
first bytes sent immediately after open are lost. This wrapper opens the port,
waits, then talks to PinFinder.

    python scripts/pf.py info
    python scripts/pf.py "detect hall"
    python scripts/pf.py --port COM5 --boot-delay 3 info
    python scripts/pf.py --export board.json

All PinFinder commands are passed through unchanged (see
vendor/ALIFE-MM32/tools/pinfinder.py).
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "vendor", "ALIFE-MM32", "tools")
sys.path.insert(0, os.path.abspath(TOOLS))

from pinfinder import PinFinder, export, import_, WARNING  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("commands", nargs="*", help="PinFinder commands (quote commands with arguments)")
    ap.add_argument("--export", metavar="FILE", help="after the commands: config + info to a JSON file")
    ap.add_argument("--import", dest="imp", metavar="FILE", help="before the commands: set the words of a JSON export")
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=90.0, help="seconds per command")
    ap.add_argument("--boot-delay", type=float, default=2.0,
                    help="seconds to wait after opening the port (ESP32 native USB resets)")
    ap.add_argument("--json", action="store_true", help="one JSON object per command")
    ap.add_argument("-v", "--verbose", action="store_true", help="raw lines to stderr")
    a = ap.parse_args()
    if not (a.commands or a.export or a.imp):
        ap.error("no command (or --export / --import)")

    print(WARNING, file=sys.stderr)
    pf = PinFinder(a.port, a.baud, a.verbose)
    if a.boot_delay > 0:
        time.sleep(a.boot_delay)
    failed = False
    try:
        if a.imp:
            failed |= not import_(pf, a.imp, a.timeout)
        for c in a.commands:
            good, res, lines = pf.cmd(c, a.timeout)
            failed |= not good
            if a.json:
                import json
                print(json.dumps({"command": c, "ok": good, "results": res, "lines": lines}))
            else:
                for ln in lines:
                    print(ln)
        if a.export:
            export(pf, a.export, a.timeout)
    finally:
        pf.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
