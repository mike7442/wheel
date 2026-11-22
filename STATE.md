# STATE — known-good checkpoint

**Date:** 2026-11-22
**Tag:** `checkpoint-42v-torque`
**Board:** MM32SPIN05PF, master, `drivemode=10` (FOC + torque), node 0
**Power:** 42 V 9 A PSU (switched from the 20 V lab supply this day)

## What works

- [x] SWD flash via `mm32flash.py`; PinFinder 2.0 pin detection, saved config
- [x] CANSimple over UART at 115200 through the ESP32-S3 bridge (COM4)
- [x] Controlled velocity spin (+/- turns/s), keep-alive, clean disarm
- [x] FOC torque: 0.1/0.2 Nm tracked within ~15 %, at standstill and while spinning
- [x] `drivemode=10` persisted in flash, survives power cycle
- [x] PC -> ESP32 -> MM32 torque stream at ~80 Hz (Python/Windows sleep limit)
- [x] Bench FFB effects: const / sine / spring, no jolts
- [x] AC/ACC shared memory (`Local\acpmf_physics`) -> torque, dry-run verified
- [x] 42 V power-up: `vbus` 41.98, `vbatgain` linear, no ADC saturation

## What is not done

- [ ] Wheel position to the game as an axis (HID) — the next milestone
- [ ] DR2 (Colin McRae Dirt Rally 2) telemetry / FFB path
- [ ] Calibrations: `hallang1..6`, `motorr`, `phasel`, `sinekv` still default
- [ ] Mechanics: frame, column, bearings, mechanical lock
- [ ] Fuse + E-stop installed (spec in `BRINGUP.md` section 6)

## Board config (key words)

Authoritative full dump: `config_snapshot.json` (113 rw words, saved 2026-11-22
17:46 at `vbus` 42.02). The page has 116 words; the other 3 are read-only.
Restore: `python scripts\snapshot.py --restore config_snapshot.json --flash`
(then power-cycle and `--diff`).

| Word | Value | Meaning |
|---|---|---|
| `drivemode` | 10 | FOC + torque |
| `timeout` | 400 | ms without a command -> torque 0 |
| `batfull` | 41900 | mV; 100 mV below the 42 V bus, blocks regen into the PSU |
| `batempty` | 18000 | mV; PSU setup, raise to 30000 on the real 10S pack |
| `posrpm` | 65535 = default 300 | vel_limit 5 turns/s; torque falls to 0 there |
| `focimax` | 0 = default 100 (10 A) | phase current limit |
| `overspeederr` | 65535 | off |
| `vbatgain` | 29985 | battery divider 29.985 (linear at 42 V) |
| `itotaldivider` | 332 | bus shunt ~3 mOhm |
| `itotalzero` | 389 | shunt zero |
| `windings` | 30 | 15 pole pairs, 90 hall steps/turn |
| `baud` | 1152 | 115200 |

ODrive-style views: overvoltage trip 44.9 V, undervoltage trip 18.0 V,
`vel_limit` 5.0 turns/s, `current_soft_max` 10 A, `kt` 0.3417 Nm/A.

## Toolchain

- Arm GNU Toolchain **14.2.1** (GCC 12.2.1 overflows the flash by 580 B)
- build (Git Bash, in `vendor/ALIFE-MM32/HoverBoardMindMotion`):
  `make MCU=SPIN05 CROSS="/c/Users/mikha/AppData/Local/Temp/opencode/arm-14.2/bin/arm-none-eabi-" clean both`
- flash: `python vendor\ALIFE-MM32\tools\mm32flash.py ...\build\spin05\hoverboard.bin`
- vendor pin: `vendor.lock` (`68f880d`, 2026-11-18)
- ESP32: PlatformIO `esp32s3_native_usb`, Arduino, native USB CDC

## Known-good binaries

| File | Size | What |
|---|---|---|
| `snapshots/hoverboard-2026-11-22.bin` | 31648 B | main firmware at this checkpoint |
| `snapshots/pinfinder-2026-11-22.bin` | 31744 B | PinFinder 2.0 |
| `before.bin` | 32768 B | original board dump (before any change) |

## Reproduce from zero

1. `scripts\fetch_vendor.ps1`
2. build + flash PinFinder, run `pf.py detect ...`, `pf.py --export board.json`
3. build + flash the main firmware
4. `python scripts\snapshot.py --restore config_snapshot.json --flash`
5. power-cycle, `python scripts\persist.py --show` -> compare with the table above
6. `python scripts\torque.py --torque 0.1 --torque -0.1 --seconds 2`

## Restore drill (2026-11-22)

- `--diff` right after the save: **0 of 113** changed
- `--restore` into RAM: **113 words**, `--diff` again **0 of 113**
- `--restore --flash`: page written; after a power cycle `--diff` = **0 of 113**
  (2026-11-22), so the config page really survives power-off
- `drivemode`, `timeout`, `batfull` are part of the page, so one restore
  reproduces the whole working state, not just the pin config
