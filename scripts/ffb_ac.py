#!/usr/bin/env python3
"""Assetto Corsa / ACC -> torque -> hoverboard wheel (CANSimple over UART).

    python scripts/ffb_ac.py --dry-run            # no board: print telemetry + torque
    python scripts/ffb_ac.py --max-torque 1.0     # drive the wheel
    python scripts/ffb_ac.py --help               # all gains

AC/ACC must be in a session (not the menus): the physics shared memory
`Local\\acpmf_physics` then updates. The prefix we read is identical in AC and ACC.

Model (all terms are Nm at the wheel, direct drive):
    center   -k_center * angle * f(speed)          self-aligning spring
    load     -k_lat * |lat_g| * ramp(angle) * f    more load -> heavier wheel
    damping  -damp * wheel_speed
    friction -friction * sign(wheel_speed)         only above a small deadband
    soft lock  -lock_stiff * (|angle| - max_turns)

`f(speed)` goes from `standstill` at 0 km/h to 1 at `speed_ref`. The wheel angle
comes from the motor encoder (turns), centered at the position where the app
started or at `--center`. Press `c` in the console to re-center, `q` to quit.

Wheel free (not clamped). 24 V bench supply, 2 A limit.
"""
import argparse
import ctypes
import math
import os
import struct
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "vendor", "ALIFE-MM32", "tools")
sys.path.insert(0, os.path.abspath(TOOLS))

from cansimple_uart import Bus, CONTROL_MODE, INPUT_MODE, AXIS_STATE, ERRORS  # noqa: E402

try:
    import msvcrt
except ImportError:
    msvcrt = None

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenFileMappingW.restype = wintypes.HANDLE
k32.OpenFileMappingW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
k32.MapViewOfFile.restype = ctypes.c_void_p
k32.MapViewOfFile.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                              wintypes.DWORD, ctypes.c_size_t)
k32.UnmapViewOfFile.argtypes = (ctypes.c_void_p,)
k32.UnmapViewOfFile.restype = wintypes.BOOL
k32.CloseHandle.argtypes = (wintypes.HANDLE,)
k32.CloseHandle.restype = wintypes.BOOL

FILE_MAP_READ = 0x0004


class ACShared:
    """Local\\acpmf_physics: packetId, gas, brake, fuel, gear, rpms, steerAngle,
    speedKmh, velocity[3], accG[3] -- the common AC/ACC prefix."""

    FMT = "<ifffiiffffffff"
    SIZE = struct.calcsize(FMT)

    def __init__(self, name=r"Local\acpmf_physics", stale_after=1.0):
        self.stale_after = stale_after
        self.h = k32.OpenFileMappingW(FILE_MAP_READ, False, name)
        if not self.h:
            raise OSError(ctypes.get_last_error(),
                          "OpenFileMapping(%s) failed: is AC/ACC in a session?" % name)
        self.addr = k32.MapViewOfFile(self.h, FILE_MAP_READ, 0, 0, self.SIZE)
        if not self.addr:
            err = ctypes.get_last_error()
            k32.CloseHandle(self.h)
            raise OSError(err, "MapViewOfFile failed")
        self.last_packet = None
        self.last_change = 0.0

    def read(self, now):
        raw = ctypes.string_at(self.addr, self.SIZE)
        (packet, gas, brake, fuel, gear, rpms, steer, speed,
         vx, vy, vz, ax, ay, az) = struct.unpack(self.FMT, raw)
        if packet != self.last_packet:
            self.last_packet = packet
            self.last_change = now
        if now - self.last_change > self.stale_after:
            return None
        return dict(packet=packet, gas=gas, brake=brake, fuel=fuel, gear=gear,
                    rpms=rpms, steer=steer, speed=speed, acc=(ax, ay, az),
                    vel=(vx, vy, vz))

    def close(self):
        if self.addr:
            k32.UnmapViewOfFile(self.addr)
            self.addr = None
        if self.h:
            k32.CloseHandle(self.h)
            self.h = None


def clamp(x, lim):
    return lim if x > lim else (-lim if x < -lim else x)


def err_names(mask):
    return ", ".join(n for b, n in ERRORS.items() if mask & b) or "none"


class Model:
    def __init__(self, a):
        self.k_center = a.k_center
        self.k_lat = a.k_lat
        self.speed_ref = max(a.speed_ref, 1.0)
        self.standstill = clamp(a.standstill, 1.0)
        self.damp = a.damp
        self.friction = a.friction
        self.max_torque = a.max_torque
        self.max_turns = a.max_turns
        self.lock_stiff = a.lock_stiff

    def __call__(self, speed_kmh, lat_g, angle, vel):
        sp = min(max(speed_kmh, 0.0) / self.speed_ref, 1.0)
        f = self.standstill + (1.0 - self.standstill) * sp
        t = -self.k_center * angle * f
        t += -self.k_lat * abs(lat_g) * clamp(angle / 0.05, 1.0) * f
        t += -self.damp * vel
        if abs(vel) > 0.05:
            t += -self.friction * (1.0 if vel > 0 else -1.0)
        over = abs(angle) - self.max_turns
        if over > 0:
            t += -(1.0 if angle > 0 else -1.0) * self.lock_stiff * over
        return clamp(t, self.max_torque)


def hotkeys():
    """'c' re-center, 'q' quit. Returns 'c', 'q' or None."""
    if not msvcrt:
        return None
    out = None
    while msvcrt.kbhit():
        ch = msvcrt.getwch().lower()
        if ch in ("c", "q", "\x03"):
            out = ch
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--node", type=int, default=0)
    ap.add_argument("--rate", type=float, default=80.0, help="frames/s")
    ap.add_argument("--dry-run", action="store_true", help="no serial: print telemetry and torque")
    ap.add_argument("--boot-delay", type=float, default=2.0)
    ap.add_argument("--timeout", type=float, default=0.2)
    ap.add_argument("--center", type=float, help="wheel turns at center (default: where it starts)")
    ap.add_argument("--max-torque", type=float, default=1.5, help="Nm clamp (bench: keep <= 1.5)")
    ap.add_argument("--k-center", type=float, default=0.45, help="Nm per turn at full speed")
    ap.add_argument("--k-lat", type=float, default=0.55, help="Nm per g at full steering")
    ap.add_argument("--speed-ref", type=float, default=60.0, help="km/h where the effects reach full")
    ap.add_argument("--standstill", type=float, default=0.10, help="fraction of the spring at 0 km/h")
    ap.add_argument("--damp", type=float, default=0.05, help="Nm per turn/s")
    ap.add_argument("--friction", type=float, default=0.03, help="Nm, constant")
    ap.add_argument("--max-turns", type=float, default=1.25, help="soft lock, turns from center")
    ap.add_argument("--lock-stiff", type=float, default=4.0, help="Nm per turn beyond the lock")
    ap.add_argument("--max-bus-a", type=float, default=3.0)
    ap.add_argument("--max-iq-a", type=float, default=6.0)
    a = ap.parse_args()

    model = Model(a)

    try:
        shm = ACShared()
    except OSError as e:
        print("telemetry: %s" % e)
        return 1
    print("telemetry: Local\\acpmf_physics open")

    if a.dry_run:
        print("dry run: Ctrl+C to stop")
        t_print = 0.0
        try:
            while True:
                now = time.perf_counter()
                tel = shm.read(now)
                tq = 0.0 if tel is None else model(tel["speed"], tel["acc"][1], 0.3, 0.0)
                if now - t_print > 0.25:
                    t_print = now
                    if tel is None:
                        print("telemetry stale (AC paused or in the menus)")
                    else:
                        print("speed=%6.1f km/h  lat=%+5.2fg  steer=%+5.2f  gear=%d  rpm=%5d  T@0.3turn=%+.2f Nm"
                              % (tel["speed"], tel["acc"][1], tel["steer"], tel["gear"],
                                 tel["rpms"], tq))
                time.sleep(0.02)
        except KeyboardInterrupt:
            pass
        finally:
            shm.close()
        return 0

    bus = Bus(a.port, a.baud, timeout=a.timeout)
    bus.node = a.node
    armed = False
    center = a.center
    frames = 0
    t0 = 0.0
    try:
        if a.boot_delay > 0:
            time.sleep(a.boot_delay)
        print("version    %s" % bus.get_version(a.node))
        hb = bus.heartbeat(a.node)
        print("heartbeat  %s" % hb)
        if hb is None:
            print("no answer: check wiring, baud (config baud=1152) and node id")
            return 1
        print("error      %s" % bus.get_error(a.node))
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
        print("armed      %s" % hb)
        if hb and hb["errors"]:
            print("refusing to drive, errors: %s" % err_names(hb["errors"]))
            return 1

        if center is None:
            enc = bus.get_encoder_estimates(a.node)
            center = enc[0] if enc else 0.0
        print("center     %.3f turns (press c to re-center, q to quit)" % center)

        pos, vel = center, 0.0
        vc = None
        t_next = time.perf_counter()
        t_print = 0.0
        t0 = t_next
        while True:
            now = time.perf_counter()
            key = hotkeys()
            if key == "q":
                break
            if key == "c":
                center = pos
                print("re-centered at %.3f turns" % center)

            tel = shm.read(now)
            enc = bus.get_encoder_estimates(a.node)
            if enc:
                pos, vel = enc
            angle = pos - center
            tq = 0.0 if tel is None else model(tel["speed"], tel["acc"][1], angle, vel)
            bus.set_input_torque(a.node, tq)
            frames += 1

            if frames % 20 == 0:
                vc = bus.get_bus_voltage_current(a.node)
                hb = bus.heartbeat(a.node)
                if hb is None:
                    raise RuntimeError("lost the board")
                if hb["errors"]:
                    raise RuntimeError("errors: %s" % err_names(hb["errors"]))
                if vc and abs(vc[1]) > a.max_bus_a:
                    raise RuntimeError("bus %.2f A" % vc[1])
            if now - t_print > 0.5:
                t_print = now
                iq = bus.get_iq(a.node)
                if tel is None:
                    print("telemetry stale: torque 0 (AC paused or in the menus)")
                else:
                    print("speed=%6.1f  lat=%+5.2f  ang=%+5.2f  T=%+5.2f  iq=%s  bus=%s"
                          % (tel["speed"], tel["acc"][1], angle, tq,
                             "%+.2f" % iq[1] if iq else "?",
                             "%.2f" % vc[1] if vc else "?"))

            t_next += 1.0 / a.rate
            delay = t_next - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                t_next = time.perf_counter()
        return 0
    except RuntimeError as e:
        print("ABORT: %s" % e)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            bus.set_input_torque(a.node, 0.0)
            if armed:
                bus.set_axis_state(a.node, AXIS_STATE["IDLE"])
            time.sleep(0.3)
            if frames:
                print("rate       %.1f frames/s (target %.0f)"
                      % (frames / max(time.perf_counter() - t0, 1e-6), a.rate))
            print("disarmed   %s" % bus.heartbeat(a.node))
        except Exception as e:
            print("cleanup: %s" % e)
        bus.s.close()
        shm.close()


if __name__ == "__main__":
    sys.exit(main())
