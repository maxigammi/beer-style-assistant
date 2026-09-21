"""
Рисует аватар бота: плоская иллюстрация стакана пейл-эля с пеной.
Запуск (нужен Pillow, в зависимости бота он не входит):  python assets/make_avatar.py
Результат: assets/avatar.png (640x640) и assets/avatar_preview.png (как Telegram обрежет кругом).
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent
SIZE = 640
SS = 4  # рисуем в 4 раза крупнее и уменьшаем: гладкие края без артефактов

# Палитра: плоские цвета, без градиентов
BG = "#17424D"        # тёмный сине-зелёный фон
BG_DISC = "#1D5361"   # чуть светлее круг за стаканом
SHADOW = "#0F2F38"
GLASS = "#E6F0F3"     # стекло
BEER = "#F0A122"      # пейл-эль: янтарно-золотой
BEER_LIGHT = "#F7BC4B"
BEER_DARK = "#D98A12"
BUBBLE = "#F9D082"
FOAM = "#FFF5DC"


def s(v):
    return int(round(v * SS))


def poly(d, pts, fill):
    d.polygon([(s(x), s(y)) for x, y in pts], fill=fill)


def circle(d, cx, cy, r, fill):
    d.ellipse([s(cx - r), s(cy - r), s(cx + r), s(cy + r)], fill=fill)


def rounded_bar(d, x0, y0, x1, y1, fill):
    """Прямоугольник со скруглёнными торцами (для бликов и потёков)."""
    r = (x1 - x0) / 2
    d.rounded_rectangle([s(x0), s(y0), s(x1), s(y1)], radius=s(r), fill=fill)


def build() -> Image.Image:
    img = Image.new("RGB", (s(SIZE), s(SIZE)), BG)
    d = ImageDraw.Draw(img)

    circle(d, 320, 320, 250, BG_DISC)

    # Тень под стаканом
    d.ellipse([s(205), s(492), s(435), s(522)], fill=SHADOW)

    # Стакан-«пинта»: сужается книзу; внешний контур, потом пиво внутри с толстым дном
    top_l, top_r, bot_l, bot_r = 200, 440, 232, 408
    y_top, y_bot = 168, 505
    poly(d, [(top_l, y_top), (top_r, y_top), (bot_r, y_bot - 8), (bot_r - 8, y_bot),
             (bot_l + 8, y_bot), (bot_l, y_bot - 8)], GLASS)

    # Пиво: те же наклонные стенки, отступ 9 по бокам и толстое стеклянное дно
    def edge(y, inset):  # x левой/правой стенки стакана на высоте y
        t = (y - y_top) / (y_bot - y_top)
        return top_l + (bot_l - top_l) * t + inset, top_r + (bot_r - top_r) * t - inset

    y_beer_top, y_beer_bot = 200, 478
    lt, rt = edge(y_beer_top, 9)
    lb, rb = edge(y_beer_bot, 9)
    poly(d, [(lt, y_beer_top), (rt, y_beer_top), (rb, y_beer_bot), (lb, y_beer_bot)], BEER)

    # Плоские тени и блик на пиве
    rl, rr = edge(y_beer_top, 9)[1], edge(y_beer_bot, 9)[1]
    poly(d, [(rl - 30, y_beer_top), (rl, y_beer_top), (rr, y_beer_bot), (rr - 22, y_beer_bot)], BEER_DARK)
    rounded_bar(d, 232, 250, 250, 440, BEER_LIGHT)

    # Пузырьки
    for cx, cy, r in [(300, 420, 7), (352, 385, 5), (318, 335, 6), (372, 300, 4), (290, 270, 4), (345, 445, 4)]:
        circle(d, cx, cy, r, BUBBLE)

    # Пена: шапка над краем стакана + тело внутри + «потёки» вниз
    l0, r0 = edge(y_top, 9)
    poly(d, [(l0, y_top), (r0, y_top), (rt, y_beer_top + 10), (lt, y_beer_top + 10)], FOAM)
    for cx, cy, r in [(222, 182, 21), (240, 164, 30), (286, 148, 36), (340, 144, 38), (392, 156, 33), (422, 180, 23)]:
        circle(d, cx, cy, r, FOAM)
    for cx, cy, r in [(262, 138, 9), (350, 122, 8)]:  # маленькие пузырьки пены сверху
        circle(d, cx, cy, r, FOAM)
    # потёки: скруглённые «язычки» пены на границе с пивом
    for cx, w, bottom in [(232, 26, 246), (282, 30, 232), (338, 24, 250), (388, 28, 236)]:
        rounded_bar(d, cx - w / 2, y_beer_top - 6, cx + w / 2, bottom, FOAM)

    return img.resize((SIZE, SIZE), Image.LANCZOS)


if __name__ == "__main__":
    avatar = build()
    avatar.save(OUT / "avatar.png")

    # Как увидит Telegram: аватар обрезается кругом
    mask = Image.new("L", (SIZE * 2, SIZE * 2), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, SIZE * 2 - 1, SIZE * 2 - 1], fill=255)
    mask = mask.resize((SIZE, SIZE), Image.LANCZOS)
    preview = Image.new("RGB", (SIZE, SIZE), "#FFFFFF")
    preview.paste(avatar, (0, 0), mask)
    preview.save(OUT / "avatar_preview.png")
    print("готово:", OUT / "avatar.png")
