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


PANEL_TOP, PANEL_BOTTOM = 56, 214  # between the group row and the encoder footer
ROW_COLORS = ('#00dcff', '#bc63ff', '#ffda00', '#25ee77')  # one per encoder, as Machinista does


def panel(image, info):
    """Draws the norns PARAMS page (or the load page) on the left display."""
    from PIL import ImageDraw
    from app.mk3_graphics import BG, PANEL, INK, MUTED, ACCENT, font
    from app.mk3_theme import resolve, active
    scheme = active()

    def color(value):
        return resolve(value, scheme)

    def text(x, y, value, size, bold=False, fill=INK, width=None, right=False):
        f = font(size, bold)
        value = str(value)
        while width and value and f.getlength(value) > width:
            value = value[:-1]
        if right:
            x -= f.getlength(value)
        draw.text((x, y), value, font=f, fill=color(fill))

    image = image.copy()
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, PANEL_TOP, 479, PANEL_BOTTOM), fill=color(BG))
    draw.rectangle((10, 59, 13, 83), fill=color(ACCENT))
    text(21, 61, 'NORNS / ' + info['title'], 17, True, width=360)
    if info['pages']:
        page = 'LOAD' if info['page'] == 0 else f"{info['page']}/{info['pages']}"
        text(470, 65, page, 11, fill=MUTED, right=True)
    if info['status']:
        text(21, 120, info['status'], 15, True, fill=MUTED, width=440)
    if info['page'] == 0:
        for i, (label, value, _) in enumerate(info['rows'][:2]):
            y = 92 + i * 52
            text(20, y, label, 12, fill=ROW_COLORS[i])
            text(20, y + 17, value, 21, True, width=440)
        text(20, 196, info['hint'], 11, fill=MUTED)
        return image
    for i, (label, value, fraction) in enumerate(info['rows'][:4]):
        y = 92 + i * 30
        text(15, y, str(i + 1), 10, True, fill=ROW_COLORS[i])
        text(32, y, label, 13, width=250)
        text(465, y, value, 13, True, fill=MUTED, width=170, right=True)
        draw.rectangle((15, y + 21, 465, y + 23), fill=color(PANEL))
        if fraction >= 0:
            end = 15 + round(450 * max(0.0, min(1.0, fraction)))
            if end > 15:
                draw.rectangle((15, y + 21, end, y + 23), fill=color(ROW_COLORS[i]))
    return image


def overlay(images, state=None):
    left, right = images[0], images[1]
    info = (state or {}).get('norns_panel')
    if info:
        left = panel(left, info)
    frame = screen.read()
    if frame is not None:
        right = right.copy()
        right.paste((0, 0, 0), (0, LABELS_HEIGHT, 480, 272))
        right.paste(frame, (LEFT, TOP))
    return left, right
