"""Generate the PWA icons. Android will not offer 'Install app' without a
192px and a 512px PNG, and it crops a non-maskable icon into a circle, so a
maskable variant with a safe centre is generated too."""

from pathlib import Path
from PIL import Image, ImageDraw

# Dark teal, so the shared board is never mistaken for a personal list on
# the home screen.
BG = (13, 59, 55)
LINE = (232, 245, 243)
FLAG = (251, 191, 36)
OUT = Path(__file__).resolve().parent.parent / "icons"


def draw_mark(size: int, inset: float) -> Image.Image:
    """A three-row list; the top row carries the flag triangle."""
    img = Image.new("RGBA", (size, size), BG + (255,))
    d = ImageDraw.Draw(img)

    pad = size * inset
    span = size - 2 * pad
    row_h = span * 0.11
    gap = (span - 3 * row_h) / 2
    tri = row_h * 1.5
    text_x = pad + tri + span * 0.10

    for i in range(3):
        y = pad + i * (row_h + gap)
        if i == 0:
            d.polygon(
                [(pad, y + row_h), (pad + tri / 2, y - row_h * 0.35), (pad + tri, y + row_h)],
                fill=FLAG,
            )
        else:
            r = row_h / 2
            cx = pad + tri / 2
            d.ellipse([cx - r, y, cx + r, y + row_h], fill=LINE + (110,))
        d.rounded_rectangle(
            [text_x, y, pad + span, y + row_h],
            radius=row_h / 2,
            fill=LINE + (255 if i == 0 else 150,),
        )
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # 0.18 inset for normal icons; 0.28 keeps the maskable art inside the
    # circle Android crops to.
    for name, size, inset in [
        ("icon-192.png", 192, 0.18),
        ("icon-512.png", 512, 0.18),
        ("icon-maskable-512.png", 512, 0.28),
    ]:
        img = draw_mark(size * 4, inset).resize((size, size), Image.LANCZOS)
        img.save(OUT / name)
        print(f"wrote {OUT / name} ({size}x{size})")


if __name__ == "__main__":
    main()
