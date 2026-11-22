#!/usr/bin/env python3
"""First controlled spin of the main firmware over CANSimple/UART.

    python scripts/spin.py
    python scripts/spin.py --vel 1.0 --seconds 5
    python scripts/spin.py --port COM5 --node 0

Flow: version -> heartbeat -> errors -> velocity mode -> arm -> slow speed ->
read encoder -> stop -> idle. Always disarms in `finally`.

Wheel clamped, 24 V bench supply with a 2 A current limit.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "vendor", "ALIFE-MM32", "tools")
sys.path.insert(0, os.path.abspath(TOOLS))

from cansimple_uart import Bus, CONTROL_MODE, INPUT_MODE, AXIS_STATE, ERRORS  # noqa: E402


def show(tag, value):
    print("%-10s %s" % (tag, value))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--node", type=int, default=0)
    ap.add_argument("--vel", type=float, default=0.5, help="turns/s (0.5 = ~28 rpm)")
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--boot-delay", type=float, default=2.0,
                    help="ESP32 native USB restarts on port open")
    ap.add_argument("--timeout", type=float, default=0.3, help="reply timeout per request")
    a = ap.parse_args()

    print("port %s @ %d, node %d, target %.2f turns/s" % (a.port, a.baud, a.node, a.vel))
    bus = Bus(a.port, a.baud, timeout=a.timeout)
    armed = False
    try:
        if a.boot_delay > 0:
            time.sleep(a.boot_delay)

        ver = bus.get_version(a.node)
        show("version", ver)
        hb = bus.heartbeat(a.node)
        show("heartbeat", hb)
        err = bus.get_error(a.node)
        show("error", err)
        if ver is None and hb is None:
            print("no answer: check wiring, baud (config baud=1152) and node id")
            return 1

        bus.set_controller_mode(a.node, CONTROL_MODE["VELOCITY_CONTROL"], INPUT_MODE["PASSTHROUGH"])
        time.sleep(0.1)
        bus.set_axis_state(a.node, AXIS_STATE["CLOSED_LOOP_CONTROL"])
        armed = True
        time.sleep(0.2)

        hb = bus.heartbeat(a.node)
        show("armed", hb)
        if hb and hb["errors"]:
            print("refusing to spin, errors: %s" % ", ".join(hb["error_names"]))
            return 1

        t0 = time.time()
        while time.time() - t0 < a.seconds:
            bus.set_input_vel(a.node, a.vel)    # keep-alive: timeout is 300-400 ms in the config
            enc = bus.get_encoder_estimates(a.node)
            vc = bus.get_bus_voltage_current(a.node)
            show("encoder", enc)
            show("bus", vc)
            time.sleep(0.1)

        bus.set_input_vel(a.node, 0.0)
        time.sleep(0.3)
        show("final", bus.heartbeat(a.node))
        return 0
    finally:
        try:
            bus.set_input_vel(a.node, 0.0)
            if armed:
                bus.set_axis_state(a.node, AXIS_STATE["IDLE"])
            time.sleep(0.4)
            show("disarmed", bus.heartbeat(a.node))
        except Exception as e:
            print("cleanup: %s" % e)
        bus.s.close()


if __name__ == "__main__":
    sys.exit(main())
