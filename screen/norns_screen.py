"""Shows the norns display on the MK3's right LCD.

matron publishes each finished frame to /dev/shm/norns-screen. This reader only looks at
that memory, so a stopped or missing norns can never disturb Machinista.
"""
import mmap
import struct
from PIL import Image

PATH = '/dev/shm/norns-screen'
HEADER = 16
WIDTH, HEIGHT, SCALE = 128, 64, 3
TOP, LEFT = 50, (480 - WIDTH * SCALE) // 2
LABELS_HEIGHT = 40  # the display-button labels stay visible above the norns screen


class NornsScreen:
    def __init__(self):
        self.memory = None
        self.seq = None
        self.frame = None

    def read(self):
        if self.memory is None:
            try:
                with open(PATH, 'rb') as file:
                    self.memory = mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ)
            except (OSError, ValueError):
                return None
        first, width, height, stride = struct.unpack_from('<4I', self.memory, 0)
        if first & 1 or first == 0 or (width, height) != (WIDTH, HEIGHT):
            return self.frame
        if first == self.seq:
            return self.frame
        pixels = self.memory[HEADER:HEADER + stride * height]
        if struct.unpack_from('<I', self.memory, 0)[0] != first:
            return self.frame  # norns wrote during the copy; the next refresh gets it
        image = Image.frombuffer('RGBA', (width, height), pixels, 'raw', 'BGRA', stride, 1).convert('RGB')
        self.frame = image.resize((width * SCALE, height * SCALE), Image.NEAREST)
        self.seq = first
        return self.frame


screen = NornsScreen()


def overlay(images):
    frame = screen.read()
    if frame is None:
        return images
    right = images[1].copy()
    right.paste((0, 0, 0), (0, LABELS_HEIGHT, 480, 272))
    right.paste(frame, (LEFT, TOP))
    return images[0], right
