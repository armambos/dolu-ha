#!/usr/bin/env python3
"""Genera los iconos de marca de la integración a partir del logotipo de DoLu.

    python3 scripts/make_brand_icons.py

Escribe custom_components/dolu/brand/icon.png (256x256) e icon@2x.png (512x512), que son
los tamaños que exige el repositorio home-assistant/brands y que Home Assistant sirve desde
2026.3 para las integraciones personalizadas.

Por qué existe este script en vez de dos PNG sueltos en el repo: el icono se compone con los
trazos reales del logotipo (la "D" de "Do" en blanco y la "L" de "Lu" en dorado, que es el
monograma DL), copiados de public/dolu-logo.svg del backend. Teniéndolo así, el día que la
marca cambie se regenera en vez de repintarse a mano, y queda escrito de dónde salió cada
curva.

Sin dependencias a propósito: esta Raspberry no tiene rasterizador de SVG ni Pillow, y meter
uno para generar dos imágenes que casi nunca cambian no compensa. El rasterizado es un
scanline con regla non-zero y sobremuestreo de 4x4, y el PNG se escribe con zlib, que viene
en la biblioteca estándar.
"""

import re
import struct
import zlib
from pathlib import Path

# Colores de la marca, los mismos del tema del panel (public/dolu-theme.css).
FONDO = (0x0A, 0x0A, 0x0A, 255)
BLANCO = (0xFE, 0xFE, 0xFE, 255)
DORADO = (0xB5, 0x8D, 0x47, 255)

# Trazos tomados tal cual de public/dolu-logo.svg: el primero es la "D" y el tercero la "L".
# Cada uno trae su propia transformación, igual que en el SVG original.
GLIFOS = [
    {
        "color": BLANCO,
        "translate": (0.00, 86.40),
        "d": (
            "M693 351Q693 248 647.5 168.0Q602 88 518.5 44.0Q435 0 325 0H62V702H325"
            "Q436 702 519.0 658.0Q602 614 647.5 534.5Q693 455 693 351Z"
            "M519 351Q519 448 465.0 502.0Q411 556 314 556H233V148H314Q411 148 465.0 201.0"
            "Q519 254 519 351Z"
        ),
    },
    {
        "color": DORADO,
        "translate": (181.56, 86.40),
        "d": "M233 132H457V0H62V702H233Z",
    },
]

ESCALA = (0.120000, -0.120000)  # la del SVG: el eje Y de los glifos va al revés
CURVA_PASOS = 24                 # en cuántos segmentos se parte cada Bézier cuadrática
SUPERMUESTREO = 4                # 4x4 muestras por píxel
MARGEN = 0.13                    # aire alrededor del monograma, en fracción del lado
SEPARACION = 0.10                # hueco entre la D y la L, en fracción del ancho total
RADIO_ESQUINA = 0.22             # esquinas redondeadas del fondo, en fracción del lado


def parsear(d, translate):
    """Los subtrazos de un path como listas de puntos ya transformados."""
    tx, ty = translate
    sx, sy = ESCALA
    fichas = re.findall(r"([MQHVLZ])([^MQHVLZ]*)", d, re.IGNORECASE)

    subtrazos, actual = [], []
    x = y = 0.0
    inicio = (0.0, 0.0)

    def punto(px, py):
        return (tx + sx * px, ty + sy * py)

    for comando, crudo in fichas:
        numeros = [float(n) for n in re.findall(r"-?\d+\.?\d*", crudo)]

        if comando == "M":
            if len(actual) > 1:
                subtrazos.append(actual)
            x, y = numeros[0], numeros[1]
            inicio = (x, y)
            actual = [punto(x, y)]
        elif comando == "L":
            x, y = numeros[0], numeros[1]
            actual.append(punto(x, y))
        elif comando == "H":
            x = numeros[0]
            actual.append(punto(x, y))
        elif comando == "V":
            y = numeros[0]
            actual.append(punto(x, y))
        elif comando == "Q":
            # Bézier cuadrática: se aplana en segmentos rectos, que es todo lo que necesita
            # un rasterizador de polígonos.
            for i in range(0, len(numeros), 4):
                cx, cy, fx, fy = numeros[i : i + 4]
                x0, y0 = x, y
                for paso in range(1, CURVA_PASOS + 1):
                    t = paso / CURVA_PASOS
                    u = 1 - t
                    px = u * u * x0 + 2 * u * t * cx + t * t * fx
                    py = u * u * y0 + 2 * u * t * cy + t * t * fy
                    actual.append(punto(px, py))
                x, y = fx, fy
        elif comando in "Zz":
            if len(actual) > 1:
                actual.append(punto(*inicio))
                subtrazos.append(actual)
            actual = []
            x, y = inicio

    if len(actual) > 1:
        subtrazos.append(actual)
    return subtrazos


def caja(subtrazos):
    puntos = [p for sub in subtrazos for p in sub]
    xs = [p[0] for p in puntos]
    ys = [p[1] for p in puntos]
    return min(xs), min(ys), max(xs), max(ys)


def transformar(subtrazos, escala, dx, dy):
    return [[(p[0] * escala + dx, p[1] * escala + dy) for p in sub] for sub in subtrazos]


def componer(lado):
    """Los dos glifos colocados uno al lado del otro y encajados en el lienzo."""
    glifos = [(g["color"], parsear(g["d"], g["translate"])) for g in GLIFOS]

    (dx0, dy0, dx1, dy1) = caja(glifos[0][1])
    (lx0, ly0, lx1, ly1) = caja(glifos[1][1])

    # La L se pega a la D con un hueco proporcional: en el logotipo completo las separan la
    # "o" y la nada, y aquí sobra todo ese espacio.
    hueco = (dx1 - dx0) * SEPARACION
    corrimiento = (dx1 + hueco) - lx0
    glifos[1] = (glifos[1][0], transformar(glifos[1][1], 1.0, corrimiento, 0.0))

    x0 = dx0
    x1 = lx1 + corrimiento
    y0 = min(dy0, ly0)
    y1 = max(dy1, ly1)

    util = lado * (1 - 2 * MARGEN)
    escala = min(util / (x1 - x0), util / (y1 - y0))
    dx = (lado - (x1 - x0) * escala) / 2 - x0 * escala
    dy = (lado - (y1 - y0) * escala) / 2 - y0 * escala

    return [(color, transformar(subs, escala, dx, dy)) for color, subs in glifos]


def cobertura(subtrazos, lado):
    """Cobertura por píxel (0..1) con regla non-zero y sobremuestreo."""
    n = SUPERMUESTREO
    acumulado = [[0] * lado for _ in range(lado)]

    aristas = []
    for sub in subtrazos:
        for (ax, ay), (bx, by) in zip(sub, sub[1:]):
            if ay != by:
                aristas.append((ax, ay, bx, by))

    for fila_sub in range(lado * n):
        y = (fila_sub + 0.5) / n
        cruces = []
        for ax, ay, bx, by in aristas:
            if (ay <= y < by) or (by <= y < ay):
                t = (y - ay) / (by - ay)
                # +1 o -1 según el sentido: es lo que distingue el contorno del hueco de la D.
                cruces.append((ax + t * (bx - ax), 1 if by > ay else -1))
        if not cruces:
            continue
        cruces.sort()

        fila = acumulado[fila_sub // n]
        devanado = 0
        for i in range(len(cruces) - 1):
            devanado += cruces[i][1]
            if devanado == 0:
                continue
            desde, hasta = cruces[i][0], cruces[i + 1][0]
            for col_sub in range(max(0, int(desde * n)), min(lado * n, int(hasta * n) + 1)):
                x = (col_sub + 0.5) / n
                if desde <= x < hasta:
                    fila[col_sub // n] += 1

    total = n * n
    return [[min(1.0, v / total) for v in f] for f in acumulado]


def fondo_redondeado(lado):
    """Máscara del cuadrado de fondo con las esquinas redondeadas."""
    radio = lado * RADIO_ESQUINA
    mascara = [[0.0] * lado for _ in range(lado)]
    n = SUPERMUESTREO
    for fy in range(lado):
        for fx in range(lado):
            dentro = 0
            for sy in range(n):
                for sx in range(n):
                    x = fx + (sx + 0.5) / n
                    y = fy + (sy + 0.5) / n
                    cx = min(max(x, radio), lado - radio)
                    cy = min(max(y, radio), lado - radio)
                    if (x - cx) ** 2 + (y - cy) ** 2 <= radio * radio:
                        dentro += 1
            mascara[fy][fx] = dentro / (n * n)
    return mascara


def mezclar(base, color, alfa):
    return tuple(round(base[i] * (1 - alfa) + color[i] * alfa) for i in range(4))


def render(lado):
    capas = componer(lado)
    mascara = fondo_redondeado(lado)
    pixeles = [[(0, 0, 0, 0)] * lado for _ in range(lado)]

    for y in range(lado):
        for x in range(lado):
            a = mascara[y][x]
            if a:
                pixeles[y][x] = (FONDO[0], FONDO[1], FONDO[2], round(255 * a))

    for color, subs in capas:
        cob = cobertura(subs, lado)
        for y in range(lado):
            for x in range(lado):
                a = cob[y][x]
                if a:
                    pixeles[y][x] = mezclar(pixeles[y][x], color, a)

    return pixeles


def escribir_png(ruta, pixeles):
    lado = len(pixeles)
    crudo = b"".join(
        b"\x00" + b"".join(struct.pack("4B", *p) for p in fila) for fila in pixeles
    )

    def trozo(tipo, datos):
        return (
            struct.pack(">I", len(datos))
            + tipo
            + datos
            + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + trozo(b"IHDR", struct.pack(">IIBBBBB", lado, lado, 8, 6, 0, 0, 0))
        + trozo(b"IDAT", zlib.compress(crudo, 9))
        + trozo(b"IEND", b"")
    )
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(png)
    print(f"{ruta} · {lado}x{lado} · {len(png)} bytes")


if __name__ == "__main__":
    destino = Path(__file__).resolve().parent.parent / "custom_components" / "dolu" / "brand"
    escribir_png(destino / "icon.png", render(256))
    escribir_png(destino / "icon@2x.png", render(512))
