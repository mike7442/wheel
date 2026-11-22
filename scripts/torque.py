#!/usr/bin/env python3
"""First torque test on the main firmware (CANSimple over UART).

    python scripts/torque.py --check                        # read-only, does not arm
    python scripts/torque.py                                # FOC torque, clamped wheel
    python scripts/torque.py --torque 0.1 --torque 0.3 --seconds 2
    python scripts/torque.py --commutation block            # block torque (bus current loop)

Wheel clamped. 24 V bench supply with a 2 A current limit.

Units: `set_input_torque` is Nm at the motor shaft. The firmware converts with
TorqueConstant() = 82.7 / kv (kv unset -> 242 -> ~0.342 Nm/A), so 0.1 Nm is
~0.29 A of phase current. `get_iq` returns (setpoint, measured) in A.

FOC is switched on in RAM through the SDO endpoint `hoverboard.commutation`
(0 block, 1 sine, 2 FOC), so this test needs no reflash. `save_configuration`
(REBOOT action 1) would store it, but it also releases the power latch.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "vendor", "ALIFE-MM32", "tools")
sys.path.insert(0, os.path.abspath(TOOLS))

from cansimple_uart import Bus, CONTROL_MODE, INPUT_MODE, AXIS_STATE, ERRORS  # noqa: E402

COMMUTATION = {"block": 0, "sine": 1, "foc": 2}


def show(tag, value):
    print("%-12s %s" % (tag, value))


def err_names(mask):
    return ", ".join(n for b, n in ERRORS.items() if mask & b) or "none"


def param(bus, name):
    v = bus.read(name)
    return "?" if v is None else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--node", type=int, default=0)
    ap.add_argument("--commutation", choices=sorted(COMMUTATION), default="foc")
    ap.add_argument("--torque", type=float, action="append",
                    help="Nm, repeatable; default 0.1 -0.1 0.2 -0.2")
    ap.add_argument("--seconds", type=float, default=1.5, help="per torque step")
    ap.add_argument("--boot-delay", type=float, default=2.0,
                    help="ESP32 native USB restarts on port open")
    ap.add_argument("--timeout", type=float, default=0.3)
    ap.add_argument("--max-bus-a", type=float, default=3.0,
                    help="abort above this bus current (42 V PSU: 9 A; the 3 mOhm shunt reads ~0.3 A per count)")
    ap.add_argument("--max-iq-a", type=float, default=6.0,
                    help="abort above this |measured I_q| (1.5 Nm = 4.4 A)")
    ap.add_argument("--check", action="store_true", help="read parameters only, do not arm")
    a = ap.parse_args()

    torques = a.torque if a.torque else [0.1, -0.1, 0.2, -0.2]

    bus = Bus(a.port, a.baud, timeout=a.timeout)
    bus.node = a.node
    armed = False
    try:
        if a.boot_delay > 0:
            time.sleep(a.boot_delay)

        show("version", bus.get_version(a.node))
        hb = bus.heartbeat(a.node)
        show("heartbeat", hb)
        if hb is None:
            print("no answer: check wiring, baud (config baud=1152) and node id")
            return 1
        show("error", bus.get_error(a.node))

        kt = param(bus, "hoverboard.torque_constant_in_use")
        show("kt Nm/A", kt)
        show("commutation", param(bus, "hoverboard.commutation"))
        show("control_mode", param(bus, "axis0.controller.config.control_mode"))
        show("current_max", param(bus, "axis0.config.motor.current_soft_max"))
        show("iq A", bus.get_iq(a.node))

        if a.check:
            print("check only: not armed")
            return 0

        if hb["errors"]:
            print("refusing to arm, errors: %s" % err_names(hb["errors"]))
            return 1

        want = COMMUTATION[a.commutation]
        got = param(bus, "hoverboard.commutation")
        if got != want:
            if not bus.write("hoverboard.commutation", want):
                print("SDO write of commutation failed")
                return 1
            time.sleep(0.2)
            got = param(bus, "hoverboard.commutation")
        show("commutation", got)
        if got != want:
            print("commutation did not take: %s (want %d)" % (got, want))
            return 1

        bus.set_controller_mode(a.node, CONTROL_MODE["TORQUE_CONTROL"], INPUT_MODE["PASSTHROUGH"])
        time.sleep(0.15)
        show("control_mode", param(bus, "axis0.controller.config.control_mode"))
        if param(bus, "axis0.controller.config.control_mode") != CONTROL_MODE["TORQUE_CONTROL"]:
            print("controller mode did not take (commutation %s supports torque?)" % a.commutation)
            return 1

        bus.set_axis_state(a.node, AXIS_STATE["CLOSED_LOOP_CONTROL"])
        armed = True
        time.sleep(0.2)
        hb = bus.heartbeat(a.node)
        show("armed", hb)
        if hb and hb["errors"]:
            print("refusing to drive, errors: %s" % err_names(hb["errors"]))
            return 1

        for tq in torques:
            print("-- torque %.3f Nm for %.1f s" % (tq, a.seconds))
            bus.set_input_torque(a.node, tq)          # set the step before the first read
            time.sleep(0.05)                          # let the firmware take it (UART + main loop)
            t_end = time.time() + a.seconds
            while time.time() < t_end:
                bus.set_input_torque(a.node, tq)      # also the keep-alive (timeout=1000 ms)
                iq = bus.get_iq(a.node)
                vc = bus.get_bus_voltage_current(a.node)
                enc = bus.get_encoder_estimates(a.node)
                hb = bus.heartbeat(a.node)
                show("iq/enc", "%s  enc=%s" % (iq, enc))
                show("bus", vc)
                if hb is None:
                    print("lost the board")
                    return 1
                if hb["errors"]:
                    print("ABORT, errors: %s" % err_names(hb["errors"]))
                    return 1
                if iq and abs(iq[1]) > a.max_iq_a:
                    print("ABORT, |I_q| %.2f A above %.2f A" % (abs(iq[1]), a.max_iq_a))
                    return 1
                if vc and abs(vc[1]) > a.max_bus_a:
                    print("ABORT, bus %.2f A above %.2f A" % (vc[1], a.max_bus_a))
                    return 1
                time.sleep(0.2)

        bus.set_input_torque(a.node, 0.0)
        time.sleep(0.3)
        show("final", bus.heartbeat(a.node))
        return 0
    finally:
        try:
            bus.set_input_torque(a.node, 0.0)
            if armed:
                bus.set_axis_state(a.node, AXIS_STATE["IDLE"])
            time.sleep(0.4)
            show("disarmed", bus.heartbeat(a.node))
        except Exception as e:
            print("cleanup: %s" % e)
        bus.s.close()


if __name__ == "__main__":
    sys.exit(main())
