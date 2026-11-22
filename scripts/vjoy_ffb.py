#!/usr/bin/env python3
"""Wheel axis to vJoy + the game's force feedback back as torque.

    python scripts/vjoy_ffb.py --dry-run                  # no motor, print only
    python scripts/vjoy_ffb.py --max-torque 0.3           # first real run
    python scripts/vjoy_ffb.py --max-torque 0.8 --invert-torque

The loop does two things at once:

  pos_estimate -> vJoy axis -> the game sees the wheel turning
  game -> DirectInput FFB -> vJoy callback -> torque -> MM32

This is the same scheme as the Arduino prototype (`Simple_FFB.py`), with the
hoverboard board replacing the Arduino and FOC torque replacing PWM.

Dirt Rally 2 sends one constant-force effect (type 1) at 100 Hz, magnitude
-10000..+10000, gain 255; that is all this script needs. Effects are tracked
per block index, and a stop/reset of the effect zeroes the torque instead of
leaving the last value on the rim.

Safety: the firmware zeroes the torque 400 ms after the last command, and the
loop disarms on any board error, overcurrent or lost link. The wheel is direct
drive: keep --max-torque low until the fuse and the E-stop are in.
"""
import argparse
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "vendor", "ALIFE-MM32", "tools")
sys.path.insert(0, os.path.abspath(TOOLS))

from cansimple_uart import Bus, AXIS_STATE, CONTROL_MODE, INPUT_MODE  # noqa: E402

try:
    import pyvjoy
except ImportError:                     # the axis and FFB need it; --dry-run too
    pyvjoy = None

AXES = {"X": 0x30, "Y": 0x31, "Z": 0x32, "RX": 0x33, "RY": 0x34, "RZ": 0x35}
AXIS_MAX = 32767
AXIS_MID = 16384
MAG_MAX = 10000.0


def clip(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class FFBState:
    """Latest constant-force magnitudes from the vJoy FFB callback.

    The callback runs in pyvjoy's thread; the main loop only reads the sum.
    Effects are kept per block index so a stop/reset of one block cannot be
    mistaken for another effect.
    """

    def __init__(self, deadzone):
        self.lock = threading.Lock()
        self.mags = {}
        self.gain = 255
        self.deadzone = deadzone
        self.packets = 0
        self.last_type = None

    def callback(self, data, reptype):
        try:
            packetdict, ebi = pyvjoy.FFB_Effect_Manager.ffb_packet_to_dict(data, reptype)
        except Exception:
            return
        with self.lock:
            self.packets += 1
            self.last_type = reptype
            if reptype == 5:                       # set constant force
                c = packetdict.get("const")
                if c is not None:
                    self.mags[ebi] = int(c.get("Magnitude", 0))
            elif reptype == 10:                    # effect operation
                op = packetdict.get("effop", {}).get("EffectOp")
                if op == 3:                        # EFF_STOP
                    self.mags.pop(ebi, None)
            elif reptype == 11:                    # block free
                freed = packetdict.get("blkfree")
                if freed:
                    self.mags.pop(int(freed), None)
            elif reptype == 12:                    # device control
                ctrl = packetdict.get("ctrl")
                if ctrl in (2, 3, 4, 5):           # disable, stop all, reset, pause
                    self.mags.clear()
            elif reptype == 13:                    # device gain
                self.gain = int(packetdict.get("gain", 255))

    def raw(self):
        with self.lock:
            v = float(sum(self.mags.values())) * self.gain / 255.0
        return clip(v, -MAG_MAX, MAG_MAX)

    def normalized(self):
        m = self.raw()
        dz = self.deadzone
        if abs(m) <= dz:
            return 0.0
        s = (abs(m) - dz) / (MAG_MAX - dz)
        return -s if m < 0 else s


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
    ap.add_argument("--max-torque", type=float, default=0.3,
                    help="Nm at full magnitude; 1.5 Nm = 4.4 A")
    ap.add_argument("--ffb-deadzone", type=float, default=150.0,
                    help="ignore |magnitude| below this (of 10000)")
    ap.add_argument("--ffb-smoothing", type=float, default=0.3,
                    help="0 = no update, 1 = no smoothing")
    ap.add_argument("--invert-axis", action="store_true")
    ap.add_argument("--invert-torque", action="store_true")
    ap.add_argument("--max-bus-a", type=float, default=3.0)
    ap.add_argument("--max-iq-a", type=float, default=6.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="do not arm or drive; print position and FFB")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="stop after this many seconds (0 = until Ctrl+C)")
    a = ap.parse_args()

    if a.range_deg <= 0:
        ap.error("--range-deg must be positive")
    if a.max_torque < 0:
        ap.error("--max-torque must be >= 0")

    if pyvjoy is None:
        print("pyvjoy is not installed: pip install pyvjoy")
        return 1

    try:
        joy = pyvjoy.VJoyDevice(a.device)
    except Exception as e:
        print("vJoy device %d: %s" % (a.device, e))
        return 1
    if not joy.ffb_supported():
        print("vJoy device %d has no force feedback: enable FFB in vJoyConf" % a.device)
        return 1

    ffb = FFBState(a.ffb_deadzone)
    joy.ffb_register_callback(ffb.callback)

    bus = Bus(a.port, a.baud, timeout=a.timeout)
    bus.node = a.node
    armed = False
    smooth = 0.0
    try:
        if a.boot_delay > 0:
            time.sleep(a.boot_delay)
        hb = bus.heartbeat(a.node)
        if hb is None:
            print("no answer: check wiring, baud and node id")
            return 1
        if hb["errors"]:
            print("board errors: %s" % hb["error_names"])
            return 1
        if hb["state"] == AXIS_STATE["CLOSED_LOOP_CONTROL"]:
            print("board is already armed: disarm first")
            return 1

        enc = bus.get_encoder_estimates(a.node)
        if enc is None:
            print("no encoder estimate")
            return 1
        centre = enc[0]
        half = a.range_deg / 720.0

        if not a.dry_run:
            bus.set_controller_mode(a.node, CONTROL_MODE["TORQUE_CONTROL"],
                                    INPUT_MODE["PASSTHROUGH"])
            time.sleep(0.15)
            bus.set_axis_state(a.node, AXIS_STATE["CLOSED_LOOP_CONTROL"])
            armed = True
            time.sleep(0.2)
            hb = bus.heartbeat(a.node)
            if hb is None or hb["errors"]:
                print("arm failed: %s" % (hb and hb["error_names"]))
                return 1
            print("armed, max torque %.2f Nm" % a.max_torque)

        print("centre %+.4f turns, range %.0f deg, axis %s%s, dry-run %s"
              % (centre, a.range_deg, a.axis,
                 " (inverted)" if a.invert_axis else "", a.dry_run))

        t0 = time.time()
        t_print = t0
        frames = 0
        while True:
            enc = bus.get_encoder_estimates(a.node)
            if enc is None:
                print("lost the board")
                break
            pos = enc[0]
            frac = (pos - centre) / half
            if a.invert_axis:
                frac = -frac
            axis = int(round(AXIS_MID + clip(frac, -1.0, 1.0) * AXIS_MID))
            joy.set_axis(AXES[a.axis], axis)

            target = ffb.normalized()
            smooth += a.ffb_smoothing * (target - smooth)
            torque = smooth * a.max_torque
            if a.invert_torque:
                torque = -torque
            if armed:
                bus.set_input_torque(a.node, torque)
            frames += 1

            now = time.time()
            if now - t_print >= 1.0:
                iq = bus.get_iq(a.node)
                vc = bus.get_bus_voltage_current(a.node)
                hb = bus.heartbeat(a.node)
                print("pos %+.4f  axis %5d  ffb %+6.3f  tq %+.3f Nm  iq %s  bus %s  %.1f Hz"
                      % (pos, axis, smooth, torque,
                         "%.2f" % iq[1] if iq else "?",
                         "%.2f/%.2f" % vc if vc else "?", frames / (now - t_print)))
                frames = 0
                t_print = now
                if hb is None:
                    print("lost the board")
                    break
                if hb["errors"]:
                    print("ABORT, errors: %s" % hb["error_names"])
                    break
                if iq and abs(iq[1]) > a.max_iq_a:
                    print("ABORT, |Iq| %.2f A" % iq[1])
                    break
                if vc and abs(vc[1]) > a.max_bus_a:
                    print("ABORT, bus %.2f A" % vc[1])
                    break

            if a.seconds and now - t0 >= a.seconds:
                break
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        if armed:
            try:
                bus.set_input_torque(a.node, 0.0)
                time.sleep(0.05)
                bus.set_axis_state(a.node, AXIS_STATE["IDLE"])
            except Exception:
                pass
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
