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
import sys
import uuid
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
            # OJO: no incluir "--disable-blink-features=AutomationControlled" aqui.
            # Ese flag dispara el banner de Chrome/Edge "marca de linea de comandos
            # no admitida" (bad_flags_prompt), que es en si mismo una senal de
            # automatizacion visible/detectable. Se probo quitarlo para descartarlo
            # como causa de baja confianza en reCAPTCHA Enterprise.
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                # La ventana debe arrancar siempre maximizada. Este flag lo pide
                # al arranque; ademas se refuerza por CDP en _maximizar_y_traer_al_frente()
                # porque Chrome ignora --start-maximized cuando el perfil persistente
                # ya trae guardado el tamano de la ultima sesion.
                "--start-maximized",
            ],
            # no_viewport=True: NO forzar un viewport CDP fijo. Con un viewport fijo,
            # window.innerWidth/innerHeight quedan recortados por Playwright mientras
            # que window.outerWidth/outerHeight (tamano real de la ventana del SO)
            # no coinciden — esa discrepancia es una senal de automatizacion que un
            # navegador manual nunca tiene (ahi la ventana real y el viewport siempre
            # coinciden). Dejamos que la pagina use el tamano real de la ventana.
            no_viewport=True,
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

        try:
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
        except Exception as e:
            # El fallo mas comun aqui es el perfil persistente bloqueado por otro
            # proceso Chrome/Edge que quedo abierto (ej. una corrida anterior que
            # crasheo sin cerrar el navegador). Playwright reporta esto con un
            # error crudo poco accionable; se traduce a un mensaje claro para
            # que el operador sepa que revisar en vez de ver un stacktrace.
            await self._playwright.stop()
            self._playwright = None
            raise RuntimeError(
                f"No se pudo iniciar el navegador con el perfil persistente en {perfil}. "
                "Verifique que no haya otra instancia de Chrome/Edge usando este perfil "
                "(cierre procesos residuales del navegador) e intente de nuevo. "
                f"Detalle: {e}"
            ) from e

        # Enmascarar navigator.webdriver en todas las paginas nuevas
        await self._context.add_init_script(_STEALTH_SCRIPT)

        # Limite explicito para cualquier accion/espera de Playwright (click,
        # wait_for_selector, etc.). Sin esto, algunas esperas usan el default
        # interno de Playwright (30s) de forma implicita; fijarlo aqui deja el
        # limite documentado y consistente con los timeouts propios del
        # orquestador (ver TIMEOUT_DESCARGA_ITEM en orchestrator.py), y evita
        # que una espera se cuelgue mas alla de lo esperado.
        self._context.set_default_timeout(20_000)

        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

        await self._fijar_ventana_al_frente()
        await self._maximizar_y_traer_al_frente()

        return self._page

    async def _maximizar_y_traer_al_frente(self) -> None:
        """
        Deja la ventana maximizada y en primer plano.

        Se usa CDP en vez de confiar solo en --start-maximized porque el perfil
        persistente recuerda el tamano/posicion de la corrida anterior y pisa el flag.
        Chrome exige pasar por windowState 'normal' antes de cambiar a otro estado.
        """
        if not self._page or settings.BROWSER_HEADLESS:
            return
        try:
            cdp = await self._context.new_cdp_session(self._page)
            result = await cdp.send("Browser.getWindowForTarget")
            window_id = result["windowId"]
            await cdp.send("Browser.setWindowBounds", {
                "windowId": window_id,
                "bounds": {"windowState": "normal"},
            })
            await cdp.send("Browser.setWindowBounds", {
                "windowId": window_id,
                "bounds": {"windowState": "maximized"},
            })
            await cdp.detach()
            await self._page.bring_to_front()
        except Exception as e:
            logger.debug("No se pudo maximizar la ventana: %s", e)

    async def _fijar_ventana_al_frente(self) -> None:
        """
        Marca la ventana del navegador como 'siempre visible' (topmost) en Windows,
        para que no quede tapada por la app ni por otras ventanas durante el scraping.

        Para ubicar el HWND correcto (y no otra ventana de Chrome/Edge que el usuario
        tenga abierta) se le pone un titulo unico temporal a la pagina inicial, se
        busca esa ventana por titulo y luego se restaura el titulo.
        """
        if not self._page or settings.BROWSER_HEADLESS or sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            marcador = f"sri-scraper-{uuid.uuid4().hex}"
            titulo_previo = await self._page.title()
            await self._page.evaluate("t => document.title = t", marcador)

            user32 = ctypes.windll.user32
            encontrada: list[int] = []

            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def _callback(hwnd, _lparam):
                largo = user32.GetWindowTextLengthW(hwnd)
                if largo:
                    buffer = ctypes.create_unicode_buffer(largo + 1)
                    user32.GetWindowTextW(hwnd, buffer, largo + 1)
                    if marcador in buffer.value:
                        encontrada.append(hwnd)
                        return False
                return True

            user32.EnumWindows(WNDENUMPROC(_callback), 0)

            await self._page.evaluate("t => document.title = t", titulo_previo)

            if not encontrada:
                logger.debug("No se encontro la ventana del navegador para fijarla al frente.")
                return

            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_SHOWWINDOW = 0x0040
            user32.SetWindowPos(
                encontrada[0], HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
            )
            logger.info("Ventana del navegador fijada como siempre visible (topmost).")
        except Exception as e:
            logger.debug("No se pudo fijar la ventana al frente: %s", e)

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
        """Restaura la ventana del navegador maximizada y la trae al frente."""
        await self._maximizar_y_traer_al_frente()

    @property
    def page(self) -> Page | None:
        return self._page

    @property
    def context(self) -> BrowserContext | None:
        return self._context

    async def cerrar(self) -> None:
        # Ambos pasos van en su propio try/except: esto se llama desde el
        # `finally` del orquestador, y una excepcion sin atrapar aqui (ej. el
        # navegador ya crasheo y el contexto no responde) se propagaria
        # reemplazando cualquier error real ya capturado en el estado del
        # proceso, dejando al usuario con un "Error inesperado" generico en
        # vez del detalle real de lo que fallo.
        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                logger.warning("Error al cerrar el contexto del navegador: %s", e)
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.warning("Error al detener Playwright: %s", e)
        self._context = None
        self._page = None
        self._playwright = None
