import curses
import logging
import os
import select
import sys
import time
from typing import Any, List

from . import bagl
from .display import Display, DisplayNotifier, FrameBuffer, MODELS
from .readerror import ReadError
from .struct import DisplayArgs, ServerArgs

wait_time = 0.01

BUTTON_LEFT = 1
BUTTON_RIGHT = 2

_TEXT_ = "\033[36;40m"
_BORDER_ = "\033[30;1;40m"
_RESET_COLOR = "\033[0m"

M: List = [0]*16
M[0b0000] = ' '
M[0b0001] = '\u2598'
M[0b0010] = '\u259D'
M[0b0011] = '\u2580'
M[0b0100] = '\u2596'
M[0b0101] = '\u258C'
M[0b0110] = '\u259E'
M[0b0111] = '\u259B'
M[0b1000] = '\u2597'
M[0b1001] = '\u259A'
M[0b1010] = '\u2590'
M[0b1011] = '\u259C'
M[0b1100] = '\u2584'
M[0b1101] = '\u2599'
M[0b1110] = '\u259F'
M[0b1111] = '\u2588'


# a b
# c d
def map_pix(a, b, c, d):
    return M[d << 3 | c << 2 | b << 1 | a]


class TextWidget(FrameBuffer):
    def __init__(self, parent, model: str):
        super().__init__(model)
        self.width = parent.width
        self.height = parent.height
        self.previous_screen = 0

        # ncurses stops the process if in the background
        if os.tcgetpgrp(sys.stdin.fileno()) != os.getpgrp():
            logging.getLogger("display").warn("please run speculos in the foreground to allow the initialization of "
                                              "the display")

        self.stdscr = curses.initscr()
        curses.noecho()
        curses.cbreak()
        curses.start_color()
        curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)
        self.stdscr.nodelay(True)  # returns -1 if nothing
        self.stdscr.clear()
        self.stdscr.keypad(True)  # interpret escape sequences generated by keypad and function keys

    def get_pixel(self, x, y):
        color = self.pixels.get((x, y), 0)
        return int(color != 0)

    def update(self):
        if self.pixels != self.previous_screen:
            self._redraw()
            self.previous_screen = self.pixels.copy()
            return True
        return False

    def _redraw(self):
        self.stdscr.clear()
        for i in range(0, self.height, 2):
            line = []
            for j in range(0, self.width-2, 2):
                a = self.get_pixel(j, i)
                b = self.get_pixel(j+1, i)
                c = self.get_pixel(j, i+1)
                d = self.get_pixel(j+1, i+1)
                line.append(map_pix(a, b, c, d))

            self.stdscr.addstr(1 + i//2, 0, ' ', curses.color_pair(2))
            self.stdscr.addstr(1 + i//2, 1, ''.join(line), curses.color_pair(1))
            self.stdscr.addstr(1 + i//2, self.width//2 + 1, ' ', curses.color_pair(2))

        self.stdscr.addstr(0, 0, ' '*(self.width//2 + 2), curses.color_pair(2))
        self.stdscr.addstr(self.height//2+1, 0, ' '*(self.width//2 + 2), curses.color_pair(2))
        self.stdscr.refresh()
        self.update_screenshot()


class TextScreen(Display):
    def __init__(self, display_args: DisplayArgs, server_args: ServerArgs) -> None:
        super().__init__(display_args, server_args)

        self.width, self.height = MODELS[display_args.model].screen_size
        self.m = TextWidget(self, display_args.model)
        if self.use_bagl:
            self._gl = bagl.Bagl(self.m, MODELS[display_args.model].screen_size, display_args.model)
        else:
            raise NotImplementedError("This display can not emulate NBGL OS yet")

        if display_args.keymap is not None:
            self.ARROW_KEYS = list(map(ord, display_args.keymap))
        else:
            self.ARROW_KEYS = [curses.KEY_LEFT, curses.KEY_RIGHT, curses.KEY_DOWN]

        self.key2btn = {
                            self.ARROW_KEYS[0]: BUTTON_LEFT,
                            self.ARROW_KEYS[1]: BUTTON_RIGHT,
                            self.ARROW_KEYS[2]: BUTTON_LEFT | BUTTON_RIGHT,
                        }

    @property
    def gl(self) -> bagl.Bagl:
        return self._gl

    def display_status(self, data):
        return self.gl.display_status(data)

    def display_raw_status(self, data) -> None:
        self.gl.display_raw_status(data)

    def screen_update(self) -> bool:
        return self.gl.refresh()

    def get_keypress(self) -> bool:
        key = self.m.stdscr.getch()
        if key == -1:
            return True
        elif key in self.ARROW_KEYS:
            self.seph.handle_button(self.key2btn[key], True)
            time.sleep(wait_time)
            self.seph.handle_button(self.key2btn[key], False)
            return True
        elif key == ord('q'):
            return False
        else:
            return True


class TextScreenNotifier(DisplayNotifier):

    def __init__(self, display_args: DisplayArgs, server_args: ServerArgs) -> None:
        super().__init__(display_args, server_args)
        self._set_display_class(TextScreen)

    def run(self) -> None:
        while True:
            rlist: List[Any] = list(self.notifiers.keys())
            if not rlist:
                break

            rlist += [sys.stdin]
            rlist, _, _ = select.select(rlist, [], [])
            if sys.stdin in rlist:
                rlist.remove(sys.stdin)
                assert isinstance(self.display, TextScreen)
                if not self.display.get_keypress():
                    break
            try:
                for fd in rlist:
                    self.notifiers[fd].can_read(self)

            # This exception occur when can_read have no more data available
            except ReadError:
                break

        curses.nocbreak()
        curses.echo()
        curses.endwin()
