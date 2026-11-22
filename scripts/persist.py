#!/usr/bin/env python3
"""Read or store the main firmware configuration words (CANSimple over UART).

    python scripts/persist.py --show
    python scripts/persist.py --drivemode 10 --save
    python scripts/persist.py --save                 # store what is in RAM now
    python scripts/persist.py --set "config.dc_bus_overvoltage_trip_level=44.9" --save
    python scripts/persist.py --read vbus_voltage

`--save` calls save_configuration: the firmware writes the config page once the
wheel stands still. It does NOT reset, so the power latch stays on. Verify with a
power cycle and `--show`: drivemode must still be 10.

drivemode = commutation*4 + control:
    0 COM_VOLT     1 COM_SPEED     2 COM_TORQUE     3 COM_POSITION
    4 SINE_VOLT    5 SINE_SPEED    6 SINE_TORQUE    7 SINE_POSITION
    8 FOC_VOLT     9 FOC_SPEED    10 FOC_TORQUE    11 FOC_POSITION

Wheel free or clamped, board disarmed. Bench supply (20 V, or 42 V once batfull
is raised above 29 V - see BRINGUP.md section 6).
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "vendor", "ALIFE-MM32", "tools")
sys.path.insert(0, os.path.abspath(TOOLS))

from cansimple_uart import Bus, AXIS_STATE  # noqa: E402

WORDS = ("hoverboard.config.drivemode", "hoverboard.commutation",
         "axis0.controller.config.control_mode", "hoverboard.torque_constant_in_use",
         "axis0.config.motor.current_soft_max", "axis0.pos_estimate", "axis0.vel_estimate",
         "vbus_voltage", "ibus",
         "hoverboard.config.batfull", "hoverboard.config.batempty",
         "config.dc_bus_overvoltage_trip_level", "config.dc_bus_undervoltage_trip_level",
         "axis0.controller.config.vel_limit", "hoverboard.config.overspeederr",
         "hoverboard.config.timeout")


def show(tag, value):
    print("%-14s %s" % (tag, value))


def dump(bus):
    for name in WORDS:
        show(name.split(".")[-1], bus.read(name))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--node", type=int, default=0)
    ap.add_argument("--drivemode", type=int, help="commutation*4 + control, e.g. 10 = FOC + torque")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="write one config word, repeatable (float or int by endpoint type)")
    ap.add_argument("--read", action="append", default=[], metavar="NAME",
                    help="read one endpoint by name, repeatable")
    ap.add_argument("--show", action="store_true", help="print the current words (default: always printed)")
    ap.add_argument("--save", action="store_true", help="write the config page to flash")
    ap.add_argument("--boot-delay", type=float, default=2.0)
    ap.add_argument("--timeout", type=float, default=0.3)
    a = ap.parse_args()

    bus = Bus(a.port, a.baud, timeout=a.timeout)
    bus.node = a.node
    try:
        if a.boot_delay > 0:
            time.sleep(a.boot_delay)

        show("version", bus.get_version(a.node))
        hb = bus.heartbeat(a.node)
        show("heartbeat", hb)
        if hb is None:
            print("no answer: check wiring, baud (config baud=1152) and node id")
            return 1
        dump(bus)

        if hb["state"] == AXIS_STATE["CLOSED_LOOP_CONTROL"]:
            print("board is armed: disarm first (set_axis_state IDLE)")
            return 1

        for name in a.read:
            show(name.split(".")[-1], bus.read(name))

        for spec in a.set:
            name, eq, val = spec.partition("=")
            if not eq:
                print("--set wants NAME=VALUE, got %r" % spec)
                return 1
            try:
                _, typ = bus._ep(name)
            except KeyError:
                print("unknown endpoint: %s" % name)
                return 1
            value = float(val) if typ == "float" else int(float(val))
            if not bus.write(name, value):
                print("SDO write failed: %s" % name)
                return 1
            time.sleep(0.15)
            got = bus.read(name)
            show(name.split(".")[-1], got)
            if got is None:
                print("no reply reading %s back" % name)
                return 1

        if a.drivemode is not None:
            if not 0 <= a.drivemode <= 11:
                print("drivemode must be 0..11")
                return 1
            if not bus.write("hoverboard.config.drivemode", a.drivemode):
                print("SDO write failed")
                return 1
            time.sleep(0.3)
            got = bus.read("hoverboard.config.drivemode")
            show("drivemode", got)
            if got != a.drivemode:
                print("did not take: %s (want %d)" % (got, a.drivemode))
                return 1

        if a.save:
            bus.set_input_torque(a.node, 0.0)      # speed = 0, the save waits for a still wheel
            time.sleep(0.1)
            bus.save_configuration(a.node)         # fire-and-forget: CMD_REBOOT action 1, no reply
            time.sleep(1.0)                        # flash write happens in RemoteUpdate
            print("saved (no reset); power-cycle the board and run --show to verify")
            dump(bus)

        return 0
    finally:
        try:
            bus.s.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
