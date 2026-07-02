"""
Gestion del navegador Playwright con perfil persistente y configuracion anti-deteccion.

Estrategia zero-touch reCAPTCHA:
  1. Perfil persistente reutilizado entre ejecuciones (acumula senales de confianza).
  2. Modo headed real (headless=False) para no activar heuristicas de bot.
  3. navigator.webdriver enmascarado via init script.
  4. User-Agent real de Chrome (no el de Playwright por defecto).
"""

import logging
import os
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from config import settings

logger = logging.getLogger(__name__)

# User-Agent de Chrome estable actual (actualizar periodicamente)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# Script que elimina webdriver y agrega senales de navegador humano
_STEALTH_SCRIPT = """
// Ocultar webdriver
Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });

// Tracking de posicion del mouse para que el codigo Python sepa desde donde partir
window._mouseX = Math.floor(Math.random() * 400) + 200;
window._mouseY = Math.floor(Math.random() * 300) + 100;
document.addEventListener('mousemove', function(e) {
    window._mouseX = e.clientX;
    window._mouseY = e.clientY;
}, { passive: true });

// Plugins simulados (navegador real siempre tiene plugins)
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
        ];
        arr.item = (i) => arr[i];
        arr.namedItem = (n) => arr.find(p => p.name === n) || null;
        arr.refresh = () => {};
        Object.setPrototypeOf(arr, PluginArray.prototype);
        return arr;
    },
    configurable: true
});

// Languages
Object.defineProperty(navigator, 'languages', { get: () => ['es-EC', 'es', 'en-US', 'en'], configurable: true });
"""


class BrowserManager:
    def __init__(self):
        self._playwright = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def iniciar(self) -> Page:
        """
        Lanza el navegador con perfil persistente y configuracion anti-deteccion.
        Reutiliza el perfil si ya existe (preserva cookies y cache de Google).
        """
        perfil = Path(settings.BROWSER_PROFILE_DIR).resolve()
        perfil.mkdir(parents=True, exist_ok=True)

        es_perfil_nuevo = not any(perfil.iterdir())
        if es_perfil_nuevo:
            logger.info(
                "Perfil de navegador nuevo detectado en %s. "
                "La primera ejecucion puede enfrentar mayor friccion de reCAPTCHA.",
                perfil,
            )
        else:
            logger.info("Reutilizando perfil de navegador existente: %s", perfil)

        self._playwright = await async_playwright().start()

        launch_kwargs = dict(
            headless=settings.BROWSER_HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            viewport={"width": 1366, "height": 768},
            locale="es-EC",
            timezone_id="America/Guayaquil",
        )

        # Preferir un Chrome/Edge ya instalado en el equipo, apuntando directo
        # al ejecutable (evita depender de deteccion de canal por registro de
        # Windows y evita descargar el Chromium bundled de Playwright).
        rutas_candidatas = (
            [settings.BROWSER_EXECUTABLE_PATH]
            if settings.BROWSER_EXECUTABLE_PATH
            else settings.BROWSER_EXECUTABLE_CANDIDATES
        )
        ejecutable = next((r for r in rutas_candidatas if r and os.path.isfile(r)), None)

        if ejecutable:
            # No se sobreescribe user_agent: el ejecutable real ya envia un UA
            # consistente con sus propios headers Sec-CH-UA (Client Hints). Forzar
            # un UA distinto (p.ej. "Chrome" sobre un binario de Edge) crea una
            # discrepancia de fingerprint que reCAPTCHA Enterprise usa como senal de bot.
            logger.info("Navegador iniciado con ejecutable: %s", ejecutable)
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(perfil), executable_path=ejecutable, **launch_kwargs
            )
        else:
            logger.warning(
                "No se encontro Chrome/Edge instalado en las rutas conocidas. "
                "Usando Chromium bundled de Playwright (requiere 'playwright install chromium')."
            )
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(perfil), user_agent=_UA, **launch_kwargs
            )

        # Enmascarar navigator.webdriver en todas las paginas nuevas
        await self._context.add_init_script(_STEALTH_SCRIPT)

        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        return self._page

    async def minimizar(self) -> None:
        """Minimiza la ventana del navegador (solo en modo headed)."""
        if not self._page or settings.BROWSER_HEADLESS:
            return
        try:
            cdp = await self._context.new_cdp_session(self._page)
            result = await cdp.send("Browser.getWindowForTarget")
            await cdp.send("Browser.setWindowBounds", {
                "windowId": result["windowId"],
                "bounds": {"windowState": "minimized"},
            })
            await cdp.detach()
        except Exception as e:
            logger.debug("No se pudo minimizar la ventana: %s", e)

    async def mostrar(self) -> None:
        """Restaura y trae al frente la ventana del navegador."""
        if not self._page or settings.BROWSER_HEADLESS:
            return
        try:
            cdp = await self._context.new_cdp_session(self._page)
            result = await cdp.send("Browser.getWindowForTarget")
            await cdp.send("Browser.setWindowBounds", {
                "windowId": result["windowId"],
                "bounds": {"windowState": "normal"},
            })
            await cdp.detach()
        except Exception as e:
            logger.debug("No se pudo restaurar la ventana: %s", e)

    @property
    def page(self) -> Page | None:
        return self._page

    @property
    def context(self) -> BrowserContext | None:
        return self._context

    async def cerrar(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
        self._context = None
        self._page = None
        self._playwright = None
