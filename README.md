# Hoverboard wheel -> sim-racing FFB wheel

DIY force-feedback steering wheel built from a hoverboard BLDC hub motor and a
hoverboard mainboard (MM32SPIN05PF), driven from a PC over an ESP32-S3 USB
bridge. Torque control, not speed: the wheel is a direct-drive FFB axis.

**Current state: FFB works in Dirt Rally 2.0.** The wheel position goes to the
game as a vJoy axis, and the game's DirectInput force feedback comes back as
torque (0.2–0.4 Nm proven on the bench). See [`STATE.md`](STATE.md) for the
exact working configuration, [`BRINGUP.md`](BRINGUP.md) for the bring-up ladder
and journal, and [`AGENT_CONTEXT.md`](AGENT_CONTEXT.md) for the handover notes
(decisions, pitfalls, open issues).

**Rebuilding from a clean laptop:** the standalone beginner guide lives in a
separate repo, <https://github.com/mike7442/sim-wheel>.

## Hardware

| Part | What |
|---|---|
| Motor | hoverboard hub motor, 15 pole pairs (30 windings), Hall sensors |
| Controller | hoverboard mainboard, MM32SPIN05PF, FOC + phase current sensing |
| Power | 42 V 9 A PSU (native 10S voltage); 20 V lab supply for early bring-up |
| Bridge | ESP32-S3, native USB CDC <-> UART1, 115200, RX GPIO16 / TX GPIO17 |
| Link | CANSimple over UART, node 0 |

## Layout

```
scripts/            PC tools (Python, no third-party packages)
  pf.py             PinFinder wrapper: detect/save/export/import pin config
  spin.py           first controlled velocity spin (keep-alive)
  torque.py         FOC torque steps with abort thresholds
  ffb_test.py       bench FFB effects: const / sine / spring
  ffb_ac.py         Assetto Corsa / ACC shared memory -> torque
  vjoy_wheel.py     wheel position -> vJoy virtual joystick axis
  vjoy_ffb.py       position -> vJoy axis, game FFB -> torque
  persist.py        read/write config words, save to flash
  snapshot.py       snapshot/restore the whole config page
esp32/bridge/       ESP32-S3 USB<->UART bridge (PlatformIO)
snapshots/          known-good firmware binaries
config_snapshot.json  the board's config page at the checkpoint
board.json          PinFinder pin config export
before.bin          original board dump
vendor/             third-party firmware (gitignored, pinned in vendor.lock)
```

## Quick start

```powershell
# 1. third-party firmware at the pinned commit
powershell -ExecutionPolicy Bypass -File scripts\fetch_vendor.ps1

# 2. build (Arm GNU 14.2.1; GCC 12 overflows the flash)
#    in Git Bash:
make MCU=SPIN05 CROSS="/c/Users/mikha/AppData/Local/Temp/opencode/arm-14.2/bin/arm-none-eabi-" clean both
#    run in vendor/ALIFE-MM32/HoverBoardMindMotion

# 3. flash (keeps the config page at 0x08007C00)
python vendor\ALIFE-MM32\tools\mm32flash.py vendor\ALIFE-MM32\HoverBoardMindMotion\build\spin05\hoverboard.bin

# 4. restore the board config after a reflash
python scripts\snapshot.py --restore config_snapshot.json --flash
```

The ESP32 bridge is built and uploaded with PlatformIO from `esp32/bridge`
(`pio run -t upload`). Opening the COM port resets the ESP32: wait ~2 s
before the first byte.

## Safety

The wheel is a direct drive: whatever the firmware commands goes straight to
the rim. 42 V 9 A is not a lab supply with a 2 A limit.

- 5 A time-lag ceramic fuse (125 V DC) in the positive lead, close to the PSU
- latching E-stop (NC contact) in series, DC-rated or on the PSU mains side
- precharge / anti-spark on the 42 V input
- the firmware zeroes the torque 400 ms after the last command, but the
  hardware E-stop is what stops a hung PC

Details and the 42 V transition are in `BRINGUP.md` section 6.
