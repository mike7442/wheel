#!/usr/bin/env python3
"""Feed the wheel position to a vJoy virtual joystick axis.

    python scripts/vjoy_wheel.py                    # X axis, 900 deg range
    python scripts/vjoy_wheel.py --range-deg 540 --invert
    python scripts/vjoy_wheel.py --dry-run          # no vJoy, print only

Position is the MM32 encoder estimate (`pos_estimate`, turns at the motor
shaft). The first sample becomes the wheel centre, +-range_deg/2 is mapped to
0..32767. The game sees a DirectInput device: bind steering to that axis and
run the game's own calibration.

This script only produces the axis. Force feedback is a separate loop that
sends torque to the same board.

The wheel is direct drive, so keep the torque at zero while calibrating.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "vendor", "ALIFE-MM32", "tools")
sys.path.insert(0, os.path.abspath(TOOLS))

from cansimple_uart import Bus  # noqa: E402

AXES = {"X": 0x30, "Y": 0x31, "Z": 0x32, "RX": 0x33, "RY": 0x34, "RZ": 0x35}
AXIS_MAX = 32767
AXIS_MID = 16384


def clip(v):
    return 0 if v < 0 else AXIS_MAX if v > AXIS_MAX else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--node", type=int, default=0)
    ap.add_argument("--boot-delay", type=float, default=2.0,
                    help="ESP32 native USB restarts on port open")
    ap.add_argument("--timeout", type=float, default=0.3)
    ap.add_argument("--device", type=int, default=1, help="vJoy device id")
    ap.add_argument("--axis", choices=sorted(AXES), default="X")
    ap.add_argument("--range-deg", type=float, default=900.0,
                    help="total steering range, degrees (min to max)")
    ap.add_argument("--invert", action="store_true",
                    help="flip the axis direction")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the axis instead of writing vJoy")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="stop after this many seconds (0 = until Ctrl+C)")
    a = ap.parse_args()

    if a.range_deg <= 0:
        ap.error("--range-deg must be positive")

    joy = None
    if not a.dry_run:
        try:
            import pyvjoy
        except ImportError:
            print("pyvjoy is not installed: pip install pyvjoy")
            return 1
        try:
            joy = pyvjoy.VJoyDevice(a.device)
            joy.set_axis(AXES[a.axis], AXIS_MID)
        except Exception as e:
            print("vJoy device %d: %s" % (a.device, e))
            return 1

    bus = Bus(a.port, a.baud, timeout=a.timeout)
    bus.node = a.node
    try:
        if a.boot_delay > 0:
            time.sleep(a.boot_delay)
        hb = bus.heartbeat(a.node)
        if hb is None:
            print("no answer: check wiring, baud and node id")
            return 1
        if hb["state"] == 8:
            print("board is armed: disarm first")
            return 1

        enc = bus.get_encoder_estimates(a.node)
        if enc is None:
            print("no encoder estimate")
            return 1
        centre = enc[0]
        half = a.range_deg / 720.0          # turns at the range edge
        print("centre %.4f turns, range %.0f deg, axis %s%s"
              % (centre, a.range_deg, a.axis, " (inverted)" if a.invert else ""))

        t0 = time.time()
        t_print = t0
        frames = 0
        last_pos = centre
        while True:
            enc = bus.get_encoder_estimates(a.node)
            if enc is None:
                print("lost the board")
                return 1
            pos = enc[0]
            frac = (pos - centre) / half
            if a.invert:
                frac = -frac
            axis = clip(int(round(AXIS_MID + frac * AXIS_MID)))
            if joy is not None:
                joy.set_axis(AXES[a.axis], axis)
            frames += 1
            last_pos = pos

            now = time.time()
            if now - t_print >= 1.0:
                print("pos %+.4f turns  axis %5d  %.1f Hz"
                      % (pos, axis, frames / (now - t_print)))
                frames = 0
                t_print = now
            if a.seconds and now - t0 >= a.seconds:
                break

        print("last pos %+.4f turns" % last_pos)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        if joy is not None:
            try:
                joy.reset()
            except Exception:
                pass
        try:
            bus.s.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
