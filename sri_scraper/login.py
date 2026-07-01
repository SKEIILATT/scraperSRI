"""
Login en el portal SRI en linea.

URL de inicio: https://srienlinea.sri.gob.ec/sri-en-linea/inicio/NAT
Flujo: inicio/NAT -> click "Iniciar sesion" -> formulario Keycloak (#usuario, #password, #kc-login)

Detecta:
  - Login exitoso (redireccion al portal principal).
  - Credenciales incorrectas (mensaje de error en pagina).
  - reCAPTCHA bloqueante (desafio visible o timeout sin respuesta).
"""

import asyncio
import logging
from collections.abc import Callable, Awaitable

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from .humanize import escribir_humanizado, mover_y_click, pausa

logger = logging.getLogger(__name__)

URL_INICIO = "https://srienlinea.sri.gob.ec/sri-en-linea/inicio/NAT"
URL_PERFIL = "/sri-en-linea/contribuyente/perfil"   # link de "Iniciar sesion" en el menu
TIMEOUT_NAVEGACION = 30_000   # ms
TIMEOUT_ELEMENTO = 15_000     # ms
TIMEOUT_POST_SUBMIT = 25_000  # ms para que validarUsuario()+reCAPTCHA+submit completen
MAX_INTENTOS_RECAPTCHA = 3    # reintentos antes de escalar a intervencion manual


class ErrorCredenciales(Exception):
    pass


class ErrorRecaptcha(Exception):
    """reCAPTCHA presento desafio visible o no se resolvio en tiempo."""
    pass


class ErrorLoginTimeout(Exception):
    pass


async def login_sri(
    page: Page,
    ruc: str,
    password: str,
    on_reintento: "Callable[[int, int], Awaitable[None]] | None" = None,
) -> None:
    """
    Autentica en el portal SRI con hasta MAX_INTENTOS_RECAPTCHA reintentos automaticos.

    Estrategia de reintento:
      - Si reCAPTCHA no habilita el boton en TIMEOUT_RECAPTCHA ms, recarga la pagina
        y reintenta. La pausa entre reintentos crece (3s, 6s, 10s) para dar mas
        tiempo al sistema de acumular senales de confianza del perfil.
      - Solo lanza ErrorRecaptcha (intervencion manual) si los 3 intentos fallan.
      - Si las credenciales son incorrectas, lanza ErrorCredenciales inmediatamente
        sin reintentar (no tiene sentido reintentar con credenciales malas).

    Args:
        on_reintento: callback opcional llamado al inicio de cada intento con
                      (intento_actual, total_intentos) para actualizar la UI.

    Raises:
        ErrorCredenciales: si el SRI rechaza usuario/password.
        ErrorRecaptcha: si los 3 intentos automaticos fallan (requiere intervencion manual).
        ErrorLoginTimeout: si la pagina no responde en tiempo.
    """
    logger.info("Iniciando login SRI para RUC: %s (max %d intentos)", ruc, MAX_INTENTOS_RECAPTCHA)

    for intento in range(1, MAX_INTENTOS_RECAPTCHA + 1):
        logger.info("Login SRI - intento %d/%d", intento, MAX_INTENTOS_RECAPTCHA)
        if on_reintento:
            try:
                await on_reintento(intento, MAX_INTENTOS_RECAPTCHA)
            except Exception:
                pass

        # --- Paso 1: Ir a la pagina de inicio del SRI ---
        await page.goto(URL_INICIO, wait_until="domcontentloaded", timeout=TIMEOUT_NAVEGACION)
        # Pausa creciente entre reintentos para acumular senales de confianza en el perfil
        pausa_inicial = 800 + (intento - 1) * 2500
        await asyncio.sleep(pausa_inicial / 1000)

        # --- Paso 2: Hacer click en "Iniciar sesion" del menu ---
        try:
            await page.wait_for_selector(f"a[href='{URL_PERFIL}']", timeout=TIMEOUT_ELEMENTO)
        except PlaywrightTimeout:
            raise ErrorLoginTimeout("El menu del portal SRI no cargo. Verifique su conexion.")

        await mover_y_click(page, f"a[href='{URL_PERFIL}']")
        await pausa(800, 1500)

        # --- Paso 3: Esperar formulario Keycloak con campo #usuario ---
        try:
            await page.wait_for_selector("#usuario", timeout=TIMEOUT_ELEMENTO)
        except PlaywrightTimeout:
            raise ErrorLoginTimeout("El formulario de login del SRI no cargo en tiempo.")

        logger.info("Formulario Keycloak cargado en: %s", page.url)

        # --- Paso 4: Escribir credenciales de forma humanizada ---
        # #usuario es el campo visible; el JS de la pagina copia su valor a #username (oculto)
        await escribir_humanizado(page, "#usuario", ruc)
        await pausa(300, 700)
        await escribir_humanizado(page, "#password", password)
        await pausa(500, 1000)

        # --- Paso 5: Click en "Ingresar" ---
        # validarUsuario() se ejecuta en onsubmit: copia #usuario a #username,
        # ejecuta reCAPTCHA Enterprise y solo permite el submit si pasa.
        # Si reCAPTCHA bloquea, el form no navega y nos quedamos en la misma pagina.
        await mover_y_click(page, "#kc-login")
        logger.info("Click en Ingresar ejecutado, esperando navegacion...")

        # --- Paso 6: Esperar resultado ---
        try:
            # Login exitoso = navega fuera del formulario Keycloak al portal del contribuyente
            await page.wait_for_url(
                lambda url: (
                    "sri-en-linea" in url
                    and "inicio" not in url
                    and "realms" not in url
                    and "login-actions" not in url
                ),
                timeout=TIMEOUT_POST_SUBMIT,
            )
            # Esperar que el portal Angular complete el intercambio del token Keycloak.
            # Con response_mode=fragment, el codigo llega en el hash URL y el JS del
            # portal necesita procesarlo antes de que podamos navegar a sub-apps.
            try:
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except PlaywrightTimeout:
                pass  # Background requests prolongadas — continuar de todos modos
            logger.info("Login SRI exitoso para RUC: %s (intento %d) | URL: %s", ruc, intento, page.url)
            return
        except PlaywrightTimeout:
            pass  # No navego — verificar causa

        # --- Detectar error de credenciales (el SRI muestra mensaje en la misma pagina) ---
        error_selectors = [
            "#kc-feedback-wrapper",
            ".alert-error",
            "#input-error",
            "div.alert.alert-error",
            "span#input-error-password",
            "span#input-error-username",
        ]
        for selector in error_selectors:
            el = page.locator(selector)
            if await el.count() > 0:
                texto = (await el.first.inner_text()).strip()
                if texto:
                    logger.warning("Error de credenciales SRI: %s", texto)
                    raise ErrorCredenciales(f"Credenciales incorrectas: {texto}")

        # Si seguimos en el formulario sin error explicito = reCAPTCHA bloqueo el submit
        url_actual = page.url
        if "usuario" in await page.content() and "kc-login" in await page.content():
            logger.warning(
                "Formulario sin navegar tras %dms en intento %d/%d — posible bloqueo reCAPTCHA (URL: %s)",
                TIMEOUT_POST_SUBMIT, intento, MAX_INTENTOS_RECAPTCHA, url_actual,
            )
            if intento < MAX_INTENTOS_RECAPTCHA:
                logger.info("Reintentando en %ds...", intento * 3)
                await asyncio.sleep(intento * 3)
                continue
            else:
                raise ErrorRecaptcha(
                    f"reCAPTCHA bloqueo el login en {MAX_INTENTOS_RECAPTCHA} intentos automaticos. "
                    "Se requiere verificacion manual."
                )

        # Sin redireccion, sin error y sin formulario visible: estado desconocido
        if intento < MAX_INTENTOS_RECAPTCHA:
            await asyncio.sleep(intento * 3)
        else:
            raise ErrorLoginTimeout(
                "El login del SRI no completo tras varios intentos. "
                "Verifique las credenciales e intente de nuevo."
            )
