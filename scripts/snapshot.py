#!/usr/bin/env python3
"""Snapshot / restore the whole main-firmware config page (words 0..115).

    python scripts/snapshot.py --save                 # -> config_snapshot.json
    python scripts/snapshot.py --save my.json
    python scripts/snapshot.py --diff config_snapshot.json
    python scripts/snapshot.py --restore config_snapshot.json            # RAM only
    python scripts/snapshot.py --restore config_snapshot.json --flash    # + flash page

Reads every `hoverboard.config.*` word with rw access, so an erased or
reflashed board can be brought back in one command. The board must be
disarmed.

These are the raw config page words (the same page PinFinder writes), not the
ODrive-unit views: restoring them does not depend on unit conversions.

`baud` and `slaveid` are written last: they change how the board answers.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "vendor", "ALIFE-MM32", "tools")
sys.path.insert(0, os.path.abspath(TOOLS))

from cansimple_uart import Bus, AXIS_STATE  # noqa: E402

LINK_WORDS = ("hoverboard.config.baud", "hoverboard.config.slaveid")


def show(tag, value):
    print("%-12s %s" % (tag, value))


def config_words(bus):
    """name -> endpoint id, only the rw words of the config page"""
    return {n: e["id"] for n, e in bus.endpoints.items()
            if n.startswith("hoverboard.config.") and e.get("access") == "rw"}


def read_all(bus, names):
    out, missing = {}, []
    for name in sorted(names, key=names.get):
        v = bus.read(name)
        if v is None:
            missing.append(name)
        else:
            out[name] = v
    return out, missing


def short(name):
    return name.replace("hoverboard.config.", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--node", type=int, default=0)
    ap.add_argument("--boot-delay", type=float, default=2.0)
    ap.add_argument("--timeout", type=float, default=0.3)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--save", nargs="?", const="config_snapshot.json", metavar="FILE")
    g.add_argument("--diff", metavar="FILE")
    g.add_argument("--restore", metavar="FILE")
    ap.add_argument("--flash", action="store_true",
                    help="with --restore: also write the config page to flash")
    a = ap.parse_args()

    bus = Bus(a.port, a.baud, timeout=a.timeout)
    bus.node = a.node
    try:
        if a.boot_delay > 0:
            time.sleep(a.boot_delay)
        ver = bus.get_version(a.node)
        hb = bus.heartbeat(a.node)
        if hb is None:
            print("no answer: check wiring, baud (config baud=1152) and node id")
            return 1
        if hb["state"] == AXIS_STATE["CLOSED_LOOP_CONTROL"]:
            print("board is armed: disarm first (set_axis_state IDLE)")
            return 1
        show("version", ver)
        show("heartbeat", hb)

        names = config_words(bus)
        if not names:
            print("endpoints.json not found or empty")
            return 1

        if a.save:
            words, missing = read_all(bus, names)
            vc = bus.get_bus_voltage_current(a.node)
            doc = {
                "saved": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "fw": ver,
                "vbus": vc[0] if vc else None,
                "words": words,
            }
            with open(a.save, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2, sort_keys=True)
                f.write("\n")
            show("saved", "%d words -> %s" % (len(words), a.save))
            if missing:
                print("no reply for %d words: %s" % (len(missing), ", ".join(missing)))
            return 0

        path = a.diff or a.restore
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        want = doc["words"]
        have, missing = read_all(bus, names)
        changed = [(n, have[n], want[n]) for n in sorted(want, key=lambda n: names.get(n, 999))
                   if n in have and have[n] != want[n]]

        if a.diff:
            show("snapshot", doc.get("saved", "?"))
            show("changed", "%d of %d" % (len(changed), len(want)))
            for n, cur, old in changed:
                print("  %-28s %6s -> %6s" % (short(n), cur, old))
            return 0

        order = [n for n in sorted(want, key=lambda n: names.get(n, 999)) if n not in LINK_WORDS]
        order += [n for n in LINK_WORDS if n in want]
        failed = []
        for n in order:
            if n not in names:
                print("skip unknown word: %s" % n)
                continue
            if not bus.write(n, want[n]):
                failed.append(n)
                continue
            got = bus.read(n)
            if got != want[n]:
                failed.append(n)
                print("  %-28s wrote %s, read %s" % (short(n), want[n], got))
        show("restored", "%d words" % (len(order) - len(failed)))
        if failed:
            print("FAILED: %s" % ", ".join(short(n) for n in failed))
            return 1

        if a.flash:
            bus.set_input_torque(a.node, 0.0)
            time.sleep(0.1)
            bus.save_configuration(a.node)     # CMD_REBOOT action 1: flash write, no reset
            time.sleep(1.0)
            print("written to flash (no reset); power-cycle and run --diff to verify")
        else:
            print("RAM only; add --flash to store the config page")
        return 0
    finally:
        try:
            bus.s.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
