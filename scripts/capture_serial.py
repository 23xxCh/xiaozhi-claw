from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import serial


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")

    parser = argparse.ArgumentParser(description="Capture ESP32 serial output for a fixed time")
    parser.add_argument("--port", default="COM6")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    deadline = time.monotonic() + args.seconds
    output = None
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        output = args.output.open("w", encoding="utf-8", newline="")

    connection = serial.Serial(baudrate=args.baud, timeout=0.2, port=None)
    connection.dtr = False
    connection.rts = False
    connection.port = args.port
    try:
        # Set DTR/RTS before opening the port. Opening COM6 with pyserial's
        # default line state can otherwise reset the ESP32 and corrupt the
        # very runtime evidence this diagnostic tool is meant to capture.
        connection.open()
        if args.reset:
            connection.rts = True
            time.sleep(0.1)
            connection.rts = False
        while time.monotonic() < deadline:
            data = connection.readline()
            if data:
                text = data.decode("utf-8", errors="backslashreplace")
                print(text, end="", flush=True)
                if output:
                    output.write(text)
                    output.flush()
    finally:
        if connection.is_open:
            connection.close()
        if output:
            output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
