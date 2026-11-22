# ESP32-S3 USB <-> UART1 bridge

The firmware that was running at the 42 V checkpoint. PlatformIO, Arduino
framework, native USB CDC (`esp32s3_native_usb`).

- UART1 RX = **GPIO16**, TX = **GPIO17**, 115200 8N1
- byte-for-byte bridge: USB -> UART1 and UART1 -> USB, no protocol awareness
- opening the COM port resets the chip, so the PC side waits ~2 s before the
  first byte

```powershell
pio run -t upload
pio device monitor
```

It is deliberately dumb: the PC speaks CANSimple to the MM32 through it. It
will be replaced by a composite CDC + HID device when the wheel position has
to reach the game as an axis; this copy is the known-good fallback.
