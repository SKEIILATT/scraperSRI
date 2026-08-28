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
MAX_INTENTOS = 3              # reintentos del flujo humanizado antes de escalar


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
    Autentica en el portal SRI, con hasta MAX_INTENTOS reintentos automaticos
    del flujo humanizado completo (no de resolver el captcha — reintentar es
    simplemente volver a intentar la sesion desde cero).

    El reCAPTCHA del SRI es invisible/score-based: nunca presenta un desafio
    visual que un humano pueda resolver, asi que "intervencion manual" no es
    una opcion real aqui. Lo unico que puede ayudar es reintentar (Google
    reevalua la sesion cada vez) con pausas crecientes entre intentos. Solo
    tras agotar los reintentos se levanta ErrorRecaptcha, para que el llamador
    decida si termina el proceso o deja una ultima oportunidad de revision manual.

    Args:
        on_reintento: callback opcional invocado al iniciar cada intento con
                      (intento_actual, total_intentos), para que la UI muestre el estado.

    Raises:
        ErrorCredenciales: si el SRI rechaza usuario/password (no se reintenta).
        ErrorRecaptcha: si se agotaron los intentos y el login sigue bloqueado.
        ErrorLoginTimeout: si la pagina no responde en tiempo.
    """
    logger.info("Iniciando login SRI para RUC: %s (max %d intentos)", ruc, MAX_INTENTOS)

    for intento in range(1, MAX_INTENTOS + 1):
        logger.info("Login SRI - intento %d/%d", intento, MAX_INTENTOS)
        if on_reintento:
            try:
                await on_reintento(intento, MAX_INTENTOS)
            except Exception:
                pass

        # --- Paso 1: Ir a la pagina de inicio del SRI ---
        try:
            await page.goto(URL_INICIO, wait_until="domcontentloaded", timeout=TIMEOUT_NAVEGACION)
        except Exception as e:
            # Un hipo de red puntual (DNS, conexion reseteada, timeout de goto) no
            # deberia tumbar todo el login sin siquiera intentar de nuevo -- se trata
            # como cualquier otro motivo de reintento del flujo humanizado completo.
            logger.warning(
                "Error de navegacion a %s en intento %d/%d: %s", URL_INICIO, intento, MAX_INTENTOS, e,
            )
            if intento < MAX_INTENTOS:
                await asyncio.sleep((1500 + (intento - 1) * 2000) / 1000)
                continue
            raise ErrorLoginTimeout(f"No se pudo conectar al portal SRI: {e}") from e

        # Pausa creciente entre intentos para no repetir el mismo patron de timing
        await asyncio.sleep((800 + (intento - 1) * 2500) / 1000)

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

        # --- Formulario bloqueado sin navegar (probable rechazo silencioso de reCAPTCHA) ---
        # No se usa deteccion de widget aqui: el badge de reCAPTCHA esta presente
        # en la pagina siempre (login exitoso o no), asi que no sirve para distinguir
        # un bloqueo real. La unica senal disponible es que el formulario nunca navego.
        contenido = await page.content()
        formulario_sin_navegar = "usuario" in contenido and "kc-login" in contenido

        if formulario_sin_navegar:
            logger.warning(
                "Formulario sin navegar tras %dms en intento %d/%d — probable rechazo "
                "de reCAPTCHA (URL: %s)",
                TIMEOUT_POST_SUBMIT, intento, MAX_INTENTOS, page.url,
            )
            if intento < MAX_INTENTOS:
                continue
            raise ErrorRecaptcha(
                f"El SRI no completo el login tras {MAX_INTENTOS} intentos "
                "(posible verificacion reCAPTCHA fallida)."
            )

        # Sin redireccion, sin error y sin formulario visible: estado desconocido
        if intento < MAX_INTENTOS:
            continue
        raise ErrorLoginTimeout(
            "El login del SRI no completo tras varios intentos. "
            "Verifique las credenciales e intente de nuevo."
        )
