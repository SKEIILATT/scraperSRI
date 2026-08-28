"""
Utilidades para humanizar interacciones con el navegador y reducir deteccion de bot.
"""

import asyncio
import math
import random

from playwright.async_api import Page


async def pausa(min_ms: int = 200, max_ms: int = 800) -> None:
    """Espera aleatoria entre acciones para simular comportamiento humano."""
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


def _bezier_punto(t: float, p0, p1, p2, p3) -> tuple[float, float]:
    """Punto sobre una curva Bezier cubica en t ∈ [0,1]."""
    u = 1 - t
    x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
    y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
    return x, y


async def _mover_bezier(
    page: Page,
    x0: float, y0: float,
    x1: float, y1: float,
    pasos: int = 30,
    delay_ms_min: int = 8,
    delay_ms_max: int = 22,
) -> None:
    """
    Mueve el mouse de (x0,y0) a (x1,y1) siguiendo una curva Bezier cubica
    con puntos de control aleatorios. Simula el trazo curvo del mouse humano.
    """
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)

    # Puntos de control desviados perpendicularmente — mayor desviacion en distancias largas
    desv = min(dist * 0.35, 120) * random.uniform(0.4, 1.0)
    angulo_perp = math.atan2(dy, dx) + math.pi / 2

    cp1 = (
        x0 + dx * random.uniform(0.2, 0.4) + math.cos(angulo_perp) * desv * random.choice([1, -1]),
        y0 + dy * random.uniform(0.2, 0.4) + math.sin(angulo_perp) * desv * random.choice([1, -1]),
    )
    cp2 = (
        x0 + dx * random.uniform(0.6, 0.8) + math.cos(angulo_perp) * desv * 0.5 * random.choice([1, -1]),
        y0 + dy * random.uniform(0.6, 0.8) + math.sin(angulo_perp) * desv * 0.5 * random.choice([1, -1]),
    )

    for i in range(pasos + 1):
        t = i / pasos
        # Ease-in-out suave: acelera al inicio, decelera al final
        t_suave = t * t * (3 - 2 * t)
        px, py = _bezier_punto(t_suave, (x0, y0), cp1, cp2, (x1, y1))
        await page.mouse.move(px, py)
        if i < pasos:
            await asyncio.sleep(random.uniform(delay_ms_min, delay_ms_max) / 1000)


async def mover_y_click(page: Page, selector: str, rapido: bool = False) -> None:
    """
    Mueve el mouse hacia el elemento con curva Bezier antes de hacer click.

    `rapido=True` acorta la curva y las pausas: solo debe usarse para clicks que
    NO son evaluados por reCAPTCHA Enterprise (ej. descargar XML/PDF por fila,
    que ocurren dentro de una sesion ya autenticada y con score ya establecido).
    Los submits de login y de aplicar_filtros deben seguir usando el modo
    humanizado completo (rapido=False, el default) porque esos SI son los
    formularios que reCAPTCHA puntua.
    """
    elemento = page.locator(selector)
    await elemento.scroll_into_view_if_needed()
    box = await elemento.bounding_box()
    if box:
        pos_actual = await page.evaluate("() => ({ x: window._mouseX || 100, y: window._mouseY || 100 })")
        x1 = box["x"] + random.uniform(box["width"] * 0.25, box["width"] * 0.75)
        y1 = box["y"] + random.uniform(box["height"] * 0.25, box["height"] * 0.75)
        if rapido:
            pasos = random.randint(8, 14)
            delay_min, delay_max = 4, 10
        else:
            pasos = random.randint(25, 45)
            delay_min, delay_max = 8, 22
        await _mover_bezier(
            page,
            pos_actual.get("x", 100), pos_actual.get("y", 100),
            x1, y1,
            pasos=pasos,
            delay_ms_min=delay_min,
            delay_ms_max=delay_max,
        )
        await pausa(20, 60) if rapido else await pausa(80, 220)
    await elemento.click()


async def click_directo(page: Page, selector: str) -> None:
    """
    Click sin curva de movimiento ni pausas: solo scrollea el elemento a la
    vista y clickea. Usar UNICAMENTE para acciones dentro de una sesion ya
    autenticada que reCAPTCHA no puntua (descarga de reporte/XML/PDF por
    fila) — ahi el score de la sesion ya quedo establecido en el login y en
    aplicar_filtros, asi que simular movimiento humano solo suma tiempo sin
    ningun beneficio.
    """
    elemento = page.locator(selector)
    await elemento.scroll_into_view_if_needed()
    await elemento.click()


async def acercarse_lento(page: Page, selector: str) -> None:
    """
    Mueve el mouse hacia el elemento con curva Bezier lenta y deliberada.
    No hace click — simula el usuario leyendo el boton antes de pulsar.
    """
    elemento = page.locator(selector)
    await elemento.scroll_into_view_if_needed()
    box = await elemento.bounding_box()
    if not box:
        return

    viewport = page.viewport_size or {"width": 1366, "height": 768}
    origen_x = random.uniform(viewport["width"] * 0.1, viewport["width"] * 0.9)
    origen_y = random.uniform(viewport["height"] * 0.3, viewport["height"] * 0.8)

    destino_x = box["x"] + random.uniform(box["width"] * 0.3, box["width"] * 0.7)
    destino_y = box["y"] + random.uniform(box["height"] * 0.3, box["height"] * 0.7)

    # Primera fase: aproximarse a la zona del boton (velocidad media)
    mid_x = destino_x + random.uniform(-70, 70)
    mid_y = destino_y + random.uniform(-40, 40)
    await _mover_bezier(page, origen_x, origen_y, mid_x, mid_y, pasos=random.randint(30, 50), delay_ms_min=12, delay_ms_max=28)
    await pausa(350, 800)

    # Segunda fase: acercarse al boton despacio (como si lo leyera)
    await _mover_bezier(page, mid_x, mid_y, destino_x, destino_y, pasos=random.randint(15, 25), delay_ms_min=20, delay_ms_max=45)
    await pausa(500, 1200)


async def escribir_humanizado(page: Page, selector: str, texto: str) -> None:
    """Escribe texto caracter por caracter con delay variable, como un humano."""
    await mover_y_click(page, selector)
    await pausa(150, 400)
    await page.locator(selector).fill("")
    await page.locator(selector).type(texto, delay=random.uniform(60, 140))


async def seleccionar_opcion(page: Page, selector: str, valor: str) -> None:
    """Selecciona una opcion en un <select> con pausa previa."""
    await mover_y_click(page, selector)
    await pausa(200, 500)
    await page.locator(selector).select_option(valor)
    await pausa(150, 350)


async def simular_lectura(page: Page, segundos_min: float = 3.0, segundos_max: float = 6.0) -> None:
    """
    Simula que un usuario lee la pagina: movimientos Bezier naturales y scroll suave.
    Llamar despues de que la pagina carga y antes de interactuar con el formulario.
    """
    viewport = page.viewport_size or {"width": 1366, "height": 768}
    w, h = viewport["width"], viewport["height"]

    tiempo_total = random.uniform(segundos_min, segundos_max)
    t_inicio = asyncio.get_event_loop().time()

    pos_x = random.uniform(w * 0.2, w * 0.8)
    pos_y = random.uniform(h * 0.2, h * 0.7)

    while asyncio.get_event_loop().time() - t_inicio < tiempo_total:
        nuevo_x = random.uniform(w * 0.05, w * 0.95)
        nuevo_y = random.uniform(h * 0.05, h * 0.90)
        pasos = random.randint(20, 40)
        await _mover_bezier(page, pos_x, pos_y, nuevo_x, nuevo_y, pasos=pasos, delay_ms_min=10, delay_ms_max=30)
        pos_x, pos_y = nuevo_x, nuevo_y
        await pausa(300, 900)

        if random.random() < 0.33:
            delta = random.randint(30, 120) * random.choice([1, -1])
            await page.mouse.wheel(0, delta)
            await pausa(250, 600)


async def simular_lectura_formulario(page: Page, selectores_campos: list[str]) -> None:
    """
    Simula que el usuario lee y revisa los campos del formulario antes de interactuar:
    mueve el mouse sobre las etiquetas y campos sin clickear.
    Mejora el score reCAPTCHA al mostrar intencion natural antes del submit.
    """
    viewport = page.viewport_size or {"width": 1366, "height": 768}
    w, h = viewport["width"], viewport["height"]

    pos_x = random.uniform(w * 0.3, w * 0.7)
    pos_y = random.uniform(h * 0.4, h * 0.7)

    for selector in selectores_campos:
        try:
            elemento = page.locator(selector)
            box = await elemento.bounding_box()
            if not box:
                continue
            x = box["x"] + random.uniform(-30, box["width"] + 30)
            y = box["y"] + random.uniform(-15, box["height"] + 15)
            x = max(10, min(w - 10, x))
            y = max(10, min(h - 10, y))
            await _mover_bezier(page, pos_x, pos_y, x, y, pasos=random.randint(18, 32), delay_ms_min=12, delay_ms_max=30)
            pos_x, pos_y = x, y
            await pausa(500, 1300)
        except Exception:
            pass

    # Scroll suave al final del formulario y de vuelta
    await page.mouse.wheel(0, random.randint(80, 180))
    await pausa(800, 1500)
    await page.mouse.wheel(0, -random.randint(40, 100))
    await pausa(500, 1000)
