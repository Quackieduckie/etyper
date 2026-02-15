#!/usr/bin/env python3
"""
test_patterns.py - Display test patterns to verify display connectivity.

Cycles through: all white, all black, top/bottom split, left/right split.
Useful for diagnosing wiring and data transfer issues.

Usage:
    python3 test_patterns.py
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from epd42_driver import EPD42


def main():
    with EPD42() as epd:
        W, H = epd.width, epd.height
        row_bytes = W // 8  # 50

        tests = [
            ("ALL WHITE", [0xFF] * (row_bytes * H)),
            ("ALL BLACK", [0x00] * (row_bytes * H)),
            ("TOP BLACK / BOTTOM WHITE",
             [0x00] * (row_bytes * (H // 2)) + [0xFF] * (row_bytes * (H // 2))),
            ("LEFT BLACK / RIGHT WHITE",
             sum([[0x00] * (row_bytes // 2) + [0xFF] * (row_bytes // 2)
                  for _ in range(H)], [])),
        ]

        for name, buf in tests:
            print(f"Test: {name}")
            epd.init()
            epd.display(buf)
            epd.sleep()
            time.sleep(5)

        print("All tests complete!")


if __name__ == "__main__":
    main()
