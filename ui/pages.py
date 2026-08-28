"""
Paginas NiceGUI del flujo completo de la aplicacion.

Flujo: Login interno -> Credenciales SRI -> Configuracion consulta -> Progreso -> Resultado
"""

import asyncio
import logging
import re
from calendar import monthrange
from datetime import datetime
from pathlib import Path

from nicegui import app, ui

from auth.colaborador import validar_colaborador, validar_formato
from sri_scraper.orchestrator import ScrapingOrchestrator, ParametrosConsulta, EstadoProceso
from ui.components import (
    header, stepper, divider, barra_progreso, panel_intervencion, panel_errores,
    centrar_contenido, btn_primario, btn_secundario,
)
from ui.styles import (
    CSS_GLOBAL, MAGENTA, BLANCO, BORDER_COLOR, FONDO,
    NAVY as GRIS_OSCURO, NAVY_MED as GRIS_MEDIO, FONDO as GRIS_CLARO,
    NAVY_LIGHT, ERROR_COLOR, SUCCESS_COLOR,
)

logger = logging.getLogger(__name__)

ANIOS = [str(a) for a in range(datetime.now().year, 2018, -1)]
MESES_OPCIONES = [
    ("1", "Enero"), ("2", "Febrero"), ("3", "Marzo"), ("4", "Abril"),
    ("5", "Mayo"), ("6", "Junio"), ("7", "Julio"), ("8", "Agosto"),
    ("9", "Septiembre"), ("10", "Octubre"), ("11", "Noviembre"), ("12", "Diciembre"),
]
DIAS_OPCIONES = [("0", "Todos")] + [(str(d), str(d)) for d in range(1, 32)]
TIPOS_COMPROBANTE = [
    ("1", "Factura"),
    ("2", "Liquidacion de compra de bienes y prestacion de servicios"),
    ("3", "Nota de Credito"),
    ("4", "Nota de Debito"),
    ("6", "Comprobante de Retencion"),
]
_DICT_MESES = dict(MESES_OPCIONES)
_DICT_TIPOS = dict(TIPOS_COMPROBANTE)


def _validar_periodo(anio: str, mes: str, dia: str) -> str:
    """
    Valida el periodo antes de arrancar la corrida.

    Se hace aca (y no despues de loguear en el SRI) porque una fecha futura o
    inexistente nunca va a devolver comprobantes: abrir el navegador y loguear
    solo para recibir "No existen datos" gasta tiempo y suma un login extra que
    le baja el score a reCAPTCHA sin ninguna necesidad.

    Devuelve el mensaje de error, o cadena vacia si el periodo es valido.
    """
    try:
        a, m = int(anio), int(mes)
        d = int(dia or 0)
    except (TypeError, ValueError):
        return "Seleccione un ano, mes y dia validos."

    hoy = datetime.now()
    nombre_mes = _DICT_MESES.get(str(m), str(m))

    if (a, m) > (hoy.year, hoy.month):
        return (
            f"{nombre_mes} {a} todavia no ocurre. Seleccione un periodo hasta "
            f"{_DICT_MESES.get(str(hoy.month), '')} {hoy.year}."
        )

    if d:
        dias_del_mes = monthrange(a, m)[1]
        if d > dias_del_mes:
            return f"{nombre_mes} {a} solo tiene {dias_del_mes} dias. Corrija el dia seleccionado."
        if (a, m, d) > (hoy.year, hoy.month, hoy.day):
            return (
                f"El {d} de {nombre_mes} de {a} todavia no llega. "
                "Seleccione una fecha pasada o use el dia \"Todos\"."
            )

    return ""

_orquestador_activo: ScrapingOrchestrator | None = None


# ── Helpers de sesiOn ─────────────────────────────────────────────────────────

def _sesion() -> dict:
    s = app.storage.user
    if "paso" not in s:
        s.update({
            "paso": 1,
            "autenticado": False,
            "codigo_colaborador": "",
            "nombre_usuario_banco": "",
            "nombre_completo_colaborador": "",
            "sri_ruc": "",
            "sri_ci_adicional": "",
            "sri_password": "",
            "anio": str(datetime.now().year),
            "mes": str(datetime.now().month),
            "dia": "0",
            "tipo_comprobante": "1",
            "tipos_comprobante": ["1"],
            "proceso_activo": False,
            "fase_proceso": "",
            "total_comprobantes": 0,
            "completados": 0,
            "ya_existian": 0,
            "nuevos_descargados": 0,
            "archivos_nuevos": [],
            "errores_proceso": [],
            "requiere_intervencion": False,
            "mensaje_intervencion": "",
            "proceso_finalizado": False,
            "proceso_cancelado": False,
        })
    return s


# ── Helpers de UI (todos con inline styles para evitar conflictos de Quasar) ──

def _card(max_width: str = "480px"):
    return ui.element("div").style(
        f"background:{BLANCO}; border:1px solid {BORDER_COLOR}; border-radius:14px; "
        f"padding:36px 40px; max-width:{max_width}; width:100%; "
        f"box-shadow:0 4px 24px rgba(22,15,65,0.08);"
    )


def _titulo(texto: str, color: str = ""):
    c = color or GRIS_OSCURO
    ui.label(texto).style(
        f"font-size:22px; font-weight:700; color:{c}; margin-bottom:6px; display:block; "
        f"font-family:'Inter',sans-serif; letter-spacing:-0.3px;"
    )


def _subtitulo(texto: str):
    ui.label(texto).style(
        f"font-size:14px; color:{GRIS_MEDIO}; margin-bottom:24px; "
        f"display:block; line-height:1.55; font-family:'Inter',sans-serif;"
    )


def _label_campo(texto: str):
    ui.label(texto).style(
        f"font-size:13px; font-weight:600; color:{GRIS_OSCURO}; "
        f"margin-bottom:5px; display:block; font-family:'Inter',sans-serif;"
    )


def _lbl_error() -> ui.label:
    return ui.label("").style(
        f"font-size:12px; color:{ERROR_COLOR}; min-height:16px; display:block; "
        f"margin-top:3px; font-family:'Inter',sans-serif;"
    )


def _input_separador():
    ui.element("div").style("margin-top:16px;")


# ─────────────────────────────────────────────────────────────────────────────
# PAGINA 1: Login interno
# ─────────────────────────────────────────────────────────────────────────────
@ui.page("/")
async def pagina_login():
    ui.add_head_html(CSS_GLOBAL)
    s = _sesion()

    if s.get("autenticado"):
        ui.navigate.to("/credenciales-sri")
        return

    header()

    with centrar_contenido():
        with _card():
            stepper(1)
            _titulo("Acceso al sistema")
            _subtitulo("Ingrese sus credenciales de colaborador Banco Guayaquil")
            divider()

            _label_campo("Codigo de trabajador")
            codigo_input = (
                ui.input(placeholder="Ej: 10000")
                .props("outlined dense")
                .style("width:100%;")
            )
            lbl_err_codigo = _lbl_error()

            _input_separador()
            _label_campo("Nombre de usuario")
            usuario_input = (
                ui.input(placeholder="Ej: jperez")
                .props("outlined dense")
                .style("width:100%;")
            )
            lbl_err_usuario = _lbl_error()

            lbl_err_general = ui.label("").style(
                f"font-size:13px; color:{ERROR_COLOR}; min-height:18px; "
                f"display:block; margin-top:10px;"
            )
            spinner = ui.spinner(size="sm", color=MAGENTA).style("display:none; margin-top:8px;")

            async def hacer_login():
                lbl_err_codigo.set_text("")
                lbl_err_usuario.set_text("")
                lbl_err_general.set_text("")

                codigo = (codigo_input.value or "").strip()
                usuario = (usuario_input.value or "").strip()

                ok, msg = validar_formato(codigo, usuario)
                if not ok:
                    if "codigo" in msg.lower() or "numerico" in msg.lower():
                        lbl_err_codigo.set_text(msg)
                    else:
                        lbl_err_usuario.set_text(msg)
                    return

                spinner.style("display:inline-block;")
                btn_ingresar.disable()
                try:
                    valido, msg_bd, colaborador = await asyncio.to_thread(validar_colaborador, codigo, usuario)
                finally:
                    spinner.style("display:none;")
                    btn_ingresar.enable()

                if not valido or colaborador is None:
                    lbl_err_general.set_text(msg_bd or "Acceso no autorizado.")
                    return

                s["autenticado"] = True
                s["codigo_colaborador"] = colaborador.codigo
                s["nombre_usuario_banco"] = colaborador.usuario
                s["nombre_completo_colaborador"] = colaborador.nombre_completo
                s["paso"] = 2
                ui.navigate.to("/credenciales-sri")

            def entrar_como_invitado():
                s["autenticado"] = True
                s["codigo_colaborador"] = "INVITADO"
                s["nombre_usuario_banco"] = "INVITADO"
                s["nombre_completo_colaborador"] = "Invitado"
                s["paso"] = 2
                ui.navigate.to("/credenciales-sri")

            codigo_input.on("keydown.enter", hacer_login)
            usuario_input.on("keydown.enter", hacer_login)

            ui.element("div").style("margin-top:24px;")
            btn_ingresar = btn_primario("Ingresar", on_click=hacer_login, ancho_completo=True)

            ui.element("div").style("margin-top:10px;")
            btn_secundario("Ingresar como invitado", on_click=entrar_como_invitado).style("width:100%;")


# ─────────────────────────────────────────────────────────────────────────────
# PAGINA 2: Credenciales SRI
# ─────────────────────────────────────────────────────────────────────────────
@ui.page("/credenciales-sri")
async def pagina_credenciales_sri():
    ui.add_head_html(CSS_GLOBAL)
    s = _sesion()

    if not s.get("autenticado"):
        ui.navigate.to("/")
        return

    header()

    with centrar_contenido():
        with _card():
            stepper(2)

            with ui.element("div").style(
                "display:flex; justify-content:space-between; "
                "align-items:flex-start; gap:12px; margin-bottom:4px;"
            ):
                _titulo("Credenciales SRI del contribuyente")
                ui.label(f"Usuario: {s.get('nombre_usuario_banco', '').upper()}").style(
                    f"font-size:11px; color:{GRIS_MEDIO}; margin-top:6px; "
                    f"white-space:nowrap; flex-shrink:0;"
                )

            _subtitulo(
                "Ingrese las credenciales del portal SRI en linea del contribuyente. "
                "Estos datos no se almacenan en disco."
            )

            # Aviso de bloqueo
            with ui.element("div").style(
                f"background:#FFFBEB; border:1.5px solid #F59E0B; border-radius:8px; "
                f"padding:12px 16px; margin-bottom:20px;"
            ):
                ui.label(
                    "El SRI bloquea la cuenta tras varios intentos fallidos. "
                    "Verifique bien su clave antes de continuar — se le pedira confirmarla."
                ).style(
                    f"font-size:13px; color:#92400E; font-family:'Inter',sans-serif; line-height:1.5;"
                )

            divider()

            _label_campo("RUC o Cedula")
            ruc_input = (
                ui.input(placeholder="13 digitos RUC / 10 digitos cedula")
                .props("outlined dense")
                .style("width:100%;")
            )
            lbl_err_ruc = _lbl_error()

            _input_separador()
            _label_campo("Cedula adicional (opcional)")
            ci_adicional_input = (
                ui.input(placeholder="10 digitos - solo si el contribuyente le dio acceso delegado")
                .props("outlined dense maxlength=10")
                .style("width:100%;")
            )
            ui.label(
                "Dejar en blanco si va a ingresar con el RUC y clave originales del contribuyente."
            ).style(f"font-size:12px; color:{GRIS_MEDIO}; margin-top:4px; display:block;")
            lbl_err_ci_adicional = _lbl_error()

            _input_separador()
            _label_campo("Contrasena SRI")
            pass_input = (
                ui.input(password=True, placeholder="Clave SRI")
                .props("outlined dense")
                .style("width:100%;")
            )
            lbl_err_pass = _lbl_error()

            _input_separador()
            _label_campo("Confirmar contrasena SRI")
            pass_confirm_input = (
                ui.input(password=True, placeholder="Repita su clave SRI")
                .props("outlined dense")
                .style("width:100%;")
            )
            lbl_err_confirm = _lbl_error()

            def toggle_password(e):
                tipo = "type=text" if e.value else "type=password"
                pass_input.props(tipo)
                pass_confirm_input.props(tipo)

            ui.checkbox("Mostrar contrasenas", on_change=toggle_password).style(
                f"font-size:12px; color:{GRIS_MEDIO}; margin-top:6px;"
            )

            lbl_err_gen = ui.label("").style(
                f"font-size:13px; color:{ERROR_COLOR}; min-height:18px; "
                f"display:block; margin-top:10px;"
            )

            def _validar_ruc(ruc: str) -> str:
                """Retorna mensaje de error o cadena vacia si es valido."""
                if not ruc:
                    return "El RUC o cedula es obligatorio."
                return ""

            def _validar_ci_adicional(ci: str) -> str:
                if not ci:
                    return ""
                if not re.match(r"^\d{10}$", ci):
                    return "La cedula adicional debe tener 10 digitos numericos."
                return ""

            def validar_y_continuar():
                lbl_err_ruc.set_text("")
                lbl_err_ci_adicional.set_text("")
                lbl_err_pass.set_text("")
                lbl_err_confirm.set_text("")
                lbl_err_gen.set_text("")

                ruc = (ruc_input.value or "").strip()
                ci_adicional = (ci_adicional_input.value or "").strip()
                pwd = pass_input.value or ""
                pwd_confirm = pass_confirm_input.value or ""

                err_ruc = _validar_ruc(ruc)
                if err_ruc:
                    lbl_err_ruc.set_text(err_ruc)
                    return

                err_ci_adicional = _validar_ci_adicional(ci_adicional)
                if err_ci_adicional:
                    lbl_err_ci_adicional.set_text(err_ci_adicional)
                    return

                if not pwd:
                    lbl_err_pass.set_text("La contrasena es obligatoria.")
                    return
                if len(pwd) < 6:
                    lbl_err_pass.set_text("La contrasena debe tener al menos 6 caracteres.")
                    return

                if not pwd_confirm:
                    lbl_err_confirm.set_text("Por favor confirme su contrasena.")
                    return
                if pwd != pwd_confirm:
                    lbl_err_confirm.set_text("Las contrasenas no coinciden. Verifique e intente de nuevo.")
                    pass_confirm_input.value = ""
                    return

                s["sri_ruc"] = ruc
                s["sri_ci_adicional"] = ci_adicional
                s["sri_password"] = pwd
                s["paso"] = 3
                ui.navigate.to("/configuracion")

            ruc_input.on("keydown.enter", validar_y_continuar)
            ci_adicional_input.on("keydown.enter", validar_y_continuar)
            pass_input.on("keydown.enter", validar_y_continuar)
            pass_confirm_input.on("keydown.enter", validar_y_continuar)

            with ui.element("div").style("display:flex; gap:10px; margin-top:24px;"):
                btn_secundario("Atras", on_click=lambda: ui.navigate.to("/"))
                btn_primario("Continuar", on_click=validar_y_continuar).style("flex:1;")


# ─────────────────────────────────────────────────────────────────────────────
# PAGINA 3: Configuracion de consulta
# ─────────────────────────────────────────────────────────────────────────────
@ui.page("/configuracion")
async def pagina_configuracion():
    ui.add_head_html(CSS_GLOBAL)
    s = _sesion()

    if not s.get("autenticado"):
        ui.navigate.to("/")
        return
    if not s.get("sri_ruc"):
        ui.navigate.to("/credenciales-sri")
        return

    header()

    with centrar_contenido():
        with _card("540px"):
            stepper(3)
            _titulo("Configuracion de consulta")
            _subtitulo(
                f"RUC/Cedula: {s.get('sri_ruc', '')}  —  "
                "Define el periodo y tipo de comprobante a descargar."
            )
            divider()

            # Ano / Mes
            with ui.element("div").style("display:flex; gap:16px;"):
                with ui.element("div").style("flex:1;"):
                    _label_campo("Ano")
                    anio_sel = (
                        ui.select(options=ANIOS, value=s.get("anio", str(datetime.now().year)))
                        .props("outlined dense")
                        .style("width:100%;")
                    )
                with ui.element("div").style("flex:1;"):
                    _label_campo("Mes")
                    mes_sel = (
                        ui.select(
                            options={v: l for v, l in MESES_OPCIONES},
                            value=s.get("mes", str(datetime.now().month)),
                        )
                        .props("outlined dense")
                        .style("width:100%;")
                    )

            ui.element("div").style("margin-top:16px;")

            # Dia / Tipo
            with ui.element("div").style("display:flex; gap:16px;"):
                with ui.element("div").style("flex:0 0 110px;"):
                    _label_campo("Dia")
                    dia_sel = (
                        ui.select(
                            options={v: l for v, l in DIAS_OPCIONES},
                            value=s.get("dia", "0"),
                        )
                        .props("outlined dense")
                        .style("width:100%;")
                    )
                with ui.element("div").style("flex:1;"):
                    _label_campo("Tipo(s) de comprobante")
                    valor_inicial = s.get("tipos_comprobante") or [s.get("tipo_comprobante", "1")]
                    tipo_sel = (
                        ui.select(
                            options={v: l for v, l in TIPOS_COMPROBANTE},
                            value=valor_inicial,
                            multiple=True,
                        )
                        .props("outlined dense use-chips clearable")
                        .style("width:100%;")
                    )
            lbl_err_tipo = _lbl_error()
            lbl_err_periodo = _lbl_error()

            ui.label(
                "Puede seleccionar mas de un tipo (por ejemplo Factura y Comprobante de "
                "Retencion): se descargaran uno tras otro, en una sola corrida, para el "
                "mismo RUC y periodo."
            ).style(f"font-size:12px; color:{GRIS_MEDIO}; margin-top:4px; line-height:1.5;")

            ui.element("div").style("margin-top:20px;")
            with ui.element("div").style(
                f"background:{GRIS_CLARO}; border:1px solid {BORDER_COLOR}; "
                f"border-radius:4px; padding:12px 14px;"
            ):
                ui.label(
                    "El proceso descargara automaticamente el reporte del periodo y los archivos "
                    "XML y PDF de cada comprobante. Si hay mas de una pagina se procesaran todas."
                ).style(f"font-size:12px; color:{GRIS_MEDIO}; line-height:1.5;")

            def iniciar_descarga():
                lbl_err_tipo.set_text("")
                lbl_err_periodo.set_text("")
                tipos = tipo_sel.value or []
                if not tipos:
                    lbl_err_tipo.set_text("Seleccione al menos un tipo de comprobante.")
                    return

                error_periodo = _validar_periodo(anio_sel.value, mes_sel.value, dia_sel.value)
                if error_periodo:
                    lbl_err_periodo.set_text(error_periodo)
                    ui.notify(error_periodo, type="warning", position="top")
                    return

                s["anio"] = anio_sel.value
                s["mes"] = mes_sel.value
                s["dia"] = dia_sel.value
                s["tipos_comprobante"] = list(tipos)
                s["tipo_comprobante"] = tipos[0]
                s["paso"] = 4
                s["proceso_activo"] = True
                s["proceso_finalizado"] = False
                s["proceso_cancelado"] = False
                s["errores_proceso"] = []
                s["completados"] = 0
                s["total_comprobantes"] = 0
                s["ya_existian"] = 0
                s["nuevos_descargados"] = 0
                s["archivos_nuevos"] = []
                ui.navigate.to("/descarga")

            with ui.element("div").style("display:flex; gap:10px; margin-top:24px;"):
                btn_secundario("Atras", on_click=lambda: ui.navigate.to("/credenciales-sri"))
                btn_primario("Iniciar descarga", on_click=iniciar_descarga).style("flex:1;")


# ─────────────────────────────────────────────────────────────────────────────
# PAGINA 4: Progreso de descarga
# ─────────────────────────────────────────────────────────────────────────────
@ui.page("/descarga")
async def pagina_descarga():
    global _orquestador_activo
    ui.add_head_html(CSS_GLOBAL)
    s = _sesion()

    if not s.get("autenticado"):
        ui.navigate.to("/")
        return

    header()
    btn_cancelar_ref: list = []

    with centrar_contenido():
        with _card("580px"):
            stepper(4)
            _titulo("Proceso de descarga en curso")
            _tipos_sel = s.get("tipos_comprobante") or [s.get("tipo_comprobante", "1")]
            _tipos_nombres = ", ".join(_DICT_TIPOS.get(t, t) for t in _tipos_sel)
            _subtitulo(
                f"RUC: {s.get('sri_ruc', '')}  |  "
                f"Periodo: {_DICT_MESES.get(s.get('mes',''), '')} {s.get('anio', '')}  |  "
                f"Tipo(s): {_tipos_nombres}"
            )
            divider()

            estado_container = ui.element("div").style("width:100%; min-height:80px;")
            with estado_container:
                with ui.element("div").style(
                    "display:flex; flex-direction:column; align-items:center; padding:20px 0;"
                ):
                    ui.spinner(size="lg", color=MAGENTA)
                    ui.label("Iniciando proceso...").style(
                        f"font-size:13px; color:{GRIS_MEDIO}; margin-top:12px;"
                    )

            divider()
            btn_cancelar = btn_secundario(
                "Cancelar proceso", on_click=lambda: cancelar_proceso()
            ).style("width:100%;")
            btn_cancelar_ref.append(btn_cancelar)

    def cancelar_proceso():
        if _orquestador_activo:
            _orquestador_activo.solicitar_cancelacion()
        s["proceso_cancelado"] = True

    def resolver_intervencion():
        if _orquestador_activo:
            _orquestador_activo.resolver_intervencion()
        s["requiere_intervencion"] = False

    async def actualizar_ui(estado: EstadoProceso):
        s["fase_proceso"] = estado.fase
        s["total_comprobantes"] = estado.total
        s["completados"] = estado.completados
        s["ya_existian"] = estado.ya_existian
        s["nuevos_descargados"] = estado.nuevos_descargados
        s["archivos_nuevos"] = list(estado.archivos_nuevos)
        s["errores_proceso"] = list(estado.errores)
        s["requiere_intervencion"] = estado.requiere_intervencion
        s["mensaje_intervencion"] = estado.mensaje_intervencion
        s["proceso_finalizado"] = estado.finalizado
        s["proceso_cancelado"] = estado.cancelado
        if estado.carpeta_descarga:
            s["carpeta_descarga"] = estado.carpeta_descarga

        estado_container.clear()
        with estado_container:
            if estado.requiere_intervencion:
                panel_intervencion(
                    estado.mensaje_intervencion,
                    on_resolver=resolver_intervencion,
                    on_cancelar=cancelar_proceso,
                )
            elif estado.finalizado or estado.cancelado:
                if btn_cancelar_ref:
                    btn_cancelar_ref[0].style("display:none;")
                s["paso"] = 5
                await asyncio.sleep(0.5)
                ui.navigate.to("/resultado")
            else:
                with ui.element("div").style("width:100%; padding:4px 0;"):
                    multi_tipo = len(estado.tipos_nombres) > 1

                    if multi_tipo:
                        # Chips de todos los tipos pedidos: verde = ya terminado,
                        # magenta = el que se esta procesando ahora, gris = pendiente.
                        # Deja explicito que se cambio de seccion (ej. de Factura a
                        # Comprobante de Retencion) en vez de que se vea como una
                        # continuacion silenciosa de la misma barra.
                        with ui.element("div").style(
                            "display:flex; gap:8px; flex-wrap:wrap; margin-bottom:14px;"
                        ):
                            for nombre in estado.tipos_nombres:
                                if nombre in estado.tipos_finalizados:
                                    bg, color_txt, texto_chip = SUCCESS_COLOR, BLANCO, f"✓ {nombre}"
                                elif nombre == estado.tipo_actual_nombre:
                                    bg, color_txt, texto_chip = MAGENTA, BLANCO, nombre
                                else:
                                    bg, color_txt, texto_chip = NAVY_LIGHT, GRIS_MEDIO, nombre
                                ui.label(texto_chip).style(
                                    f"background:{bg}; color:{color_txt}; padding:6px 14px; "
                                    f"border-radius:20px; font-size:12.5px; font-weight:600; "
                                    f"font-family:'Inter',sans-serif; white-space:nowrap; "
                                    f"display:inline-block;"
                                )

                        ui.label(
                            f"Tipo {estado.tipo_actual_indice} de {len(estado.tipos_nombres)}: "
                            f"{estado.tipo_actual_nombre}"
                        ).style(
                            f"font-size:15px; font-weight:700; color:{GRIS_OSCURO}; "
                            f"display:block; margin-bottom:6px;"
                        )

                    ui.label(estado.fase).style(
                        f"font-size:14px; font-weight:500; color:{GRIS_OSCURO}; "
                        f"margin-bottom:16px; display:block;"
                    )

                    if multi_tipo:
                        if estado.total_tipo_actual > 0:
                            barra_progreso(
                                estado.completados_tipo_actual, estado.total_tipo_actual,
                                f"Comprobantes de {estado.tipo_actual_nombre}",
                            )
                            ui.label(
                                f"Total general: {estado.completados} / {estado.total}"
                            ).style(
                                f"font-size:12px; color:{GRIS_MEDIO}; margin-top:8px; display:block;"
                            )
                        else:
                            with ui.element("div").style(
                                "display:flex; flex-direction:column; align-items:center; padding:8px 0;"
                            ):
                                ui.spinner(size="md", color=MAGENTA)
                    elif estado.total > 0:
                        barra_progreso(estado.completados, estado.total, "Comprobantes descargados")
                    else:
                        with ui.element("div").style(
                            "display:flex; flex-direction:column; align-items:center; padding:8px 0;"
                        ):
                            ui.spinner(size="md", color=MAGENTA)

        ui.update(estado_container)

    async def iniciar_scraping():
        global _orquestador_activo
        tipos = s.get("tipos_comprobante") or [s.get("tipo_comprobante", "1")]

        # Un solo login/navegacion para todos los tipos pedidos: el orquestador
        # reutiliza la misma sesion internamente, solo repite "filtrar + descargar"
        # por cada tipo. Mismo periodo (anio/mes/dia) para todos, tal como los
        # ingreso el usuario en el paso de configuracion.
        params = ParametrosConsulta(
            ruc=s.get("sri_ruc", ""),
            ci_adicional=s.get("sri_ci_adicional", ""),
            password=s.get("sri_password", ""),
            anio=int(s.get("anio", datetime.now().year)),
            mes=int(s.get("mes", 1)),
            dia=int(s.get("dia", 0)),
            tipos_comprobante=tipos,
            codigo_colaborador=s.get("codigo_colaborador", ""),
            usuario_colaborador=s.get("nombre_usuario_banco", ""),
        )

        try:
            orchestrator = ScrapingOrchestrator(params, on_progress=actualizar_ui)
            _orquestador_activo = orchestrator
            await orchestrator.ejecutar()

        except Exception as exc:
            logger.exception("Error no manejado en scraping: %s", exc)
            s["errores_proceso"] = [str(exc)]
            s["proceso_finalizado"] = True
            s["paso"] = 5
            ui.navigate.to("/resultado")
        finally:
            _orquestador_activo = None

    ui.timer(0.1, lambda: asyncio.create_task(iniciar_scraping()), once=True)


# ─────────────────────────────────────────────────────────────────────────────
# PAGINA 5: Resultado y descarga del ZIP
# ─────────────────────────────────────────────────────────────────────────────
@ui.page("/resultado")
async def pagina_resultado():
    ui.add_head_html(CSS_GLOBAL)
    s = _sesion()

    if not s.get("autenticado"):
        ui.navigate.to("/")
        return

    header()

    cancelado = s.get("proceso_cancelado", False)
    errores = s.get("errores_proceso", [])
    completados = s.get("completados", 0)
    total = s.get("total_comprobantes", 0)
    ya_existian = s.get("ya_existian", 0)
    nuevos_descargados = s.get("nuevos_descargados", 0)
    archivos_nuevos = s.get("archivos_nuevos", [])
    error_critico = any(
        kw in e.lower() for e in errores
        for kw in ["credencial", "incorrecto", "acceso", "login"]
    )

    # Definidas antes para poder usarlas en el aviso de "Sin resultados"
    def nueva_consulta():
        s["paso"] = 3
        s["proceso_finalizado"] = False
        s["errores_proceso"] = []
        ui.navigate.to("/configuracion")

    def otro_ruc():
        # Mantiene config (periodo, tipo) pero limpia credenciales SRI
        s.pop("sri_ruc", None)
        s.pop("sri_ci_adicional", None)
        s.pop("sri_password", None)
        s["paso"] = 2
        s["proceso_finalizado"] = False
        s["errores_proceso"] = []
        ui.navigate.to("/credenciales-sri")

    with centrar_contenido():
        with _card("580px"):
            stepper(5)

            if cancelado:
                _titulo("Proceso cancelado", color=GRIS_MEDIO)
                _subtitulo("El proceso fue cancelado. Los archivos parcialmente descargados se mantienen.")

            elif error_critico:
                _titulo("Error en el proceso", color=ERROR_COLOR)
                _subtitulo("No fue posible completar la descarga. Verifique las credenciales e intente nuevamente.")

            elif not completados:
                _titulo("Sin resultados")
                _subtitulo("No se encontraron comprobantes para el periodo y tipo seleccionados.")
                with ui.element("div").style(
                    "background:#FFF3CD; border:1px solid #E6C466; border-radius:8px; "
                    "padding:14px 16px; margin-top:12px; margin-bottom:4px;"
                ):
                    ui.label("¿Deseas intentar con otro mes, dia o tipo de comprobante?").style(
                        f"font-size:13px; color:{GRIS_OSCURO}; display:block; margin-bottom:10px; font-weight:600;"
                    )
                    with ui.element("div").style("display:flex; gap:10px; flex-wrap:wrap;"):
                        btn_primario("Buscar otro periodo", on_click=nueva_consulta)
                        btn_secundario("Probar con otro RUC", on_click=otro_ruc)

            elif nuevos_descargados == 0:
                # Se encontraron comprobantes pero TODOS ya estaban descargados de
                # una corrida anterior: mostrar "Proceso completado" aqui haria
                # parecer que se bajo algo nuevo cuando en realidad no se descargo
                # nada en esta corrida -- se distingue con su propio mensaje para
                # que quede claro que el periodo ya estaba al dia.
                with ui.element("div").style(
                    "background:#EAF6EE; border:1px solid #A3D9B1; border-radius:4px; "
                    "padding:18px; margin-bottom:16px; text-align:center;"
                ):
                    ui.label("Ya esta todo al dia").style(
                        f"font-size:18px; font-weight:700; color:{SUCCESS_COLOR}; display:block;"
                    )
                    ui.label(
                        f"Se revisaron {completados} comprobante{'s' if completados != 1 else ''} "
                        f"de este periodo y ya estaban descargados. No hay nada nuevo."
                    ).style(
                        f"font-size:14px; color:{SUCCESS_COLOR}; margin-top:6px; display:block;"
                    )

            else:
                with ui.element("div").style(
                    "background:#EAF6EE; border:1px solid #A3D9B1; border-radius:4px; "
                    "padding:18px; margin-bottom:16px; text-align:center;"
                ):
                    ui.label("Proceso completado").style(
                        f"font-size:18px; font-weight:700; color:{SUCCESS_COLOR}; display:block;"
                    )
                    conteo = (
                        f"{completados} comprobante{'s' if completados != 1 else ''} "
                        f"descargado{'s' if completados != 1 else ''}"
                        + (f" de {total}" if total > completados else "")
                    )
                    ui.label(conteo).style(
                        f"font-size:14px; color:{SUCCESS_COLOR}; margin-top:6px; display:block;"
                    )
                    # Solo se muestra el desglose cuando hubo archivos omitidos por
                    # ya existir: en una corrida completamente nueva, "nuevos" y
                    # "completados" son el mismo numero y repetirlo no aporta nada.
                    if ya_existian > 0:
                        detalle_nuevos = (
                            "1 nuevo" if nuevos_descargados == 1
                            else f"{nuevos_descargados} nuevos"
                        )
                        detalle_previos = (
                            "1 ya estaba descargado" if ya_existian == 1
                            else f"{ya_existian} ya estaban descargados"
                        )
                        ui.label(f"{detalle_nuevos}  |  {detalle_previos}").style(
                            f"font-size:12px; color:{GRIS_MEDIO}; margin-top:4px; display:block;"
                        )

            # NOTA: la tarjeta de "Guardar credenciales para sincronizacion nocturna"
            # se removio deliberadamente por decision de producto (aun no se va a
            # ofrecer esta opcion). storage/credentials.py (guardar/obtener_ci_adicional)
            # y sync_nocturna.py siguen intactos para cuando se retome -- no se debe
            # restaurar este bloque de UI solo porque un review automatico lo marque
            # como "codigo inalcanzable" sin antes confirmar con el usuario.

            divider()

            # Panel de errores parciales
            if errores:
                panel_errores(errores)
                divider()

            # Guardar archivos en carpeta elegida por el usuario: si no hubo nada
            # nuevo en esta corrida (todo ya estaba descargado de antes), no tiene
            # sentido ofrecer un ZIP aqui -- el usuario ya se llevo esos archivos
            # en una corrida anterior, y mostrar el boton de nuevo sugeriria que
            # hay algo pendiente por bajar cuando no lo hay.
            carpeta_descarga = s.get("carpeta_descarga", "")
            if completados > 0 and carpeta_descarga and nuevos_descargados > 0:
                with ui.element("div").style(
                    f"background:{FONDO}; border:1px solid {BORDER_COLOR}; border-radius:10px; "
                    f"padding:18px 20px; margin-bottom:20px; width:100%;"
                ):
                    ui.label("Guardar archivos").style(
                        f"font-size:15px; font-weight:700; color:{GRIS_OSCURO}; display:block; margin-bottom:6px;"
                    )
                    ui.label(
                        "Descargue un ZIP con todos los comprobantes de esta sesion a su equipo."
                    ).style(
                        f"font-size:13px; color:{GRIS_MEDIO}; display:block; margin-bottom:6px; line-height:1.5;"
                    )
                    # Aclaracion importante para operadores sin acceso a la BD: la
                    # deduplicacion (que no se vuelva a descargar lo mismo) depende
                    # de que estos archivos sigan existiendo en el servidor, NO de
                    # que alguien haya presionado este boton. Sin este aviso, es
                    # facil pensar (incorrectamente) que "guardar el ZIP" es lo que
                    # el sistema usa para recordar, y borrar la carpeta del servidor
                    # pensando que ya no hace falta.
                    ui.label(
                        "Los comprobantes ya quedaron guardados automaticamente en el "
                        "sistema. Este boton solo genera una copia adicional para su "
                        "equipo; no borre la carpeta original del servidor, ya que de "
                        "ahi depende que no se vuelvan a descargar la proxima vez."
                    ).style(
                        f"font-size:12px; color:{GRIS_MEDIO}; font-style:italic; "
                        f"display:block; margin-bottom:14px; line-height:1.5;"
                    )

                    notif_guardar = ui.label("").style(
                        "font-size:13px; display:block; margin-top:10px;"
                    )

                    async def guardar_en_equipo():
                        # Genera el ZIP en el servidor y lo transmite via el navegador
                        # (ui.download) para que llegue realmente al equipo del usuario.
                        # Un selector de carpeta nativo (tkinter) NO sirve aqui: correria
                        # en el servidor donde vive el proceso de NiceGUI, no en la
                        # maquina del usuario que abre la pagina desde su navegador.
                        notif_guardar.set_text("Generando archivo ZIP...")
                        notif_guardar.style(f"color:{GRIS_MEDIO};")
                        try:
                            contenido = await asyncio.to_thread(_generar_zip_carpeta, carpeta_descarga)
                            nombre_zip = f"{Path(carpeta_descarga).name}.zip"
                            ui.download(contenido, nombre_zip)
                            notif_guardar.set_text(f"Descarga iniciada: {nombre_zip}")
                            notif_guardar.style(f"color:{SUCCESS_COLOR};")
                        except Exception as exc:
                            notif_guardar.set_text(f"Error al generar el archivo: {exc}")
                            notif_guardar.style(f"color:{ERROR_COLOR};")

                    # Solo tiene sentido ofrecer un ZIP separado de "lo nuevo" cuando
                    # esta corrida realmente omitio archivos por ya existir: si todo
                    # es nuevo, este boton descargaria exactamente lo mismo que
                    # "Guardar en mi equipo" y solo confundiria.
                    if ya_existian > 0 and archivos_nuevos:
                        with ui.element("div").style("display:flex; gap:10px; margin-top:10px; flex-wrap:wrap;"):
                            btn_primario("Guardar en mi equipo", on_click=guardar_en_equipo)

                            async def guardar_solo_nuevos():
                                notif_guardar.set_text("Generando archivo ZIP...")
                                notif_guardar.style(f"color:{GRIS_MEDIO};")
                                try:
                                    contenido = await asyncio.to_thread(
                                        _generar_zip_archivos, carpeta_descarga, archivos_nuevos,
                                    )
                                    nombre_zip = f"{Path(carpeta_descarga).name}_nuevos.zip"
                                    ui.download(contenido, nombre_zip)
                                    notif_guardar.set_text(f"Descarga iniciada: {nombre_zip}")
                                    notif_guardar.style(f"color:{SUCCESS_COLOR};")
                                except Exception as exc:
                                    notif_guardar.set_text(f"Error al generar el archivo: {exc}")
                                    notif_guardar.style(f"color:{ERROR_COLOR};")

                            btn_secundario(
                                f"Descargar solo lo nuevo ({nuevos_descargados})",
                                on_click=guardar_solo_nuevos,
                            )
                    else:
                        btn_primario("Guardar en mi equipo", on_click=guardar_en_equipo)

            # Acciones
            with ui.element("div").style("display:flex; gap:10px; margin-top:8px; flex-wrap:wrap;"):
                btn_secundario("Nueva consulta", on_click=nueva_consulta)
                btn_primario("Descargar otro RUC", on_click=otro_ruc)


# ─────────────────────────────────────────────────────────────────────────────
# Historial de consultas
# ─────────────────────────────────────────────────────────────────────────────

MESES_NOMBRE = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

_ESTADO_BADGE = {
    "COMPLETADO": (SUCCESS_COLOR, "#D1FAE5"),
    "PARCIAL":    ("#92400E",    "#FEF3C7"),
    "CANCELADO":  (GRIS_MEDIO,  "#E7E7EC"),
    "ERROR":      (ERROR_COLOR,  "#FEE2E2"),
}


@ui.page("/historial")
async def pagina_historial():
    from config import settings as cfg
    from storage.db_logger import consultar_sesiones

    ui.add_head_html(CSS_GLOBAL)
    s = _sesion()

    if not s.get("autenticado"):
        ui.navigate.to("/")
        return

    codigo_colaborador = s.get("codigo_colaborador", "")

    header()

    with centrar_contenido():
        with ui.element("div").style(
            f"background:{BLANCO}; border:1px solid {BORDER_COLOR}; border-radius:14px; "
            f"padding:32px 40px; width:100%; max-width:1200px; "
            f"box-shadow:0 4px 24px rgba(22,15,65,0.08);"
        ):
            # Titulo
            ui.label("Historial de consultas").style(
                f"font-size:20px; font-weight:600; color:{GRIS_OSCURO}; display:block; margin-bottom:4px;"
            )
            ui.label("Tus sesiones de descarga registradas en el sistema.").style(
                f"font-size:13px; color:{GRIS_MEDIO}; display:block; margin-bottom:20px;"
            )

            # Aviso bypass
            bypass_activo = cfg.AUTH_BYPASS_MODE
            if bypass_activo:
                with ui.element("div").style(
                    f"background:#FFF3CD; border-left:3px solid #856404; "
                    f"padding:10px 14px; border-radius:3px; margin-bottom:16px;"
                ):
                    ui.label(
                        "BASE DE DATOS NO CONECTADA — AUTH_BYPASS_MODE activo. "
                        "El historial estara disponible cuando se conecte SQL Server."
                    ).style(f"font-size:13px; color:{GRIS_OSCURO};")

            # Filtro por RUC y acciones
            with ui.element("div").style(
                f"display:flex; gap:10px; align-items:flex-end; margin-bottom:16px; flex-wrap:wrap;"
            ):
                ruc_input = ui.input(placeholder="Filtrar por RUC...").style(
                    "width:220px; font-size:13px;"
                )
                btn_primario("Buscar", on_click=lambda: _cargar_tabla(tabla_container, ruc_input.value, codigo_colaborador, bypass_activo))
                btn_secundario("Ver todos", on_click=lambda: _cargar_tabla(tabla_container, "", codigo_colaborador, bypass_activo))

                async def exportar_excel():
                    filas = await asyncio.to_thread(consultar_sesiones, ruc_input.value.strip(), codigo_colaborador)
                    if not filas:
                        ui.notify("Sin datos para exportar.", type="warning")
                        return
                    contenido = await asyncio.to_thread(_generar_excel, filas)
                    ui.download(contenido, "historial_sri.xlsx")

                btn_secundario("Exportar Excel", on_click=exportar_excel)

            divider()

            # Tabla de resultados (debajo de la barra de busqueda/acciones)
            tabla_container = ui.element("div").style("width:100%;")
            await _cargar_tabla(tabla_container, "", codigo_colaborador, bypass_activo)

        with ui.element("div").style("margin-top:24px;"):
            ui.link("Volver al inicio", "/").style(
                f"display:inline-block; font-size:13px; font-weight:600; "
                f"color:{GRIS_OSCURO}; font-family:'Inter',sans-serif; "
                f"text-decoration:none; padding:9px 20px; border-radius:8px; "
                f"border:1.5px solid {BORDER_COLOR}; background:{BLANCO};"
            )


async def _cargar_tabla(contenedor, ruc_filtro: str, codigo_colaborador: str, bypass: bool) -> None:
    from storage.db_logger import consultar_sesiones

    contenedor.clear()
    with contenedor:
        filas = await asyncio.to_thread(consultar_sesiones, ruc_filtro, codigo_colaborador)

        if not filas:
            ui.label(
                "Sin resultados." if not bypass else "Sin datos — base de datos no conectada."
            ).style(f"font-size:13px; color:{GRIS_MEDIO}; padding:20px 0; display:block;")
            return

        # Cabecera tabla
        with ui.element("div").style("overflow-x:auto; width:100%; border-radius:10px; border:1px solid " + BORDER_COLOR + ";"):
            with ui.element("table").style(
                "width:100%; border-collapse:collapse; font-size:14px; font-family:'Inter',sans-serif;"
            ):
                with ui.element("thead"):
                    with ui.element("tr").style(
                        f"background:{GRIS_OSCURO}; border-bottom:2px solid {BORDER_COLOR};"
                    ):
                        for col in ["Fecha", "RUC", "Periodo", "Tipo", "Encontrados", "Descargados", "Errores", "Estado", "Colaborador", "Archivos"]:
                            with ui.element("th").style(
                                f"padding:13px 16px; text-align:left; font-weight:600; "
                                f"color:{BLANCO}; white-space:nowrap; font-family:'Inter',sans-serif; "
                                f"font-size:13px; letter-spacing:0.3px;"
                            ):
                                ui.label(col)

                with ui.element("tbody"):
                    for i, fila in enumerate(filas):
                        fondo = BLANCO if i % 2 == 0 else "#FAFAFE"
                        with ui.element("tr").style(
                            f"background:{fondo}; border-bottom:1px solid {BORDER_COLOR}; "
                            f"transition:background 0.15s;"
                        ):
                            # Fecha
                            fecha = fila.get("FECHA_DESCARGA")
                            fecha_str = fecha.strftime("%d/%m/%Y %H:%M") if hasattr(fecha, "strftime") else str(fecha or "")
                            _td(fecha_str)

                            # RUC
                            _td(fila.get("RUC_CONTRIBUYENTE", ""))

                            # Periodo
                            mes_n = fila.get("PERIODO_MES", 0)
                            _td(f"{MESES_NOMBRE.get(mes_n, mes_n)} {fila.get('PERIODO_ANIO', '')}")

                            # Tipo
                            _td(fila.get("TIPO_COMPROBANTE_DESC", ""))

                            # Conteos
                            _td(str(fila.get("TOTAL_ENCONTRADOS", 0)), align="center")
                            _td(str(fila.get("TOTAL_DESCARGADOS", 0)), align="center", color=SUCCESS_COLOR)
                            errores = fila.get("TOTAL_ERRORES", 0)
                            _td(str(errores), align="center", color=ERROR_COLOR if errores else "")

                            # Estado badge
                            estado = fila.get("ESTADO", "")
                            color_text, color_bg = _ESTADO_BADGE.get(estado, (GRIS_MEDIO, "#E2E3E5"))
                            with ui.element("td").style("padding:12px 16px;"):
                                ui.label(estado).style(
                                    f"background:{color_bg}; color:{color_text}; "
                                    f"padding:3px 10px; border-radius:20px; font-size:12px; "
                                    f"font-weight:700; font-family:'Inter',sans-serif; "
                                    f"letter-spacing:0.3px; display:inline-block;"
                                )

                            # Colaborador
                            codigo = fila.get("CODIGO_COLABORADOR") or ""
                            usuario = fila.get("USUARIO_COLABORADOR") or ""
                            _td(f"{codigo} {usuario}".strip() or "-")

                            # Archivos: vista previa + descarga ZIP
                            with ui.element("td").style("padding:8px 16px;"):
                                carpeta_destino = fila.get("CARPETA_DESTINO") or ""
                                if carpeta_destino and Path(carpeta_destino).is_dir():
                                    btn_secundario(
                                        "Ver archivos",
                                        on_click=lambda c=carpeta_destino: _dialogo_archivos(c),
                                    )
                                else:
                                    ui.label("No disponible").style(
                                        f"font-size:12px; color:{GRIS_MEDIO}; font-style:italic;"
                                    )

        ui.label(f"{len(filas)} registro{'s' if len(filas) != 1 else ''} encontrado{'s' if len(filas) != 1 else ''}.").style(
            f"font-size:12px; color:{GRIS_MEDIO}; margin-top:12px; display:block;"
        )


def _td(texto: str, align: str = "left", color: str = "") -> None:
    estilo = (
        f"padding:12px 16px; text-align:{align}; white-space:nowrap; "
        f"font-family:'Inter',sans-serif; font-size:14px; color:{GRIS_OSCURO};"
    )
    if color:
        estilo += f" color:{color}; font-weight:600;"
    with ui.element("td").style(estilo):
        ui.label(texto)


def _formatear_tamano(bytes_: int) -> str:
    valor = float(bytes_)
    for unidad in ("B", "KB", "MB", "GB"):
        if valor < 1024 or unidad == "GB":
            return f"{valor:.0f} {unidad}" if unidad == "B" else f"{valor:.1f} {unidad}"
        valor /= 1024
    return f"{valor:.1f} GB"


def _generar_zip_carpeta(carpeta: str) -> bytes:
    import io
    import zipfile

    origen = Path(carpeta)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for archivo in origen.rglob("*"):
            if archivo.is_file():
                zf.write(archivo, arcname=str(archivo.relative_to(origen)))
    return buf.getvalue()


def _generar_zip_archivos(carpeta_base: str, archivos: list[str]) -> bytes:
    """
    Igual que _generar_zip_carpeta pero solo con la lista de archivos dada
    (rutas absolutas). Se usa para el ZIP de "solo lo nuevo" de esta corrida,
    en vez de forzar a volver a empaquetar todo el periodo cuando la mayoria
    de los comprobantes ya se habian descargado antes.
    """
    import io
    import zipfile

    origen = Path(carpeta_base)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for ruta_str in archivos:
            archivo = Path(ruta_str)
            if archivo.is_file():
                try:
                    arcname = str(archivo.relative_to(origen))
                except ValueError:
                    arcname = archivo.name
                zf.write(archivo, arcname=arcname)
    return buf.getvalue()


async def _dialogo_archivos(carpeta: str) -> None:
    """Vista previa de los archivos de una sesion del historial, con descarga ZIP."""
    origen = Path(carpeta)
    archivos = sorted(
        (f for f in origen.rglob("*") if f.is_file()),
        key=lambda f: str(f.relative_to(origen)).lower(),
    )

    with ui.dialog() as dialogo, ui.card().style("min-width:420px; max-width:640px; padding:20px 24px;"):
        ui.label(origen.name).style(
            f"font-size:16px; font-weight:700; color:{GRIS_OSCURO}; display:block; margin-bottom:4px; "
            f"font-family:'Inter',sans-serif;"
        )
        ui.label(f"{len(archivos)} archivo{'s' if len(archivos) != 1 else ''}").style(
            f"font-size:12px; color:{GRIS_MEDIO}; display:block; margin-bottom:14px;"
        )

        if not archivos:
            ui.label("Sin archivos en esta carpeta.").style(f"font-size:13px; color:{GRIS_MEDIO};")
        else:
            with ui.element("div").style(
                f"max-height:320px; overflow-y:auto; border:1px solid {BORDER_COLOR}; "
                f"border-radius:8px; padding:4px 0;"
            ):
                for archivo in archivos:
                    relativo = archivo.relative_to(origen)
                    tamano = _formatear_tamano(archivo.stat().st_size)
                    with ui.element("div").style(
                        f"display:flex; justify-content:space-between; gap:12px; "
                        f"padding:7px 14px; border-bottom:1px solid {BORDER_COLOR}; "
                        f"font-family:'Inter',sans-serif;"
                    ):
                        ui.label(str(relativo)).style(f"font-size:12.5px; color:{GRIS_OSCURO};")
                        ui.label(tamano).style(f"font-size:12px; color:{GRIS_MEDIO}; white-space:nowrap;")

        async def descargar_zip():
            if not archivos:
                ui.notify("No hay archivos para descargar.", type="warning")
                return
            contenido = await asyncio.to_thread(_generar_zip_carpeta, carpeta)
            ui.download(contenido, f"{origen.name}.zip")

        with ui.element("div").style("display:flex; gap:10px; margin-top:16px;"):
            btn_secundario("Cerrar", on_click=dialogo.close)
            if archivos:
                btn_primario("Descargar ZIP", on_click=descargar_zip).style("flex:1;")

    dialogo.open()


def _generar_excel(filas: list[dict]) -> bytes:
    import io
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Historial SRI"

    headers = ["Fecha", "RUC", "Anio", "Mes", "Tipo", "Encontrados", "Descargados", "Errores", "Estado", "Colaborador", "Carpeta destino"]
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="160F41")

    for col_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for fila in filas:
        fecha = fila.get("FECHA_DESCARGA")
        fecha_str = fecha.strftime("%d/%m/%Y %H:%M") if hasattr(fecha, "strftime") else str(fecha or "")
        mes_n = fila.get("PERIODO_MES", 0)
        colaborador = f"{fila.get('CODIGO_COLABORADOR') or ''} {fila.get('USUARIO_COLABORADOR') or ''}".strip()
        ws.append([
            fecha_str,
            fila.get("RUC_CONTRIBUYENTE", ""),
            fila.get("PERIODO_ANIO", ""),
            MESES_NOMBRE.get(mes_n, mes_n),
            fila.get("TIPO_COMPROBANTE_DESC", ""),
            fila.get("TOTAL_ENCONTRADOS", 0),
            fila.get("TOTAL_DESCARGADOS", 0),
            fila.get("TOTAL_ERRORES", 0),
            fila.get("ESTADO", ""),
            colaborador or "-",
            fila.get("CARPETA_DESTINO", ""),
        ])

    # Ancho automatico de columnas
    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=0)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 50)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


