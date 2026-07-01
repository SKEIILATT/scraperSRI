"""
Paginas NiceGUI del flujo completo de la aplicacion.

Flujo: Login interno -> Credenciales SRI -> Configuracion consulta -> Progreso -> Resultado
"""

import asyncio
import logging
import re
import shutil
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
    ERROR_COLOR, SUCCESS_COLOR,
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
            "sri_ruc": "",
            "sri_password": "",
            "anio": str(datetime.now().year),
            "mes": str(datetime.now().month),
            "dia": "0",
            "tipo_comprobante": "1",
            "proceso_activo": False,
            "fase_proceso": "",
            "total_comprobantes": 0,
            "completados": 0,
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
                ui.input(placeholder="Ej: 23412")
                .props("outlined dense")
                .style("width:100%;")
            )
            lbl_err_codigo = _lbl_error()

            _input_separador()
            _label_campo("Nombre de usuario")
            usuario_input = (
                ui.input(placeholder="Ej: jgutierrez")
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
                    valido, msg_bd = await asyncio.to_thread(validar_colaborador, codigo, usuario)
                finally:
                    spinner.style("display:none;")
                    btn_ingresar.enable()

                if not valido:
                    lbl_err_general.set_text(msg_bd or "Acceso no autorizado.")
                    return

                s["autenticado"] = True
                s["codigo_colaborador"] = codigo
                s["nombre_usuario_banco"] = usuario
                s["paso"] = 2
                ui.navigate.to("/credenciales-sri")

            codigo_input.on("keydown.enter", hacer_login)
            usuario_input.on("keydown.enter", hacer_login)

            ui.element("div").style("margin-top:24px;")
            btn_ingresar = btn_primario("Ingresar", on_click=hacer_login, ancho_completo=True)


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
                if not re.match(r"^\d{10}$|^\d{13}$", ruc):
                    return "Debe tener 10 digitos (cedula) o 13 digitos (RUC)."

                provincia = int(ruc[:2])
                if not (1 <= provincia <= 24):
                    return "Codigo de provincia invalido (primeros 2 digitos)."

                tercer = int(ruc[2])

                if tercer < 6:
                    # Persona natural: modulo 10 sobre primeros 10 digitos
                    base = ruc[:10]
                    coefs = [2, 1, 2, 1, 2, 1, 2, 1, 2]
                    total = sum(
                        (d - 9 if d >= 10 else d)
                        for d in (c * int(n) for c, n in zip(coefs, base[:9]))
                    )
                    verificador = (10 - (total % 10)) % 10
                    if verificador != int(base[9]):
                        return "El numero de cedula o RUC no es valido (digito verificador incorrecto)."
                    if len(ruc) == 13 and ruc[10:] == "000":
                        return "El codigo de establecimiento del RUC no puede ser 000."

                elif tercer == 6:
                    # Entidad publica: modulo 11 sobre primeros 8 digitos
                    coefs = [3, 2, 7, 6, 5, 4, 3, 2]
                    total = sum(c * int(n) for c, n in zip(coefs, ruc[:8]))
                    verificador = 11 - (total % 11)
                    if verificador in (10, 11):
                        verificador = 0
                    if len(ruc) != 13:
                        return "RUC de entidad publica debe tener 13 digitos."
                    if verificador != int(ruc[8]):
                        return "El RUC de entidad publica no es valido (digito verificador incorrecto)."

                elif tercer == 9:
                    # Sociedad privada / extranjero: modulo 11 sobre primeros 9 digitos
                    coefs = [4, 3, 2, 7, 6, 5, 4, 3, 2]
                    total = sum(c * int(n) for c, n in zip(coefs, ruc[:9]))
                    verificador = 11 - (total % 11)
                    if verificador in (10, 11):
                        verificador = 0
                    if len(ruc) != 13:
                        return "RUC de sociedad debe tener 13 digitos."
                    if verificador != int(ruc[9]):
                        return "El RUC de sociedad no es valido (digito verificador incorrecto)."

                else:
                    return "El tercer digito del RUC no es valido."

                return ""

            def validar_y_continuar():
                lbl_err_ruc.set_text("")
                lbl_err_pass.set_text("")
                lbl_err_confirm.set_text("")
                lbl_err_gen.set_text("")

                ruc = (ruc_input.value or "").strip()
                pwd = pass_input.value or ""
                pwd_confirm = pass_confirm_input.value or ""

                err_ruc = _validar_ruc(ruc)
                if err_ruc:
                    lbl_err_ruc.set_text(err_ruc)
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
                s["sri_password"] = pwd
                s["paso"] = 3
                ui.navigate.to("/configuracion")

            ruc_input.on("keydown.enter", validar_y_continuar)
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
                    _label_campo("Tipo de comprobante")
                    tipo_sel = (
                        ui.select(
                            options={v: l for v, l in TIPOS_COMPROBANTE},
                            value=s.get("tipo_comprobante", "1"),
                        )
                        .props("outlined dense")
                        .style("width:100%;")
                    )

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
                s["anio"] = anio_sel.value
                s["mes"] = mes_sel.value
                s["dia"] = dia_sel.value
                s["tipo_comprobante"] = tipo_sel.value
                s["paso"] = 4
                s["proceso_activo"] = True
                s["proceso_finalizado"] = False
                s["proceso_cancelado"] = False
                s["errores_proceso"] = []
                s["completados"] = 0
                s["total_comprobantes"] = 0
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
            _subtitulo(
                f"RUC: {s.get('sri_ruc', '')}  |  "
                f"Periodo: {_DICT_MESES.get(s.get('mes',''), '')} {s.get('anio', '')}  |  "
                f"Tipo: {_DICT_TIPOS.get(s.get('tipo_comprobante', ''), '')}"
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
                    ui.label(estado.fase).style(
                        f"font-size:14px; font-weight:500; color:{GRIS_OSCURO}; "
                        f"margin-bottom:16px; display:block;"
                    )
                    if estado.total > 0:
                        barra_progreso(estado.completados, estado.total, "Comprobantes descargados")
                    else:
                        with ui.element("div").style(
                            "display:flex; flex-direction:column; align-items:center; padding:8px 0;"
                        ):
                            ui.spinner(size="md", color=MAGENTA)

        ui.update(estado_container)

    async def iniciar_scraping():
        global _orquestador_activo
        params = ParametrosConsulta(
            ruc=s.get("sri_ruc", ""),
            password=s.get("sri_password", ""),
            anio=int(s.get("anio", datetime.now().year)),
            mes=int(s.get("mes", 1)),
            dia=int(s.get("dia", 0)),
            tipo_comprobante=s.get("tipo_comprobante", "1"),
            codigo_colaborador=s.get("codigo_colaborador", ""),
            usuario_colaborador=s.get("nombre_usuario_banco", ""),
        )
        orchestrator = ScrapingOrchestrator(params, on_progress=actualizar_ui)
        _orquestador_activo = orchestrator
        try:
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
    error_critico = any(
        kw in e.lower() for e in errores
        for kw in ["credencial", "incorrecto", "acceso", "login"]
    )

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

            # Opcion de guardar credenciales para sync automatica
            if completados > 0:
                ruc_sync = s.get("sri_ruc", "")
                pwd_sync = s.get("sri_password", "")
                if ruc_sync and pwd_sync:
                    from storage.credentials import guardar as _guardar_cred, tiene_credenciales as _tiene_cred
                    ya_guardadas = _tiene_cred(ruc_sync)
                    lbl_cred = ui.label(
                        "Credenciales ya guardadas para sincronizacion automatica." if ya_guardadas
                        else ""
                    ).style(f"font-size:12px; color:{SUCCESS_COLOR}; display:block; margin-top:6px;")

                    def _toggle_guardar_cred(e):
                        if e.value:
                            try:
                                _guardar_cred(ruc_sync, pwd_sync)
                                lbl_cred.set_text("Credenciales guardadas correctamente.")
                                lbl_cred.style(f"font-size:12px; color:{SUCCESS_COLOR}; display:block; margin-top:6px;")
                            except Exception as exc:
                                lbl_cred.set_text(f"No se pudieron guardar: {exc}")
                                lbl_cred.style(f"font-size:12px; color:{ERROR_COLOR}; display:block; margin-top:6px;")

                    ui.checkbox(
                        "Guardar credenciales para sincronizacion automatica nocturna",
                        value=ya_guardadas,
                        on_change=_toggle_guardar_cred,
                    ).style(
                        f"font-size:13px; color:{GRIS_OSCURO}; margin-top:12px; "
                        f"font-family:'Inter',sans-serif;"
                    )
                    ui.element("div").append(lbl_cred)

            divider()

            # Panel de errores parciales
            if errores:
                panel_errores(errores)
                divider()

            # Guardar archivos en carpeta elegida por el usuario
            carpeta_descarga = s.get("carpeta_descarga", "")
            if completados > 0 and carpeta_descarga:
                with ui.element("div").style(
                    f"background:{FONDO}; border:1px solid {BORDER_COLOR}; border-radius:10px; "
                    f"padding:18px 20px; margin-bottom:20px; width:100%;"
                ):
                    ui.label("Guardar archivos").style(
                        f"font-size:15px; font-weight:700; color:{GRIS_OSCURO}; display:block; margin-bottom:6px;"
                    )
                    ui.label(
                        "Los archivos estan disponibles en la carpeta temporal de descarga. "
                        "Puede copiarlos a una ubicacion de su eleccion."
                    ).style(
                        f"font-size:13px; color:{GRIS_MEDIO}; display:block; margin-bottom:14px; line-height:1.5;"
                    )

                    notif_guardar = ui.label("").style(
                        "font-size:13px; display:block; margin-top:10px;"
                    )

                    async def guardar_en_equipo():
                        notif_guardar.set_text("Abriendo selector de carpeta...")
                        notif_guardar.style(f"color:{GRIS_MEDIO};")

                        def _pedir_carpeta():
                            import tkinter as tk
                            from tkinter import filedialog
                            root = tk.Tk()
                            root.withdraw()
                            root.wm_attributes("-topmost", True)
                            ruta = filedialog.askdirectory(
                                title="Seleccionar carpeta de destino",
                                mustexist=True,
                            )
                            root.destroy()
                            return ruta

                        destino = await asyncio.to_thread(_pedir_carpeta)

                        if not destino:
                            notif_guardar.set_text("Operacion cancelada.")
                            notif_guardar.style(f"color:{GRIS_MEDIO};")
                            return

                        try:
                            origen = Path(carpeta_descarga)
                            destino_path = Path(destino) / origen.name
                            destino_path.mkdir(parents=True, exist_ok=True)
                            copiados = 0
                            for archivo in origen.iterdir():
                                if archivo.is_file():
                                    shutil.copy2(archivo, destino_path / archivo.name)
                                    copiados += 1
                            notif_guardar.set_text(
                                f"{copiados} archivo{'s' if copiados != 1 else ''} copiado{'s' if copiados != 1 else ''} a: {destino_path}"
                            )
                            notif_guardar.style(f"color:{SUCCESS_COLOR};")
                        except Exception as exc:
                            notif_guardar.set_text(f"Error al copiar archivos: {exc}")
                            notif_guardar.style(f"color:{ERROR_COLOR};")

                    btn_primario("Guardar en mi equipo", on_click=guardar_en_equipo)

            # Acciones
            def nueva_consulta():
                s["paso"] = 3
                s["proceso_finalizado"] = False
                s["errores_proceso"] = []
                ui.navigate.to("/configuracion")

            with ui.element("div").style("display:flex; gap:10px; margin-top:8px;"):
                btn_primario("Nueva consulta", on_click=nueva_consulta)


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
            ui.label("Sesiones de descarga registradas en el sistema.").style(
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

            # Filtro por RUC
            tabla_container = ui.element("div").style("width:100%;")

            with ui.element("div").style(
                f"display:flex; gap:10px; align-items:flex-end; margin-bottom:16px;"
            ):
                ruc_input = ui.input(placeholder="Filtrar por RUC...").style(
                    "width:220px; font-size:13px;"
                )
                btn_primario("Buscar", on_click=lambda: _cargar_tabla(tabla_container, ruc_input.value, bypass_activo))
                btn_secundario("Ver todos", on_click=lambda: _cargar_tabla(tabla_container, "", bypass_activo))

            divider()

            # Carga inicial
            await _cargar_tabla(tabla_container, "", bypass_activo)

        with ui.element("div").style("margin-top:24px;"):
            ui.link("Volver al inicio", "/").style(
                f"display:inline-block; font-size:13px; font-weight:600; "
                f"color:{GRIS_OSCURO}; font-family:'Inter',sans-serif; "
                f"text-decoration:none; padding:9px 20px; border-radius:8px; "
                f"border:1.5px solid {BORDER_COLOR}; background:{BLANCO};"
            )


async def _cargar_tabla(contenedor, ruc_filtro: str, bypass: bool) -> None:
    from storage.db_logger import consultar_sesiones

    contenedor.clear()
    with contenedor:
        filas = await asyncio.to_thread(consultar_sesiones, ruc_filtro)

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
                        for col in ["Fecha", "RUC", "Periodo", "Tipo", "Encontrados", "Descargados", "Errores", "Estado", "Colaborador"]:
                            ui.element("th").style(
                                f"padding:13px 16px; text-align:left; font-weight:600; "
                                f"color:{BLANCO}; white-space:nowrap; font-family:'Inter',sans-serif; "
                                f"font-size:13px; letter-spacing:0.3px;"
                            ).text = col

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
                                ui.element("span").style(
                                    f"background:{color_bg}; color:{color_text}; "
                                    f"padding:3px 10px; border-radius:20px; font-size:12px; "
                                    f"font-weight:700; font-family:'Inter',sans-serif; "
                                    f"letter-spacing:0.3px;"
                                ).text = estado

                            # Colaborador
                            codigo = fila.get("CODIGO_COLABORADOR") or ""
                            usuario = fila.get("USUARIO_COLABORADOR") or ""
                            _td(f"{codigo} {usuario}".strip() or "-")

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
    ui.element("td").style(estilo).text = texto


