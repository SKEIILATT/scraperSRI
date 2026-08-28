"""
Navegacion y consulta en la pagina de Comprobantes Electronicos Recibidos del SRI.

URL destino:
  https://srienlinea.sri.gob.ec/comprobantes-electronicos-internet/pages/consultas/recibidos/comprobantesRecibidos.jsf

El formulario usa JSF/PrimeFaces con ViewState. Los submits son postbacks AJAX.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from .humanize import (
    escribir_humanizado, mover_y_click, pausa, seleccionar_opcion,
    simular_lectura, simular_lectura_formulario, acercarse_lento, click_directo,
)

logger = logging.getLogger(__name__)

# URL del gateway del portal (tuportal-internet) para comprobantes recibidos.
# redireccion=57 e idGrupo=55 son los parametros del menu "Comprobantes electronicos recibidos".
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

def _frm(sufijo: str) -> str:
    """
    Selector tolerante a cambios de id: el SRI renombra periodicamente los ids
    de sus componentes JSF (ya paso con el boton Consultar: btnBuscar -> btnConsultarSinRe).
    En vez de anclar al id completo, se ancla solo al sufijo estable del campo/componente
    usando el selector de atributo `$=` (termina con), que actua como un "regex" laxo
    y sobrevive a que el SRI agregue prefijos o renombre el contenedor.
    """
    return f'[id$="{sufijo}"]'


TIPOS_COMPROBANTE = {
    "1": "Factura",
    "2": "Liquidación de compra",
    "3": "Nota de Crédito",
    "4": "Nota de Débito",
    "6": "Comprobante de Retención",
}


# Extrae la parte numerica "establecimiento-puntoEmision-secuencial" del texto
# de la columna "Tipo y serie de comprobante" (ej. "Factura  323-002-000103524" -> "323-002-000103524")
_RE_SERIE = re.compile(r"\d{3}-\d{3}-\d+")


@dataclass
class FilaComprobante:
    indice: int                   # indice real de fila usado por PrimeFaces (data-ri), NO la posicion local en la pagina
    tipo_y_serie: str             # ej. "Factura 001-012-021762077"
    razon_social: str = ""        # razon social del emisor (columna "RUC y Razon social emisor")
    fecha_emision: str = ""       # fecha de emision en formato DD/MM/YYYY (columna "Fecha emision")
    es_retencion: bool = False
    xml_descargado: bool = False
    pdf_descargado: bool = False
    error: str = ""

    @property
    def nombre_archivo(self) -> str:
        """
        Nombre de archivo pedido por el usuario: "DD-MM-YYYY serie razon_social"
        (ej. "01-04-2026 001-104-000041246 TAGSA"). Si falta fecha o razon social
        (columnas no encontradas), cae de vuelta a tipo_y_serie para no perder el
        archivo por completo.
        """
        fecha = self.fecha_emision.replace("/", "-")
        m = _RE_SERIE.search(self.tipo_y_serie)
        serie = m.group(0) if m else self.tipo_y_serie
        partes = [p for p in (fecha, serie, self.razon_social) if p]
        return " ".join(partes) if partes else self.tipo_y_serie


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
    for sel in (_frm(":ano"), "form[id='frmPrincipal']", "#frmPrincipal"):
        try:
            await page.wait_for_selector(sel, timeout=20_000)
            logger.info("Formulario de comprobantes detectado con: %s", sel)
            return True
        except PlaywrightTimeout:
            continue
    return False



async def _verificar_error_captcha(page: Page) -> None:
    """Detecta si el SERVIDOR rechazo la consulta por verificacion CAPTCHA
    (mensaje de error explicito en el formulario JSF). Lanza ErrorCaptchaConsulta
    si existe.

    OJO: no se usa hay_recaptcha() (presencia del badge/widget) como senal aqui.
    El badge de reCAPTCHA esta presente en la pagina SIEMPRE (es un requisito de
    Google, se muestra en toda carga de pagina, haya fallado la consulta o no).
    Usarlo como disparador aqui causaba un falso positivo en el 100% de los
    intentos, exitosos o no. La unica senal confiable de bloqueo real es que
    el propio servidor del SRI devuelva un mensaje de error mencionando "captcha".
    """
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
                    # OJO: el navegador corre en el servidor, no en el equipo del
                    # usuario -- nadie puede "resolver" nada en esa ventana. El
                    # mensaje no debe prometer una accion que no existe; solo
                    # informa que el SRI rechazo la verificacion automatica.
                    raise ErrorCaptchaConsulta(
                        "El SRI rechazó la verificación automática (reCAPTCHA) para este "
                        "tipo de comprobante tras varios reintentos. Puede deberse a un "
                        "bloqueo temporal — intente nuevamente en unos minutos."
                    )
        except ErrorCaptchaConsulta:
            raise
        except Exception:
            pass


async def _texto_mensajes_servidor(page: Page) -> str:
    """
    Recolecta el texto de cualquier mensaje JSF (.ui-messages-*) visible en la
    pagina. Se usa para incluir el mensaje real del SRI en los errores en vez
    de un texto generico — asi un mensaje nuevo/desconocido queda visible en el
    log inmediatamente, sin tener que reproducir el caso para verlo.
    """
    selectors = [
        ".ui-messages-warn",
        ".ui-messages-error",
        ".ui-messages-info",
        ".ui-growl-item-container",
        ".ui-messages",
    ]
    textos: list[str] = []
    for sel in selectors:
        try:
            elementos = page.locator(sel)
            count = await elementos.count()
            for i in range(count):
                texto = (await elementos.nth(i).inner_text()).strip()
                if texto:
                    textos.append(texto)
        except Exception:
            pass
    return " | ".join(dict.fromkeys(textos))


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
        # No navego. No usamos hay_recaptcha() aqui: el badge de reCAPTCHA esta
        # presente en la pagina siempre, exitosa o no, asi que no distingue nada.
        # Se deja que el llamador (navegar_a_comprobantes) reintente el flujo
        # humanizado — no es "resolver" el captcha, solo darle mas oportunidades
        # a una sesion genuina.
        logger.warning("Login tuportal no navego en 25s")
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

        try:
            await page.goto(URL_COMPROBANTES_PORTAL, wait_until="domcontentloaded", timeout=40_000)
        except Exception as e:
            # Igual que en login.py: un hipo de red puntual en el goto no debe
            # escapar del loop de reintentos ya existente para este flujo.
            logger.warning(
                "Error de navegacion al gateway de comprobantes (intento %d/%d): %s",
                intento, MAX_REINTENTOS, e,
            )
            if intento < MAX_REINTENTOS:
                await pausa(2000, 4000)
                continue
            await _tomar_screenshot_debug(page, "debug_comprobantes_goto_fallo.png")
            raise RuntimeError(
                f"No se pudo conectar al portal de comprobantes tras {MAX_REINTENTOS} intentos: {e}"
            ) from e
        await pausa(1000, 1800)

        url_actual = page.url
        logger.info("URL tras goto gateway (intento %d): %s", intento, url_actual)

        # --- Caso A: Formulario JSF ya cargo (gateway redirigido directo) ---
        if "comprobantes-electronicos-internet" in url_actual:
            if await _esperar_formulario(page):
                return
            await _tomar_screenshot_debug(page, "debug_comprobantes_sin_formulario.png")
            raise RuntimeError(
                "La página de comprobantes cargó pero el formulario JSF no apareció."
            )

        # --- Caso B: Keycloak pide login para tuportal-internet ---
        if "realms" in url_actual or "openid-connect" in url_actual:
            logger.info("Keycloak tuportal-internet detectado. Completando login...")

            if not ruc or not password:
                raise RuntimeError(
                    "El portal SRI requiere autenticación en tuportal-internet pero "
                    "no se proporcionaron credenciales."
                )

            login_ok = await _login_tuportal(page, ruc, password)
            if not login_ok:
                if intento < MAX_REINTENTOS:
                    await pausa(intento * 3000, intento * 3000 + 1500)
                    continue
                await _tomar_screenshot_debug(page, "debug_tuportal_login_fallo.png")
                raise RuntimeError(
                    f"Login tuportal-internet falló tras {MAX_REINTENTOS} intentos."
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


# El id del boton "Consultar" ha cambiado antes (SRI actualiza el HTML periodicamente),
# por eso se ubica por texto visible dentro del form en vez de un id fijo.
_SELECTOR_BTN_CONSULTAR = '#frmPrincipal button:has-text("Consultar")'

# Mensaje que el SRI muestra cuando la consulta es valida pero no hay comprobantes
# para esos parametros (no es un error, es un resultado de 0 filas legitimo).
_SELECTOR_SIN_DATOS = '.ui-messages-warn:has-text("No existen datos")'

_SELECTOR_ANO = _frm(":ano")
_SELECTOR_MES = _frm(":mes")
_SELECTOR_DIA = _frm(":dia")
_SELECTOR_TIPO_COMPROBANTE = _frm(":cmbTipoComprobante")

_CAMPOS_FORMULARIO = [
    _SELECTOR_ANO,
    _SELECTOR_MES,
    _SELECTOR_DIA,
    _SELECTOR_TIPO_COMPROBANTE,
    _SELECTOR_BTN_CONSULTAR,
]


async def aplicar_filtros(
    page: Page,
    anio: str,
    mes: str,
    dia: str,
    tipo_comprobante: str,
    lectura_completa: bool = True,
) -> None:
    """
    Selecciona los filtros en el formulario frmPrincipal y hace click en Consultar.
    Espera que la tabla de resultados cargue antes de retornar.

    `lectura_completa=False` omite las fases 1 y 2 (lectura larga de la pagina
    y recorrido visual del formulario). Solo debe usarse para el 2do+ tipo de
    comprobante pedido en la MISMA corrida ya autenticada, donde la sesion ya
    establecio confianza con este mismo formulario en el envio anterior — el
    envio en si (fases 3-6, que es lo que reCAPTCHA puntua) no cambia.
    """
    logger.info("Aplicando filtros: anio=%s mes=%s dia=%s tipo=%s", anio, mes, dia, tipo_comprobante)

    if lectura_completa:
        # Fase 1: lectura larga de la pagina — reCAPTCHA evalua el tiempo y movimiento
        # desde que carga hasta la primera interaccion
        await simular_lectura(page, segundos_min=8.0, segundos_max=14.0)

        # Fase 2: revisar visualmente los campos del formulario antes de tocarlos
        await simular_lectura_formulario(page, _CAMPOS_FORMULARIO)

    # Fase 3: seleccionar filtros con pausas largas (humano que piensa entre selecciones)
    await seleccionar_opcion(page, _SELECTOR_ANO, anio)
    await pausa(2000, 4000)
    await seleccionar_opcion(page, _SELECTOR_MES, mes)
    await pausa(2000, 4000)
    await seleccionar_opcion(page, _SELECTOR_DIA, dia)
    await pausa(2000, 3500)
    await seleccionar_opcion(page, _SELECTOR_TIPO_COMPROBANTE, tipo_comprobante)

    # Fase 4: pausa larga post-seleccion — como si el usuario revisara antes de enviar
    await pausa(3000, 6000)

    # Fase 5: acercarse lentamente al boton Consultar (movimiento deliberado)
    await acercarse_lento(page, _SELECTOR_BTN_CONSULTAR)

    # Fase 6: click en Consultar
    try:
        async with page.expect_response(
            lambda r: "comprobantesRecibidos" in r.url and r.request.method == "POST",
            timeout=TIMEOUT_AJAX,
        ):
            await mover_y_click(page, _SELECTOR_BTN_CONSULTAR)
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
            f"{_frm(':tablaCompRecibidos_data')} tr, "
            ".ui-datatable-empty-message, "
            f"{_SELECTOR_SIN_DATOS}",
            timeout=TIMEOUT_TABLA,
        )
    except PlaywrightTimeout:
        await _verificar_error_captcha(page)
        await _tomar_screenshot_debug(page, "debug_tabla_no_cargo.png")
        try:
            html = await page.content()
            with open("debug_tabla_no_cargo.html", "w", encoding="utf-8") as f:
                f.write(html)
        except Exception:
            pass
        mensaje_servidor = await _texto_mensajes_servidor(page)
        detalle = f" Mensaje del servidor: {mensaje_servidor}" if mensaje_servidor else ""
        raise RuntimeError(f"La tabla de resultados no cargó después de aplicar los filtros.{detalle}")

    await pausa(500, 1000)


async def obtener_filas_pagina(page: Page, tipo_comprobante: str) -> list[FilaComprobante]:
    """Extrae todas las filas visibles de la tabla en la pagina actual."""
    filas = []
    rows = page.locator(f"{_frm(':tablaCompRecibidos_data')} tr")
    total = await rows.count()

    for i in range(total):
        row = rows.nth(i)
        # La fila placeholder "sin resultados" de PrimeFaces (misma clase que
        # esperar_tabla ya usa para reconocer una consulta valida con 0 filas)
        # tiene un <td> real dentro -- sin este chequeo se colaba como si fuera
        # un comprobante mas, con un tipo_y_serie basura, y el orquestador
        # intentaba (y fallaba) descargarle XML/PDF por cada consulta vacia.
        clase_fila = await row.get_attribute("class") or ""
        if "ui-datatable-empty-message" in clase_fila:
            continue
        celdas = row.locator("td")
        if await celdas.count() == 0:
            continue

        # PrimeFaces expone en `data-ri` el indice REAL de la fila (usado en los
        # ids de sus botones, ej. "tablaCompRecibidos:50:lnkXml"). No es lo mismo
        # que la posicion local `i` dentro de la pagina actual: en la pagina 2+,
        # `data-ri` sigue numerando desde donde quedo la pagina 1 (ej. 50, 51...)
        # mientras que `i` siempre reinicia en 0. Usar `i` aqui hacia que la
        # descarga de XML/PDF apuntara a selectores que no existen en paginas
        # distintas de la primera (no descargaba nada, sin importar los reintentos).
        indice_real = i
        data_ri = await row.get_attribute("data-ri")
        if data_ri is not None and data_ri.strip().isdigit():
            indice_real = int(data_ri)

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

        # Columna "RUC y Razon social emisor" trae ambas lineas separadas por <br>
        # (ej. "0990017514001\nTIENDAS INDUSTRIALES ASOCIADAS TIA S. A."); la razon
        # social es la ultima linea. Columna "Fecha emision" es la unica con formato
        # de solo fecha (DD/MM/YYYY) -- "Fecha y hora de autorizacion" tambien tiene
        # hora, por eso se exige match exacto sin sufijo de hora.
        razon_social = ""
        fecha_emision = ""
        for col_idx in range(await celdas.count()):
            texto = (await celdas.nth(col_idx).inner_text()).strip()
            if not texto:
                continue
            if not razon_social and re.match(r"^\d{13}", texto):
                lineas = [l.strip() for l in texto.splitlines() if l.strip()]
                if len(lineas) > 1:
                    razon_social = lineas[-1]
            if not fecha_emision and re.match(r"^\d{2}/\d{2}/\d{4}$", texto):
                fecha_emision = texto

        es_retencion = tipo_comprobante == "6"
        filas.append(FilaComprobante(
            indice=indice_real,
            tipo_y_serie=tipo_y_serie,
            razon_social=razon_social,
            fecha_emision=fecha_emision,
            es_retencion=es_retencion,
        ))

    return filas


async def _pagina_actual(page: Page) -> int:
    """Lee el numero de pagina actual del texto "(X of Y)" del paginador."""
    try:
        actual = page.locator(".ui-paginator-current")
        if await actual.count() == 0:
            return 1
        texto = (await actual.first.inner_text()).strip()
        m = re.search(r"(\d+)\s+of\s+\d+", texto, re.IGNORECASE)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 1


async def total_paginas(page: Page) -> int:
    """
    Detecta cuantas paginas tiene el paginador de la tabla, leyendo el texto
    "(X of Y)" del elemento .ui-paginator-current.

    OJO: este paginador NO usa links numerados por pagina (".ui-paginator-page"
    no existe aqui) — solo iconos primera/anterior/siguiente/ultima mas ese
    texto. Contar ".ui-paginator-page" siempre da 0 y hacia que el scraper
    creyera que solo habia 1 pagina sin importar cuantos resultados reales
    hubiera (con 50 filas por pagina por defecto, esto se perdia todo lo que
    excediera la primera pagina de forma silenciosa).
    """
    try:
        actual = page.locator(".ui-paginator-current")
        if await actual.count() == 0:
            return 1
        texto = (await actual.first.inner_text()).strip()
        m = re.search(r"of\s+(\d+)", texto, re.IGNORECASE)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 1


async def _click_flecha_paginador(page: Page, selector: str) -> bool:
    """
    Click en una flecha del paginador (primera/anterior/siguiente/ultima), si
    no esta deshabilitada. Retorna True si hizo click y la respuesta AJAX llego
    a tiempo. Si la respuesta no llega, NO propaga la excepcion — solo retorna
    False, para que un problema puntual al paginar no tumbe todo el proceso
    (antes esto subia como excepcion no controlada hasta el orquestador y
    marcaba TODA la corrida como "Error inesperado", no solo esta pagina).
    """
    boton = page.locator(selector)
    if await boton.count() == 0:
        return False
    clases = await boton.first.get_attribute("class") or ""
    if "ui-state-disabled" in clases:
        return False
    try:
        async with page.expect_response(
            lambda r: "comprobantesRecibidos" in r.url and r.request.method == "POST",
            timeout=TIMEOUT_AJAX,
        ):
            await boton.first.click()
    except PlaywrightTimeout:
        logger.warning("Timeout esperando respuesta AJAX del paginador (%s)", selector)
        return False
    # Paginar dentro de una consulta ya exitosa no es una accion puntuada por
    # reCAPTCHA (a diferencia del submit de aplicar_filtros), asi que aqui no
    # hace falta ritmo humanizado -- solo un margen minimo para que PrimeFaces
    # termine de actualizar el DOM de la tabla tras la respuesta AJAX.
    await pausa(100, 250)
    return True


async def ir_a_pagina(page: Page, numero: int) -> None:
    """
    Navega a la pagina `numero` del paginador.

    Este paginador no permite salto directo por numero de pagina (no hay links
    "1", "2", "3"): solo flechas de primera/anterior/siguiente/ultima. Para
    llegar a `numero` se resetea a la primera pagina si hace falta retroceder,
    y luego se hace click en "siguiente" las veces necesarias.

    Despues de cada click se relee el numero de pagina real del DOM (no se
    asume que avanzo solo porque la respuesta AJAX llego) — si el paginador
    no avanza pese al click, se registra advertencia y se detiene en la
    pagina donde quedo, en vez de quedar en un loop o desincronizarse con el
    contador interno.
    """
    actual = await _pagina_actual(page)
    if actual == numero:
        return

    if numero < actual:
        if not await _click_flecha_paginador(page, ".ui-paginator-first"):
            logger.warning("No se pudo volver a la primera pagina del paginador.")
            return
        actual = await _pagina_actual(page)

    intentos_restantes = max(numero - actual, 0) + 2  # pequeno margen de seguridad
    while actual < numero and intentos_restantes > 0:
        if not await _click_flecha_paginador(page, ".ui-paginator-next"):
            logger.warning(
                "No se pudo avanzar de la pagina %d a la %d (paginador no respondio).",
                actual, numero,
            )
            break
        nueva_actual = await _pagina_actual(page)
        if nueva_actual <= actual:
            logger.warning(
                "El paginador no avanzo tras click en 'siguiente' (seguia en pagina %d).",
                actual,
            )
            break
        actual = nueva_actual
        intentos_restantes -= 1


async def descargar_reporte(page: Page, ruta_destino: str) -> bool:
    """
    Descarga el reporte TXT haciendo click en el link 'Descargar reporte'.
    Retorna True si la descarga fue exitosa.
    """
    try:
        async with page.expect_download(timeout=30_000) as dl_info:
            await click_directo(page, _frm(":lnkTxtlistado"))
        descarga = await dl_info.value
        await descarga.save_as(ruta_destino)
        logger.info("Reporte descargado: %s", ruta_destino)
        return True
    except Exception as e:
        logger.warning("No se pudo descargar el reporte: %s", e)
        return False


async def descargar_xml(page: Page, indice: int, ruta_destino: str) -> bool:
    """Descarga el XML de la fila en el indice dado."""
    selector = _frm(f":{indice}:lnkXml")
    return await _descargar_doc(page, selector, ruta_destino, "XML")


async def descargar_pdf(page: Page, indice: int, ruta_destino: str) -> bool:
    """Descarga el PDF/RIDE de la fila en el indice dado."""
    selector = _frm(f":{indice}:lnkPdf")
    return await _descargar_doc(page, selector, ruta_destino, "PDF")


async def _descargar_doc(page: Page, selector: str, ruta_destino: str, tipo: str) -> bool:
    for intento in range(1, 4):
        try:
            async with page.expect_download(timeout=20_000) as dl_info:
                await click_directo(page, selector)
            descarga = await dl_info.value
            await descarga.save_as(ruta_destino)
            logger.debug("%s descargado: %s", tipo, ruta_destino)
            return True
        except Exception as e:
            logger.warning("Intento %d/%d fallido para %s (%s): %s", intento, 3, tipo, selector, e)
            await pausa(500, 1000)
    return False
