#!/usr/bin/env python3
"""FFB feel test: stream torque from the PC to the wheel at ~100 Hz.

    python scripts/ffb_test.py
    python scripts/ffb_test.py --max-torque 0.4 --rate 100
    python scripts/ffb_test.py --effects const,sine,spring

Hold the wheel (free, not clamped). It will pull, shake and spring back to the
center. 24 V bench supply, 2 A limit.

Effects:
    const   +0.5*T for 2 s, then -0.5*T for 2 s
    sine    0.5*T amplitude, 0.5 -> 5 Hz over 4 s
    spring  k = 1.2*T per turn towards the start position, c = 0.1*T per turn/s

This is the same link a game will use (PC -> ESP32 -> MM32), so the measured
frame rate is the rate FFB will run at.
"""
import argparse
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "vendor", "ALIFE-MM32", "tools")
sys.path.insert(0, os.path.abspath(TOOLS))

from cansimple_uart import Bus, CONTROL_MODE, INPUT_MODE, AXIS_STATE, ERRORS  # noqa: E402


def show(tag, value):
    print("%-12s %s" % (tag, value))


def err_names(mask):
    return ", ".join(n for b, n in ERRORS.items() if mask & b) or "none"


def clamp(x, lim):
    return lim if x > lim else (-lim if x < -lim else x)


class Runner:
    def __init__(self, bus, node, rate, max_torque, max_bus_a, max_iq_a):
        self.bus = bus
        self.node = node
        self.dt = 1.0 / rate
        self.max_torque = max_torque
        self.max_bus_a = max_bus_a
        self.max_iq_a = max_iq_a
        self.frames = 0
        self.t0 = 0.0

    def stream(self, seconds, torque_fn, need_pos):
        bus, node = self.bus, self.node
        t0 = time.time()
        t_next = t0
        pos = vel = 0.0
        i = 0
        while True:
            t = time.time() - t0
            if t >= seconds:
                break
            if need_pos:
                enc = bus.get_encoder_estimates(node)
                if enc:
                    pos, vel = enc
            bus.set_input_torque(node, clamp(torque_fn(t, pos, vel), self.max_torque))
            self.frames += 1
            i += 1
            if i % 25 == 0:
                vc = bus.get_bus_voltage_current(node)
                hb = bus.heartbeat(node)
                iq = bus.get_iq(node)
                if hb is None:
                    raise RuntimeError("lost the board")
                if hb["errors"]:
                    raise RuntimeError("errors: %s" % err_names(hb["errors"]))
                if vc and abs(vc[1]) > self.max_bus_a:
                    raise RuntimeError("bus %.2f A" % vc[1])
                if iq and abs(iq[1]) > self.max_iq_a:
                    raise RuntimeError("|I_q| %.2f A" % abs(iq[1]))
                show("t", "%.2fs pos=%.2f vel=%.2f iq=%s bus=%s"
                     % (t, pos, vel,
                        "%.2f" % iq[1] if iq else "?",
                        "%.2fA" % vc[1] if vc else "?"))
            t_next += self.dt
            delay = t_next - time.time()
            if delay > 0:
                time.sleep(delay)
            else:
                t_next = time.time()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--node", type=int, default=0)
    ap.add_argument("--rate", type=float, default=100.0, help="frames/s")
    ap.add_argument("--max-torque", type=float, default=0.6, help="Nm clamp for every effect")
    ap.add_argument("--effects", default="const,sine,spring")
    ap.add_argument("--boot-delay", type=float, default=2.0)
    ap.add_argument("--timeout", type=float, default=0.2)
    ap.add_argument("--max-bus-a", type=float, default=3.0)
    ap.add_argument("--max-iq-a", type=float, default=6.0)
    a = ap.parse_args()

    effects = [e.strip() for e in a.effects.split(",") if e.strip()]
    for e in effects:
        if e not in ("const", "sine", "spring"):
            print("unknown effect: %s (const, sine, spring)" % e)
            return 1

    bus = Bus(a.port, a.baud, timeout=a.timeout)
    bus.node = a.node
    armed = False
    runner = Runner(bus, a.node, a.rate, a.max_torque, a.max_bus_a, a.max_iq_a)
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
        show("commutation", bus.read("hoverboard.commutation"))
        show("control_mode", bus.read("axis0.controller.config.control_mode"))
        if hb["errors"]:
            print("refusing to arm, errors: %s" % err_names(hb["errors"]))
            return 1

        if bus.read("hoverboard.commutation") != 2:
            if not bus.write("hoverboard.commutation", 2):
                print("could not switch to FOC")
                return 1
            time.sleep(0.2)
        bus.set_controller_mode(a.node, CONTROL_MODE["TORQUE_CONTROL"], INPUT_MODE["PASSTHROUGH"])
        time.sleep(0.15)

        bus.set_axis_state(a.node, AXIS_STATE["CLOSED_LOOP_CONTROL"])
        armed = True
        time.sleep(0.2)
        hb = bus.heartbeat(a.node)
        show("armed", hb)
        if hb and hb["errors"]:
            print("refusing to drive, errors: %s" % err_names(hb["errors"]))
            return 1

        T = a.max_torque
        t_start = time.time()
        for name in effects:
            print("== %s" % name)
            if name == "const":
                runner.stream(2.0, lambda t, p, v: 0.5 * T, False)
                runner.stream(2.0, lambda t, p, v: -0.5 * T, False)
            elif name == "sine":
                def sine(t, p, v):
                    f = 0.5 + 4.5 * min(t / 4.0, 1.0)
                    return 0.5 * T * math.sin(2.0 * math.pi * f * t)
                runner.stream(4.0, sine, False)
            elif name == "spring":
                enc = bus.get_encoder_estimates(a.node)
                center = enc[0] if enc else 0.0
                show("center", center)
                runner.stream(4.0, lambda t, p, v: -1.2 * T * (p - center) - 0.1 * T * v, True)

        elapsed = time.time() - t_start
        show("rate", "%.1f frames/s (target %.0f)" % (runner.frames / elapsed, a.rate))
        bus.set_input_torque(a.node, 0.0)
        time.sleep(0.3)
        show("final", bus.heartbeat(a.node))
        return 0
    except RuntimeError as e:
        print("ABORT: %s" % e)
        return 1
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
