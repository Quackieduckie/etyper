#!/usr/bin/env python3
"""
etyper - Minimal e-paper typewriter for Orange Pi Zero 2W.

Features:
  - Portrait mode (300x400 effective, display rotated 90 CCW)
  - Opens last document on startup
  - USB keyboard input via evdev
  - Autosave every 10 seconds
  - Partial refresh for fast typing (~0.5s per update)
  - Full refresh every 5 minutes (or Ctrl+R) to clean ghosting
  - Arrow key cursor movement, insert/delete at any position
  - Word wrap with monospace font

Keyboard shortcuts:
  Ctrl+N  - New document
  Ctrl+S  - Manual save
  Ctrl+R  - Force full refresh (clean ghosting)
  Ctrl+Q  - Quit

Usage:
  sudo python3 typewriter.py
"""

import os
import sys
import time
import signal
import select
import textwrap
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from epd42_driver import EPD42

# --- Configuration ---

DOCS_DIR = os.path.expanduser("~/etyper_docs")
LAST_DOC_FILE = os.path.join(DOCS_DIR, ".last_doc")
AUTOSAVE_INTERVAL = 10  # seconds

# Portrait dimensions (display is 400x300, rotated 90 CCW)
PORTRAIT_W = 300
PORTRAIT_H = 400

# Text layout
MARGIN_X = 10
MARGIN_Y = 8

# Font settings
FONT_SIZE = 16
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATHS = [
    os.path.join(SCRIPT_DIR, "fonts", "AtkinsonHyperlegibleMono-Medium.ttf"),
    os.path.join(SCRIPT_DIR, "fonts", "AtkinsonHyperlegibleMono-Regular.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
]

# --- Keyboard mapping (evdev keycodes -> characters) ---

try:
    from evdev import InputDevice, ecodes, list_devices
    HAS_EVDEV = True
except ImportError:
    HAS_EVDEV = False

# Basic US QWERTY keymap: keycode -> (normal, shifted)
KEYMAP = {
    ecodes.KEY_A: ("a", "A"), ecodes.KEY_B: ("b", "B"), ecodes.KEY_C: ("c", "C"),
    ecodes.KEY_D: ("d", "D"), ecodes.KEY_E: ("e", "E"), ecodes.KEY_F: ("f", "F"),
    ecodes.KEY_G: ("g", "G"), ecodes.KEY_H: ("h", "H"), ecodes.KEY_I: ("i", "I"),
    ecodes.KEY_J: ("j", "J"), ecodes.KEY_K: ("k", "K"), ecodes.KEY_L: ("l", "L"),
    ecodes.KEY_M: ("m", "M"), ecodes.KEY_N: ("n", "N"), ecodes.KEY_O: ("o", "O"),
    ecodes.KEY_P: ("p", "P"), ecodes.KEY_Q: ("q", "Q"), ecodes.KEY_R: ("r", "R"),
    ecodes.KEY_S: ("s", "S"), ecodes.KEY_T: ("t", "T"), ecodes.KEY_U: ("u", "U"),
    ecodes.KEY_V: ("v", "V"), ecodes.KEY_W: ("w", "W"), ecodes.KEY_X: ("x", "X"),
    ecodes.KEY_Y: ("y", "Y"), ecodes.KEY_Z: ("z", "Z"),
    ecodes.KEY_1: ("1", "!"), ecodes.KEY_2: ("2", "@"), ecodes.KEY_3: ("3", "#"),
    ecodes.KEY_4: ("4", "$"), ecodes.KEY_5: ("5", "%"), ecodes.KEY_6: ("6", "^"),
    ecodes.KEY_7: ("7", "&"), ecodes.KEY_8: ("8", "*"), ecodes.KEY_9: ("9", "("),
    ecodes.KEY_0: ("0", ")"),
    ecodes.KEY_MINUS: ("-", "_"), ecodes.KEY_EQUAL: ("=", "+"),
    ecodes.KEY_LEFTBRACE: ("[", "{"), ecodes.KEY_RIGHTBRACE: ("]", "}"),
    ecodes.KEY_SEMICOLON: (";", ":"), ecodes.KEY_APOSTROPHE: ("'", '"'),
    ecodes.KEY_GRAVE: ("`", "~"), ecodes.KEY_BACKSLASH: ("\\", "|"),
    ecodes.KEY_COMMA: (",", "<"), ecodes.KEY_DOT: (".", ">"),
    ecodes.KEY_SLASH: ("/", "?"),
    ecodes.KEY_SPACE: (" ", " "), ecodes.KEY_TAB: ("    ", "    "),
} if HAS_EVDEV else {}


class EtyperApp:
    """Main typewriter application with cursor movement."""

    def __init__(self):
        self.text = ""
        self.cursor = 0  # character index in self.text
        self.doc_path = None
        self.running = False
        self.dirty = False
        self.last_save_time = time.time()
        self.epd = None
        self.keyboard = None
        self.font = None
        self.shift_held = False
        self.ctrl_held = False
        self.chars_per_line = 30
        self.lines_per_page = 20
        self.needs_display_update = True
        self.scroll_offset = 0  # first visible wrapped-line index

    def _find_font(self):
        """Find a suitable monospace font."""
        for path in FONT_PATHS:
            if os.path.exists(path):
                return ImageFont.truetype(path, FONT_SIZE)
        return ImageFont.load_default()

    def _calc_text_metrics(self):
        """Calculate how many chars/lines fit on screen using proper font metrics."""
        ascent, descent = self.font.getmetrics()
        char_w = int(self.font.getlength("M"))

        usable_w = PORTRAIT_W - 2 * MARGIN_X
        usable_h = PORTRAIT_H - 2 * MARGIN_Y

        self.char_w = char_w
        self.cell_h = ascent + descent          # full character cell (22px)
        self.line_h = int(FONT_SIZE * 1.5)      # WCAG line height (24px)
        self.chars_per_line = max(1, usable_w // char_w)
        self.lines_per_page = max(1, usable_h // self.line_h) - 1  # reserve status bar

    def _find_keyboard(self):
        """Find a USB keyboard device via evdev."""
        if not HAS_EVDEV:
            return None

        devices = [InputDevice(path) for path in list_devices()]
        for dev in devices:
            caps = dev.capabilities(verbose=False)
            if ecodes.EV_KEY in caps:
                keys = caps[ecodes.EV_KEY]
                if ecodes.KEY_A in keys and ecodes.KEY_ENTER in keys:
                    print(f"Keyboard found: {dev.name} ({dev.path})")
                    return dev

        print("WARNING: No keyboard found. Waiting for connection...")
        return None

    # --- Document management ---

    def _ensure_docs_dir(self):
        os.makedirs(DOCS_DIR, exist_ok=True)

    def _get_last_doc_path(self):
        if os.path.exists(LAST_DOC_FILE):
            path = open(LAST_DOC_FILE).read().strip()
            if os.path.exists(path):
                return path
        return None

    def _set_last_doc(self, path):
        with open(LAST_DOC_FILE, "w") as f:
            f.write(path)

    def _new_doc_path(self):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(DOCS_DIR, f"doc_{ts}.txt")

    def load_document(self, path=None):
        self._ensure_docs_dir()

        if path and os.path.exists(path):
            self.doc_path = path
        else:
            self.doc_path = self._get_last_doc_path()

        if self.doc_path and os.path.exists(self.doc_path):
            with open(self.doc_path, "r") as f:
                self.text = f.read()
            print(f"Opened: {self.doc_path}")
        else:
            self.doc_path = self._new_doc_path()
            self.text = ""
            print(f"New document: {self.doc_path}")

        self.cursor = len(self.text)  # cursor at end
        self._set_last_doc(self.doc_path)
        self.dirty = False

    def save_document(self):
        if self.doc_path:
            with open(self.doc_path, "w") as f:
                f.write(self.text)
            self.dirty = False
            self.last_save_time = time.time()

    def new_document(self):
        self.save_document()
        self.doc_path = self._new_doc_path()
        self.text = ""
        self.cursor = 0
        self.scroll_offset = 0
        self._set_last_doc(self.doc_path)
        self.dirty = False
        self.needs_display_update = True

    def _list_docs(self):
        """Return sorted list of all .txt document paths in the docs directory."""
        self._ensure_docs_dir()
        docs = sorted(
            f for f in os.listdir(DOCS_DIR)
            if f.endswith(".txt") and f.startswith("doc_")
        )
        return [os.path.join(DOCS_DIR, f) for f in docs]

    def _switch_document(self, direction):
        """Switch to the next (+1) or previous (-1) document."""
        self.save_document()
        docs = self._list_docs()
        if not docs:
            return

        try:
            idx = docs.index(self.doc_path)
        except ValueError:
            idx = len(docs) - 1

        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(docs):
            return  # already at first/last document

        self.load_document(docs[new_idx])
        self.needs_display_update = True
        print(f"Switched to: {os.path.basename(self.doc_path)} "
              f"({new_idx + 1}/{len(docs)})")

    # --- Text wrapping with cursor tracking ---

    def _wrap_with_cursor(self):
        """Word-wrap text and track which wrapped line/column the cursor is on.

        Returns:
            (lines, cursor_line, cursor_col) where lines is a list of strings,
            cursor_line is the 0-based index into lines, cursor_col is the
            character offset within that line.
        """
        cpl = self.chars_per_line
        lines = []
        char_to_pos = {}
        text = self.text

        line_idx = 0
        para_start = 0
        paragraphs = text.split("\n")

        for p_idx, para in enumerate(paragraphs):
            if para == "":
                lines.append("")
                # Map the \n after this empty paragraph to start of this line
                if p_idx < len(paragraphs) - 1:
                    char_to_pos[para_start] = (line_idx, 0)
                line_idx += 1
                para_start += 1  # empty string + \n
                continue

            # Wrap this paragraph
            wrapped = textwrap.wrap(para, width=cpl,
                                    break_long_words=True,
                                    break_on_hyphens=False)
            if not wrapped:
                wrapped = [""]

            # Map character positions within paragraph to wrapped lines
            para_char = 0
            for w_line in wrapped:
                for col, ch in enumerate(w_line):
                    char_to_pos[para_start + para_char] = (line_idx, col)
                    para_char += 1
                # Account for the space that was consumed by wrapping
                if para_char < len(para) and para[para_char] == " ":
                    char_to_pos[para_start + para_char] = (line_idx, len(w_line))
                    para_char += 1
                lines.append(w_line)
                line_idx += 1

            # Map the \n that ends this paragraph (cursor at \n = end of line)
            if p_idx < len(paragraphs) - 1:
                newline_pos = para_start + len(para)
                char_to_pos[newline_pos] = (line_idx - 1, len(lines[-1]))

            para_start += len(para) + 1  # +1 for \n

        # Handle empty document
        if not lines:
            lines = [""]

        # Find cursor position
        if self.cursor >= len(text):
            # Cursor at end of text
            if lines:
                cursor_line = len(lines) - 1
                cursor_col = len(lines[-1])
            else:
                cursor_line = 0
                cursor_col = 0
        elif self.cursor in char_to_pos:
            cursor_line, cursor_col = char_to_pos[self.cursor]
        else:
            # Fallback: cursor at end
            cursor_line = len(lines) - 1
            cursor_col = len(lines[-1])

        return lines, cursor_line, cursor_col

    # --- Rendering ---

    def render(self):
        """Render the current text to a PIL Image in portrait orientation."""
        img = Image.new("1", (PORTRAIT_W, PORTRAIT_H), 255)
        draw = ImageDraw.Draw(img)

        lines, cursor_line, cursor_col = self._wrap_with_cursor()

        visible = self.lines_per_page

        # Auto-scroll to keep cursor visible
        if cursor_line < self.scroll_offset:
            self.scroll_offset = cursor_line
        elif cursor_line >= self.scroll_offset + visible:
            self.scroll_offset = cursor_line - visible + 1

        display_lines = lines[self.scroll_offset:self.scroll_offset + visible]

        # Draw text lines
        y = MARGIN_Y
        for line in display_lines:
            draw.text((MARGIN_X, y), line, font=self.font, fill=0)
            y += self.line_h

        # Draw cursor block (full cell height to cover ascenders and descenders)
        vis_cursor_line = cursor_line - self.scroll_offset
        if 0 <= vis_cursor_line < visible:
            cx = MARGIN_X + cursor_col * self.char_w
            cy = MARGIN_Y + vis_cursor_line * self.line_h

            if cx + self.char_w <= PORTRAIT_W - MARGIN_X:
                draw.rectangle(
                    [cx, cy, cx + self.char_w - 1, cy + self.cell_h - 1],
                    fill=0
                )
                # Draw the character under cursor in white (inverted)
                if cursor_line < len(lines) and cursor_col < len(lines[cursor_line]):
                    ch = lines[cursor_line][cursor_col]
                    draw.text((cx, cy), ch, font=self.font, fill=1)

        # Status bar
        status_y = PORTRAIT_H - MARGIN_Y - self.cell_h
        draw.line([(MARGIN_X, status_y - 2), (PORTRAIT_W - MARGIN_X, status_y - 2)], fill=0)

        doc_name = os.path.basename(self.doc_path) if self.doc_path else "untitled"
        save_indicator = "*" if self.dirty else ""
        line_num = cursor_line + 1
        col_num = cursor_col + 1
        status = f"{save_indicator}{doc_name} L{line_num}:{col_num} {len(self.text)}c"
        draw.text((MARGIN_X, status_y), status, font=self.font, fill=0)

        # Rotate for landscape display
        img_landscape = img.transpose(Image.Transpose.ROTATE_270)
        return img_landscape

    # --- Cursor movement helpers ---

    def _cursor_up(self):
        """Move cursor up one visual line."""
        lines, cur_line, cur_col = self._wrap_with_cursor()
        if cur_line == 0:
            return  # already at top

        target_line = cur_line - 1
        target_col = min(cur_col, len(lines[target_line]))
        self.cursor = self._pos_from_line_col(lines, target_line, target_col)

    def _cursor_down(self):
        """Move cursor down one visual line."""
        lines, cur_line, cur_col = self._wrap_with_cursor()
        if cur_line >= len(lines) - 1:
            return  # already at bottom

        target_line = cur_line + 1
        target_col = min(cur_col, len(lines[target_line]))
        self.cursor = self._pos_from_line_col(lines, target_line, target_col)

    def _pos_from_line_col(self, lines, target_line, target_col):
        """Convert a visual (line, col) back to a text character index."""
        # Rebuild the text position by walking through paragraphs and wrapping
        cpl = self.chars_per_line
        text = self.text

        line_idx = 0
        text_pos = 0

        for para in text.split("\n"):
            if para == "":
                if line_idx == target_line:
                    return text_pos + min(target_col, 0)
                line_idx += 1
                text_pos += 1  # the \n character
                continue

            wrapped = textwrap.wrap(para, width=cpl,
                                    break_long_words=True,
                                    break_on_hyphens=False)
            if not wrapped:
                wrapped = [""]

            para_char = 0
            for w_line in wrapped:
                if line_idx == target_line:
                    col = min(target_col, len(w_line))
                    return text_pos + para_char + col
                para_char += len(w_line)
                # Skip the space consumed by wrapping
                if para_char < len(para) and para[para_char] == " ":
                    para_char += 1
                line_idx += 1

            text_pos += len(para) + 1  # +1 for \n

        # Past end of text
        return len(text)

    # --- Keyboard input ---

    def _handle_key(self, keycode, value):
        """Process a keyboard event."""
        # Track modifier state
        if keycode in (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT):
            self.shift_held = value != 0
            return
        if keycode in (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL):
            self.ctrl_held = value != 0
            return

        # Only handle press and repeat
        if value == 0:
            return

        # Ctrl shortcuts
        if self.ctrl_held:
            if keycode == ecodes.KEY_Q:
                self.save_document()
                self._sleep_mode()
                return
            elif keycode == ecodes.KEY_S:
                self.save_document()
                self.needs_display_update = True
                return
            elif keycode == ecodes.KEY_N:
                self.new_document()
                return
            elif keycode == ecodes.KEY_R:
                # Force full refresh
                img = self.render()
                self.epd.full_refresh(list(img.tobytes()))
                self.needs_display_update = False
                return
            elif keycode == ecodes.KEY_LEFT:
                self._switch_document(-1)
                return
            elif keycode == ecodes.KEY_RIGHT:
                self._switch_document(+1)
                return

        # Arrow keys
        if keycode == ecodes.KEY_LEFT:
            if self.cursor > 0:
                self.cursor -= 1
                self.needs_display_update = True
            return

        if keycode == ecodes.KEY_RIGHT:
            if self.cursor < len(self.text):
                self.cursor += 1
                self.needs_display_update = True
            return

        if keycode == ecodes.KEY_UP:
            self._cursor_up()
            self.needs_display_update = True
            return

        if keycode == ecodes.KEY_DOWN:
            self._cursor_down()
            self.needs_display_update = True
            return

        if keycode == ecodes.KEY_HOME:
            # Move to start of current visual line
            lines, cur_line, _ = self._wrap_with_cursor()
            self.cursor = self._pos_from_line_col(lines, cur_line, 0)
            self.needs_display_update = True
            return

        if keycode == ecodes.KEY_END:
            # Move to end of current visual line
            lines, cur_line, _ = self._wrap_with_cursor()
            self.cursor = self._pos_from_line_col(lines, cur_line, len(lines[cur_line]))
            self.needs_display_update = True
            return

        # Enter
        if keycode == ecodes.KEY_ENTER:
            self.text = self.text[:self.cursor] + "\n" + self.text[self.cursor:]
            self.cursor += 1
            self.dirty = True
            self.needs_display_update = True
            return

        # Backspace
        if keycode == ecodes.KEY_BACKSPACE:
            if self.cursor > 0:
                self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
                self.cursor -= 1
                self.dirty = True
                self.needs_display_update = True
            return

        # Delete
        if keycode == ecodes.KEY_DELETE:
            if self.cursor < len(self.text):
                self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]
                self.dirty = True
                self.needs_display_update = True
            return

        # Regular characters - insert at cursor position
        if keycode in KEYMAP:
            normal, shifted = KEYMAP[keycode]
            char = shifted if self.shift_held else normal
            self.text = self.text[:self.cursor] + char + self.text[self.cursor:]
            self.cursor += len(char)
            self.dirty = True
            self.needs_display_update = True

    # --- Sleep / wake ---

    def _sleep_mode(self):
        """Save, show goodbye screen, put display to sleep, wait for Ctrl+Q to wake."""
        print("Entering sleep mode...")

        # Show goodbye screen
        if self.epd:
            try:
                self.epd.init()
                img = Image.new("1", (PORTRAIT_W, PORTRAIT_H), 255)
                draw = ImageDraw.Draw(img)
                draw.text((PORTRAIT_W // 2 - 70, PORTRAIT_H // 2 - 10),
                          "Saved. Goodbye.", font=self.font, fill=0)
                draw.text((PORTRAIT_W // 2 - 80, PORTRAIT_H // 2 + 20),
                          "Ctrl+Q to resume", font=self.font, fill=0)
                img_landscape = img.transpose(Image.Transpose.ROTATE_270)
                self.epd.display(list(img_landscape.tobytes()))
                self.epd.sleep()
            except Exception:
                pass

        # Wait for Ctrl+Q on keyboard
        print("Sleeping. Press Ctrl+Q to wake up...")
        self._wait_for_wake()

        # Wake up: reinitialize display and resume
        print("Waking up...")
        self.epd.init()
        img = self.render()
        self.epd.display(list(img.tobytes()))
        self.epd.init_partial()
        self.needs_display_update = False
        print("Resumed.")

    def _wait_for_wake(self):
        """Block until Ctrl+Q is pressed again on the keyboard."""
        ctrl_held = False
        while self.running:
            if self.keyboard is None:
                self.keyboard = self._find_keyboard()
                if self.keyboard is None:
                    time.sleep(1)
                    continue
            try:
                r, _, _ = select.select([self.keyboard.fd], [], [], 1.0)
                if not r:
                    continue
                for event in self.keyboard.read():
                    if event.type != ecodes.EV_KEY:
                        continue
                    if event.code in (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL):
                        ctrl_held = event.value != 0
                    elif event.code == ecodes.KEY_Q and event.value == 1 and ctrl_held:
                        return
            except OSError:
                self.keyboard = None
                time.sleep(1)

    # --- Main loop ---

    def run(self):
        """Start the typewriter."""
        print("=== etyper - E-Paper Typewriter ===")

        print("Initializing display...")
        self.epd = EPD42()

        self.font = self._find_font()
        self._calc_text_metrics()
        print(f"Text area: {self.chars_per_line} chars x {self.lines_per_page} lines")

        self.load_document()

        print("Initial display refresh...")
        self.epd.init()
        img = self.render()
        self.epd.display(list(img.tobytes()))
        self.epd.init_partial()

        self.keyboard = self._find_keyboard()

        self.running = True
        self.last_save_time = time.time()
        self.needs_display_update = False

        def signal_handler(sig, frame):
            self.running = False
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        print("Ready! Start typing...")
        print("  Arrows: move  |  Ctrl+S: save  |  Ctrl+N: new  |  Ctrl+R: refresh  |  Ctrl+Q: sleep/wake")

        try:
            self._main_loop()
        finally:
            self._shutdown()

    def _main_loop(self):
        """Event loop: read keyboard, update display, autosave."""
        while self.running:
            if self.keyboard is None:
                self.keyboard = self._find_keyboard()
                if self.keyboard is None:
                    time.sleep(1)
                    self._check_autosave()
                    continue

            try:
                r, _, _ = select.select([self.keyboard.fd], [], [], 0.5)

                if r:
                    for event in self.keyboard.read():
                        if event.type == ecodes.EV_KEY:
                            self._handle_key(event.code, event.value)

                if self.needs_display_update:
                    img = self.render()
                    self.epd.display_image_partial(img)
                    self.needs_display_update = False

                self._check_autosave()

            except OSError:
                print("Keyboard disconnected, waiting...")
                self.keyboard = None
                time.sleep(1)

    def _check_autosave(self):
        if self.dirty and (time.time() - self.last_save_time >= AUTOSAVE_INTERVAL):
            self.save_document()
            print(f"Autosaved: {self.doc_path}")

    def _shutdown(self):
        print("\nShutting down...")
        if self.dirty:
            self.save_document()
            print(f"Saved: {self.doc_path}")

        if self.epd:
            try:
                self.epd.init()
                img = Image.new("1", (PORTRAIT_W, PORTRAIT_H), 255)
                draw = ImageDraw.Draw(img)
                draw.text((PORTRAIT_W // 2 - 60, PORTRAIT_H // 2 - 10),
                          "Saved. Goodbye.", font=self.font, fill=0)
                img_landscape = img.transpose(Image.Transpose.ROTATE_270)
                self.epd.display(list(img_landscape.tobytes()))
                self.epd.sleep()
            except Exception:
                pass
            self.epd.close()

        print("Done.")


def main():
    if not HAS_EVDEV:
        print("ERROR: python3-evdev is required.")
        print("Install it: sudo apt-get install python3-evdev")
        sys.exit(1)

    app = EtyperApp()
    app.run()


if __name__ == "__main__":
    main()
