#!/usr/bin/env python3
"""Generate the example trend figure."""

from _style import Canvas, COLORS


points = [0.21, 0.34, 0.38, 0.55, 0.63, 0.79]
canvas = Canvas()

left, right = 95, 830
top, bottom = 55, 440

for i in range(6):
    y = bottom - round((bottom - top) * i / 5)
    canvas.line(left, y, right, y, COLORS["grid"], width=1)

canvas.line(left, top, left, bottom, COLORS["axis"], width=3)
canvas.line(left, bottom, right, bottom, COLORS["axis"], width=3)

coords = []
for i, value in enumerate(points):
    x = left + round((right - left) * i / (len(points) - 1))
    y = bottom - round((bottom - top) * value)
    coords.append((x, y))

for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
    canvas.line(x1, y1, x2, y2, COLORS["primary"], width=5)

for x, y in coords:
    canvas.line(x - 7, y, x + 7, y, COLORS["primary"], width=5)
    canvas.line(x, y - 7, x, y + 7, COLORS["primary"], width=5)

canvas.save("figures/output/png/fig1_trend.png")
print("Generated figures/output/png/fig1_trend.png")
