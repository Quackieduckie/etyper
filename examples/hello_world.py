#!/usr/bin/env python3
"""
hello_world.py - Display "Hello World" on the WeAct 4.2" E-Paper.

Usage:
    python3 hello_world.py
"""

import sys
import os
import time

# Add parent dir to path so we can import the driver
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from epd42_driver import EPD42
from PIL import Image, ImageDraw, ImageFont


def main():
    with EPD42() as epd:
        # Step 1: Clear display
        print("Clearing display...")
        epd.init()
        epd.clear()
        epd.sleep()
        time.sleep(2)

        # Step 2: Draw and display image
        print("Displaying Hello World...")
        epd.init()

        img = Image.new("1", (epd.width, epd.height), 255)
        draw = ImageDraw.Draw(img)

        # Load fonts (Atkinson Hyperlegible Mono > DejaVu > default)
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        atkinson_bold = os.path.join(script_dir, "fonts",
                                     "AtkinsonHyperlegibleMono-Bold.ttf")
        atkinson_reg = os.path.join(script_dir, "fonts",
                                    "AtkinsonHyperlegibleMono-Regular.ttf")
        try:
            if os.path.exists(atkinson_bold):
                font_big = ImageFont.truetype(atkinson_bold, 36)
                font_med = ImageFont.truetype(atkinson_reg, 20)
                font_sm = ImageFont.truetype(atkinson_reg, 16)
            else:
                font_big = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
                font_med = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
                font_sm = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        except OSError:
            font_big = font_med = font_sm = ImageFont.load_default()

        # Draw content
        draw.rectangle([(5, 5), (394, 294)], outline=0, width=2)
        draw.text((30, 30), "Hello World!", font=font_big, fill=0)
        draw.line([(30, 80), (370, 80)], fill=0, width=2)
        draw.text((30, 100), "WeAct 4.2\" E-Paper", font=font_med, fill=0)
        draw.text((30, 130), "Orange Pi Zero 2W", font=font_med, fill=0)
        draw.text((30, 170), "SSD1683 - 400x300 px", font=font_sm, fill=0)
        draw.text((30, 195), "DC=Pin22, CS=Pin24", font=font_sm, fill=0)
        draw.text((30, 220), "gpiod + spidev on Armbian", font=font_sm, fill=0)

        # Little duck
        draw.text((300, 248), ">(.)__", font=font_sm, fill=0)
        draw.text((300, 266), " (___/", font=font_sm, fill=0)

        epd.display_image(img)
        epd.sleep()
        print("Done!")


if __name__ == "__main__":
    main()
