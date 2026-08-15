from __future__ import annotations

import argparse
import time
from pathlib import Path

import serial


def main() -> int:
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

    try:
        with serial.Serial(args.port, args.baud, timeout=0.2) as connection:
            connection.dtr = False
            connection.rts = False
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
        if output:
            output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
