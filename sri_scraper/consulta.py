"""
Navegacion y consulta en la pagina de Comprobantes Electronicos Recibidos del SRI.

URL destino:
  https://srienlinea.sri.gob.ec/comprobantes-electronicos-internet/pages/consultas/recibidos/comprobantesRecibidos.jsf

El formulario usa JSF/PrimeFaces con ViewState. Los submits son postbacks AJAX.
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from .humanize import (
    escribir_humanizado, mover_y_click, pausa, seleccionar_opcion,
    simular_lectura, simular_lectura_formulario, acercarse_lento,
)

logger = logging.getLogger(__name__)

# URL del gateway del portal (tuportal-internet) para comprobantes recibidos.
# redireccion=57 e idGrupo=55 son los parametros del menu "Comprobantes electrónicos recibidos".
# Este gateway maneja la autenticacion de tuportal-internet y luego redirige al JSF.
URL_COMPROBANTES_PORTAL = (
    "https://srienlinea.sri.gob.ec/tuportal-internet/accederAplicacion.jspa"
    "?redireccion=57&idGrupo=55"
)

# URL directa de la app JSF (usada para detectar que ya llegamos al formulario)
URL_COMPROBANTES = (
    "https://srienlinea.sri.gob.ec/comprobantes-electronicos-internet/pages/consultas/recibidos/"
    "comprobantesRecibidos.jsf"
)

TIMEOUT_AJAX = 30_000
TIMEOUT_TABLA = 40_000

TIPOS_COMPROBANTE = {
    "1": "Factura",
    "2": "Liquidacion de compra",
    "3": "Nota de Credito",
    "4": "Nota de Debito",
    "6": "Comprobante de Retencion",
}


@dataclass
class FilaComprobante:
    indice: int                   # indice 0-based en la pagina actual
    tipo_y_serie: str             # ej. "Factura 001-012-021762077"
    es_retencion: bool = False
    xml_descargado: bool = False
    pdf_descargado: bool = False
    error: str = ""


@dataclass
class ResultadoConsulta:
    filas: list[FilaComprobante] = field(default_factory=list)
    total_paginas: int = 1
    reporte_descargado: bool = False
    errores: list[str] = field(default_factory=list)


ProgressCallback = Callable[[str], Awaitable[None]]


class ErrorCaptchaConsulta(Exception):
    """El formulario de consulta requiere resolver un CAPTCHA visible en el navegador."""
    pass


async def _tomar_screenshot_debug(page: Page, nombre: str) -> None:
    try:
        await page.screenshot(path=nombre)
    except Exception:
        pass


async def _esperar_formulario(page: Page) -> bool:
    """Espera que el formulario JSF de comprobantes este visible. Retorna True si lo encontro."""
    for sel in ("#frmPrincipal\\:ano", "form[id='frmPrincipal']", "#frmPrincipal"):
        try:
            await page.wait_for_selector(sel, timeout=20_000)
            logger.info("Formulario de comprobantes detectado con: %s", sel)
            return True
        except PlaywrightTimeout:
            continue
    return False



async def _verificar_error_captcha(page: Page) -> None:
    """Detecta mensajes de error CAPTCHA en el formulario JSF. Lanza ErrorCaptchaConsulta si existe."""
    selectors = [
        ".ui-messages-warn",
        ".ui-messages-error",
        ".ui-messages-info",
        ".ui-growl-item-container",
        ".ui-messages",
    ]
    for sel in selectors:
        try:
            elementos = page.locator(sel)
            count = await elementos.count()
            for i in range(count):
                texto = (await elementos.nth(i).inner_text()).strip()
                if "captcha" in texto.lower():
                    logger.warning("CAPTCHA detectado en formulario de consulta: %s", texto)
                    raise ErrorCaptchaConsulta(
                        "El formulario de consulta requiere verificacion CAPTCHA. "
                        "Resuelva el CAPTCHA visible en la ventana del navegador y presione 'Continuar'."
                    )
        except ErrorCaptchaConsulta:
            raise
        except Exception:
            pass


async def _login_tuportal(page: Page, ruc: str, password: str) -> bool:
    """
    Completa el login de Keycloak para app-tuportal-internet.

    El formulario usa los mismos IDs que el login de sri-en-linea (#usuario, #password, #kc-login).
    Retorna True si el login navego exitosamente fuera de Keycloak.
    """
    logger.info("Detectado formulario Keycloak tuportal-internet. Completando login...")

    # Verificar que el formulario Keycloak este presente
    try:
        await page.wait_for_selector("#usuario, #kc-login", timeout=8_000)
    except PlaywrightTimeout:
        logger.warning("Formulario Keycloak tuportal no encontrado con selectores esperados")
        return False

    # Limpiar y rellenar campos
    try:
        await page.locator("#usuario").clear()
    except Exception:
        pass
    await escribir_humanizado(page, "#usuario", ruc)
    await pausa(300, 600)
    await escribir_humanizado(page, "#password", password)
    await pausa(500, 900)

    # Click en Ingresar (valida reCAPTCHA via onsubmit=validarUsuario)
    await mover_y_click(page, "#kc-login")
    logger.info("Click en Ingresar (tuportal). Esperando navegacion...")

    try:
        await page.wait_for_url(
            lambda u: "realms" not in u and "openid-connect" not in u,
            timeout=25_000,
        )
        logger.info("Login tuportal exitoso. URL: %s", page.url)
        # Dar tiempo al portal para inicializar
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        return True
    except PlaywrightTimeout:
        logger.warning("Login tuportal no navego en 25s — posible bloqueo reCAPTCHA")
        return False


async def navegar_a_comprobantes(
    page: Page,
    ruc: str = "",
    password: str = "",
    on_reintento=None,
) -> None:
    """
    Navega al formulario JSF de comprobantes electronicos recibidos.

    Usa el gateway del portal (accederAplicacion.jspa?redireccion=57) en lugar de la URL
    JSF directa. Este gateway es el que maneja la autenticacion de tuportal-internet y
    hace el redirect al JSF con el contexto correcto.

    Flujo:
      1. goto accederAplicacion.jspa?redireccion=57&idGrupo=55
      2a. Si ya hay sesion tuportal: el portal redirige al JSF -> formulario carga
      2b. Si no hay sesion: Keycloak aparece con client_id=app-tuportal-internet
          -> llenamos credenciales -> login exitoso -> Keycloak redirige a tuportal
          -> portal redirige al JSF -> formulario carga
    """
    MAX_REINTENTOS = 3

    for intento in range(1, MAX_REINTENTOS + 1):
        logger.info("Navegando a comprobantes via gateway (intento %d/%d)...", intento, MAX_REINTENTOS)

        if on_reintento and intento > 1:
            try:
                await on_reintento(intento, MAX_REINTENTOS)
            except Exception:
                pass

        await page.goto(URL_COMPROBANTES_PORTAL, wait_until="domcontentloaded", timeout=40_000)
        await pausa(1000, 1800)

        url_actual = page.url
        logger.info("URL tras goto gateway (intento %d): %s", intento, url_actual)

        # --- Caso A: Formulario JSF ya cargo (gateway redirigido directo) ---
        if "comprobantes-electronicos-internet" in url_actual:
            if await _esperar_formulario(page):
                return
            await _tomar_screenshot_debug(page, "debug_comprobantes_sin_formulario.png")
            raise RuntimeError(
                "La pagina de comprobantes cargo pero el formulario JSF no aparecio."
            )

        # --- Caso B: Keycloak pide login para tuportal-internet ---
        if "realms" in url_actual or "openid-connect" in url_actual:
            logger.info("Keycloak tuportal-internet detectado. Completando login...")

            if not ruc or not password:
                raise RuntimeError(
                    "El portal SRI requiere autenticacion en tuportal-internet pero "
                    "no se proporcionaron credenciales."
                )

            login_ok = await _login_tuportal(page, ruc, password)
            if not login_ok:
                if intento < MAX_REINTENTOS:
                    await pausa(intento * 3000, intento * 3000 + 1500)
                    continue
                await _tomar_screenshot_debug(page, "debug_tuportal_login_fallo.png")
                raise RuntimeError(
                    f"Login tuportal-internet fallo tras {MAX_REINTENTOS} intentos."
                )

            # Tras el login, el portal puede:
            # a) Haberse redirigido al JSF de comprobantes ya
            # b) Estar en tuportal esperando el redirect interno
            await pausa(1500, 2500)
            url_post_login = page.url
            logger.info("URL tras login tuportal: %s", url_post_login)

            if "comprobantes-electronicos-internet" in url_post_login:
                if await _esperar_formulario(page):
                    return

            # El portal puede necesitar un momento para hacer el redirect interno
            try:
                await page.wait_for_url(
                    lambda u: "comprobantes-electronicos-internet" in u,
                    timeout=15_000,
                )
                if await _esperar_formulario(page):
                    return
            except PlaywrightTimeout:
                pass

            # Si todavia no llegamos al JSF, el siguiente ciclo hace goto al gateway de nuevo
            logger.info("Reintentando goto al gateway tras login tuportal...")
            continue

        # --- Caso C: En tuportal-internet procesando el redirect al JSF ---
        if "tuportal-internet" in url_actual:
            logger.info("En tuportal-internet. Esperando redirect al formulario JSF...")
            try:
                await page.wait_for_url(
                    lambda u: "comprobantes-electronicos-internet" in u or "realms" in u,
                    timeout=20_000,
                )
            except PlaywrightTimeout:
                pass

            url_post = page.url
            logger.info("URL tras espera en tuportal: %s", url_post)

            if "comprobantes-electronicos-internet" in url_post:
                if await _esperar_formulario(page):
                    return

            if "realms" in url_post:
                continue  # Keycloak aparecio, el siguiente ciclo lo maneja

            await pausa(1000, 1500)
            continue

        # --- Caso D: URL inesperada ---
        logger.warning("URL inesperada en intento %d: %s", intento, url_actual)
        await _tomar_screenshot_debug(page, f"debug_inesperado_{intento}.png")
        await pausa(2000, 3000)

    await _tomar_screenshot_debug(page, "debug_comprobantes_fallo_final.png")
    raise RuntimeError(
        f"No fue posible acceder al formulario de comprobantes tras {MAX_REINTENTOS} intentos. "
        f"URL final: {page.url}"
    )


_CAMPOS_FORMULARIO = [
    "#frmPrincipal\\:ano",
    "#frmPrincipal\\:mes",
    "#frmPrincipal\\:dia",
    "#frmPrincipal\\:cmbTipoComprobante",
    "#frmPrincipal\\:btnBuscar",
]


async def aplicar_filtros(
    page: Page,
    anio: str,
    mes: str,
    dia: str,
    tipo_comprobante: str,
) -> None:
    """
    Selecciona los filtros en el formulario frmPrincipal y hace click en Consultar.
    Espera que la tabla de resultados cargue antes de retornar.
    """
    logger.info("Aplicando filtros: anio=%s mes=%s dia=%s tipo=%s", anio, mes, dia, tipo_comprobante)

    # Fase 1: lectura larga de la pagina — reCAPTCHA evalua el tiempo y movimiento
    # desde que carga hasta la primera interaccion
    await simular_lectura(page, segundos_min=8.0, segundos_max=14.0)

    # Fase 2: revisar visualmente los campos del formulario antes de tocarlos
    await simular_lectura_formulario(page, _CAMPOS_FORMULARIO)

    # Fase 3: seleccionar filtros con pausas largas (humano que piensa entre selecciones)
    await seleccionar_opcion(page, "#frmPrincipal\\:ano", anio)
    await pausa(2000, 4000)
    await seleccionar_opcion(page, "#frmPrincipal\\:mes", mes)
    await pausa(2000, 4000)
    await seleccionar_opcion(page, "#frmPrincipal\\:dia", dia)
    await pausa(2000, 3500)
    await seleccionar_opcion(page, "#frmPrincipal\\:cmbTipoComprobante", tipo_comprobante)

    # Fase 4: pausa larga post-seleccion — como si el usuario revisara antes de enviar
    await pausa(3000, 6000)

    # Fase 5: acercarse lentamente al boton Consultar (movimiento deliberado)
    await acercarse_lento(page, "#frmPrincipal\\:btnBuscar")

    # Fase 6: click en Consultar
    try:
        async with page.expect_response(
            lambda r: "comprobantesRecibidos" in r.url and r.request.method == "POST",
            timeout=TIMEOUT_AJAX,
        ):
            await mover_y_click(page, "#frmPrincipal\\:btnBuscar")
    except PlaywrightTimeout:
        pass  # Sin respuesta AJAX — captcha puede haber bloqueado el submit

    await pausa(1200, 1800)

    # Verificar si el servidor respondio con error de CAPTCHA
    await _verificar_error_captcha(page)

    await esperar_tabla(page)


async def esperar_tabla(page: Page) -> None:
    """
    Espera que la tabla de resultados este visible.
    Se llama por separado cuando el usuario resolvio el CAPTCHA manualmente
    y ya hizo clic en 'Consultar' en el navegador.
    """
    try:
        await page.wait_for_selector(
            "#frmPrincipal\\:tablaCompRecibidos_data tr, "
            ".ui-datatable-empty-message",
            timeout=TIMEOUT_TABLA,
        )
    except PlaywrightTimeout:
        await _verificar_error_captcha(page)
        raise RuntimeError("La tabla de resultados no cargo despues de aplicar los filtros.")

    await pausa(500, 1000)


async def obtener_filas_pagina(page: Page, tipo_comprobante: str) -> list[FilaComprobante]:
    """Extrae todas las filas visibles de la tabla en la pagina actual."""
    filas = []
    rows = page.locator("#frmPrincipal\\:tablaCompRecibidos_data tr")
    total = await rows.count()

    for i in range(total):
        row = rows.nth(i)
        celdas = row.locator("td")
        if await celdas.count() == 0:
            continue

        # La columna de tipo y serie suele ser la primera o segunda
        tipo_y_serie = ""
        for col_idx in range(min(3, await celdas.count())):
            texto = (await celdas.nth(col_idx).inner_text()).strip()
            if texto and any(
                kw in texto.lower()
                for kw in ["factura", "liquidacion", "nota", "comprobante", "retencion", "001-", "002-", "003-"]
            ):
                tipo_y_serie = texto
                break

        if not tipo_y_serie:
            # Fallback: tomar el texto de la primera celda no vacia
            for col_idx in range(await celdas.count()):
                texto = (await celdas.nth(col_idx).inner_text()).strip()
                if texto:
                    tipo_y_serie = texto
                    break

        es_retencion = tipo_comprobante == "6"
        filas.append(FilaComprobante(indice=i, tipo_y_serie=tipo_y_serie, es_retencion=es_retencion))

    return filas


async def total_paginas(page: Page) -> int:
    """Detecta cuantas paginas tiene el paginador de la tabla."""
    paginador = page.locator(".ui-paginator-pages .ui-paginator-page")
    count = await paginador.count()
    if count == 0:
        return 1
    return count


async def ir_a_pagina(page: Page, numero: int) -> None:
    """Navega a una pagina especifica del paginador de la tabla."""
    paginas = page.locator(".ui-paginator-pages .ui-paginator-page")
    total = await paginas.count()
    if numero <= total:
        async with page.expect_response(
            lambda r: "comprobantesRecibidos" in r.url and r.request.method == "POST",
            timeout=TIMEOUT_AJAX,
        ):
            await paginas.nth(numero - 1).click()
        await pausa(800, 1500)


async def descargar_reporte(page: Page, ruta_destino: str) -> bool:
    """
    Descarga el reporte TXT haciendo click en el link 'Descargar reporte'.
    Retorna True si la descarga fue exitosa.
    """
    try:
        async with page.expect_download(timeout=30_000) as dl_info:
            await mover_y_click(page, "#frmPrincipal\\:lnkTxtlistado")
        descarga = await dl_info.value
        await descarga.save_as(ruta_destino)
        logger.info("Reporte descargado: %s", ruta_destino)
        return True
    except Exception as e:
        logger.warning("No se pudo descargar el reporte: %s", e)
        return False


async def descargar_xml(page: Page, indice: int, ruta_destino: str) -> bool:
    """Descarga el XML de la fila en el indice dado."""
    selector = f"#frmPrincipal\\:tablaCompRecibidos\\:{indice}\\:lnkXml"
    return await _descargar_doc(page, selector, ruta_destino, "XML")


async def descargar_pdf(page: Page, indice: int, ruta_destino: str) -> bool:
    """Descarga el PDF/RIDE de la fila en el indice dado."""
    selector = f"#frmPrincipal\\:tablaCompRecibidos\\:{indice}\\:lnkPdf"
    return await _descargar_doc(page, selector, ruta_destino, "PDF")


async def _descargar_doc(page: Page, selector: str, ruta_destino: str, tipo: str) -> bool:
    for intento in range(1, 4):
        try:
            async with page.expect_download(timeout=30_000) as dl_info:
                await mover_y_click(page, selector)
            descarga = await dl_info.value
            await descarga.save_as(ruta_destino)
            logger.debug("%s descargado: %s", tipo, ruta_destino)
            return True
        except Exception as e:
            logger.warning("Intento %d/%d fallido para %s (%s): %s", intento, 3, tipo, selector, e)
            await pausa(1000, 2000)
    return False
