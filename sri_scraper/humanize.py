"""
Utilidades para humanizar interacciones con el navegador y reducir deteccion de bot.
"""

import asyncio
import random

from playwright.async_api import Page


async def pausa(min_ms: int = 200, max_ms: int = 800) -> None:
    """Espera aleatoria entre acciones para simular comportamiento humano."""
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def mover_y_click(page: Page, selector: str) -> None:
    """Mueve el mouse hacia el elemento antes de hacer click (evita click teleportado)."""
    elemento = page.locator(selector)
    await elemento.scroll_into_view_if_needed()
    box = await elemento.bounding_box()
    if box:
        # Punto aleatorio dentro del bounding box del elemento
        x = box["x"] + random.uniform(box["width"] * 0.2, box["width"] * 0.8)
        y = box["y"] + random.uniform(box["height"] * 0.2, box["height"] * 0.8)
        await page.mouse.move(x, y, steps=random.randint(5, 15))
        await pausa(100, 300)
    await elemento.click()


async def escribir_humanizado(page: Page, selector: str, texto: str) -> None:
    """Escribe texto caracter por caracter con delay variable, como un humano."""
    await mover_y_click(page, selector)
    await pausa(150, 400)
    await page.locator(selector).fill("")  # limpiar campo primero
    await page.locator(selector).type(texto, delay=random.uniform(60, 140))


async def seleccionar_opcion(page: Page, selector: str, valor: str) -> None:
    """Selecciona una opcion en un <select> con pausa previa."""
    await mover_y_click(page, selector)
    await pausa(200, 500)
    await page.locator(selector).select_option(valor)
    await pausa(150, 350)


async def simular_lectura(page: Page, segundos_min: float = 3.0, segundos_max: float = 6.0) -> None:
    """
    Simula que un usuario lee la pagina: movimientos de mouse naturales y scroll suave.
    El reCAPTCHA invisible evalua este comportamiento para determinar el score de confianza.
    Llamar despues de que la pagina carga y antes de interactuar con el formulario.
    """
    viewport = page.viewport_size or {"width": 1366, "height": 768}
    w, h = viewport["width"], viewport["height"]

    tiempo_total = random.uniform(segundos_min, segundos_max)
    t_inicio = asyncio.get_event_loop().time()

    # Puntos aleatorios por la pagina simulando lectura
    while asyncio.get_event_loop().time() - t_inicio < tiempo_total:
        x = random.uniform(w * 0.05, w * 0.95)
        y = random.uniform(h * 0.05, h * 0.90)
        steps = random.randint(8, 20)
        await page.mouse.move(x, y, steps=steps)
        await pausa(400, 1200)

        # Scroll ocasional (1 de cada 3 movimientos)
        if random.random() < 0.33:
            delta = random.randint(30, 120) * random.choice([1, -1])
            await page.mouse.wheel(0, delta)
            await pausa(300, 700)
