"""
Componentes reutilizables de UI para Banco Guayaquil.
"""

from nicegui import app as _nicegui_app
from nicegui import ui
from ui.styles import (
    MAGENTA, MAGENTA_HOVER, MAGENTA_LIGHTEST,
    NAVY, NAVY_MED, NAVY_LIGHT,
    BLANCO, FONDO,
    ERROR_COLOR, SUCCESS_COLOR, WARNING_COLOR, BORDER_COLOR,
)

# ── Botones ──────────────────────────────────────────────────────────────────

_BTN_BASE = (
    "font-family:'Inter',sans-serif !important; font-weight:600 !important; "
    "text-transform:none !important; letter-spacing:0.2px !important; "
    "box-shadow:none !important; border-radius:8px !important; "
    "font-size:14px !important; min-height:42px !important; "
    "padding:0 24px !important; transition:all 0.2s !important;"
)

_S_BTN_PRI = (
    f"{_BTN_BASE} "
    f"background:{MAGENTA} !important; background-color:{MAGENTA} !important; "
    f"color:{BLANCO} !important; border:none !important;"
)

_S_BTN_SEC = (
    f"{_BTN_BASE} "
    f"background:transparent !important; background-color:transparent !important; "
    f"color:{MAGENTA} !important; border:2px solid {MAGENTA} !important;"
)


def btn_primario(texto: str, on_click=None, ancho_completo: bool = False):
    s = _S_BTN_PRI + (" width:100% !important;" if ancho_completo else "")
    return ui.button(texto, on_click=on_click).style(s)


def btn_secundario(texto: str, on_click=None):
    return ui.button(texto, on_click=on_click).style(_S_BTN_SEC)


# ── Header ───────────────────────────────────────────────────────────────────

def header():
    with ui.element("header").style(
        f"background:{BLANCO}; width:100%; "
        f"padding:0 40px; height:68px; display:flex; align-items:center; "
        f"justify-content:space-between; position:sticky; top:0; z-index:100; "
        f"border-bottom:3px solid {MAGENTA}; "
        f"box-shadow:0 2px 10px rgba(22,15,65,0.06);"
    ):
        # Logo + separador + nombre app
        with ui.element("div").style("display:flex; align-items:center; gap:20px;"):
            ui.html(
                '<img src="/_assets/logo_bg_magenta.png" '
                'style="height:40px; width:auto; object-fit:contain; display:block;" '
                'alt="Banco Guayaquil">'
            )
            ui.element("div").style(
                f"width:1px; height:26px; background:{BORDER_COLOR};"
            )
            with ui.element("div"):
                ui.label("Descarga de Comprobantes SRI").style(
                    f"font-size:14px; font-weight:600; color:{NAVY}; "
                    f"font-family:'Inter',sans-serif; display:block; line-height:1.2;"
                )
                ui.label("Uso interno Banco Guayaquil").style(
                    f"font-size:11px; color:{NAVY_MED}; "
                    f"font-family:'Inter',sans-serif; display:block;"
                )

        # Controles derecha del header
        with ui.element("div").style("display:flex; align-items:center; gap:10px;"):
            ui.link("Ver historial", "/historial").style(
                f"font-size:13px; font-weight:600; color:{MAGENTA}; "
                f"font-family:'Inter',sans-serif; text-decoration:none; "
                f"padding:9px 20px; border-radius:8px; "
                f"border:2px solid {MAGENTA}; "
                f"white-space:nowrap;"
            )
            if _nicegui_app.storage.user.get("autenticado"):
                def _cerrar_sesion():
                    for k in list(_nicegui_app.storage.user.keys()):
                        del _nicegui_app.storage.user[k]
                    ui.navigate.to("/")
                ui.button("Cerrar sesion", on_click=_cerrar_sesion).style(
                    f"font-family:'Inter',sans-serif !important; font-weight:600 !important; "
                    f"text-transform:none !important; font-size:13px !important; "
                    f"background:{MAGENTA} !important; color:{BLANCO} !important; "
                    f"border:none !important; border-radius:8px !important; "
                    f"padding:0 20px !important; min-height:38px !important; "
                    f"box-shadow:none !important;"
                )


# ── Contenedor principal centrado ─────────────────────────────────────────────

def centrar_contenido():
    """Wrapper de página que centra el contenido horizontalmente y lo hace responsivo."""
    return ui.element("div").style(
        "width:100%; min-height:calc(100vh - 60px); "
        "display:flex; flex-direction:column; align-items:center; "
        "padding:48px 24px 64px; background:transparent;"
    )


# ── Stepper ───────────────────────────────────────────────────────────────────

def stepper(paso_actual: int):
    pasos = ["Acceso", "Credenciales", "Configuracion", "Descarga", "Resultado"]

    with ui.element("div").style("width:100%; margin-bottom:36px;"):
        with ui.element("div").style("display:flex; align-items:flex-start; width:100%;"):
            for i, nombre in enumerate(pasos, start=1):
                if i < paso_actual:
                    bg, text_color, icono = SUCCESS_COLOR, BLANCO, "✓"
                    lbl_color, lbl_weight = SUCCESS_COLOR, "600"
                elif i == paso_actual:
                    bg, text_color, icono = MAGENTA, BLANCO, str(i)
                    lbl_color, lbl_weight = MAGENTA, "700"
                else:
                    bg, text_color, icono = NAVY_LIGHT, NAVY_MED, str(i)
                    lbl_color, lbl_weight = NAVY_MED, "400"

                with ui.element("div").style(
                    "display:flex; flex-direction:column; align-items:center; flex-shrink:0; gap:6px;"
                ):
                    with ui.element("div").style(
                        f"width:32px; height:32px; border-radius:50%; background:{bg}; "
                        f"display:flex; align-items:center; justify-content:center; "
                        f"box-shadow:0 2px 8px rgba(0,0,0,0.12);"
                    ):
                        ui.label(icono).style(
                            f"color:{text_color}; font-size:12px; font-weight:700; "
                            f"font-family:'Inter',sans-serif; line-height:1;"
                        )
                    ui.label(nombre).style(
                        f"font-size:11px; color:{lbl_color}; font-weight:{lbl_weight}; "
                        f"font-family:'Inter',sans-serif; text-align:center; white-space:nowrap;"
                    )

                if i < len(pasos):
                    done = i < paso_actual
                    ui.element("div").style(
                        f"flex:1; height:2px; margin:0 6px; margin-bottom:26px; "
                        f"background:{'linear-gradient(to right,'+SUCCESS_COLOR+','+SUCCESS_COLOR+')' if done else NAVY_LIGHT}; "
                        f"border-radius:1px; min-width:8px;"
                    )


# ── Barra de progreso ─────────────────────────────────────────────────────────

def barra_progreso(completados: int, total: int, fase: str):
    pct = int(completados / total * 100) if total > 0 else 0
    with ui.element("div").style("width:100%;"):
        with ui.element("div").style(
            "display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;"
        ):
            ui.label(fase).style(
                f"font-size:14px; color:{NAVY}; font-weight:500; "
                f"font-family:'Inter',sans-serif;"
            )
            ui.label(f"{completados} / {total}").style(
                f"font-size:13px; color:{NAVY_MED}; font-family:'Inter',sans-serif;"
            )
        with ui.element("div").style(
            f"background:{NAVY_LIGHT}; border-radius:6px; height:10px; "
            f"width:100%; overflow:hidden;"
        ):
            ui.element("div").style(
                f"height:100%; border-radius:6px; transition:width 0.4s ease; "
                f"width:{pct}%; "
                f"background:linear-gradient(to right, {MAGENTA}, #E04D9A);"
            )
        ui.label(f"{pct}% completado").style(
            f"font-size:12px; color:{NAVY_MED}; margin-top:5px; "
            f"font-family:'Inter',sans-serif;"
        )


# ── Panel de intervención ─────────────────────────────────────────────────────

def panel_intervencion(mensaje: str, on_resolver, on_cancelar):
    with ui.element("div").style(
        f"background:#FFFBEB; border:1.5px solid #F59E0B; border-radius:10px; "
        f"padding:20px 22px; width:100%;"
    ):
        with ui.element("div").style("display:flex; align-items:center; gap:10px; margin-bottom:10px;"):
            ui.element("div").style(
                f"width:8px; height:8px; border-radius:50%; background:#F59E0B; flex-shrink:0;"
            )
            ui.label("Verificacion requerida").style(
                f"font-size:15px; font-weight:700; color:{NAVY}; "
                f"font-family:'Inter',sans-serif;"
            )
        ui.label(mensaje).style(
            f"font-size:14px; color:{NAVY}; margin-bottom:6px; "
            f"font-family:'Inter',sans-serif; line-height:1.5;"
        )
        ui.label("Resuelva la verificacion en la ventana del navegador y presione Continuar.").style(
            f"font-size:13px; color:{NAVY_MED}; margin-bottom:18px; "
            f"font-family:'Inter',sans-serif;"
        )
        with ui.element("div").style("display:flex; gap:10px;"):
            btn_primario("Continuar", on_click=on_resolver)
            btn_secundario("Cancelar", on_click=on_cancelar)


# ── Panel de errores ──────────────────────────────────────────────────────────

def panel_errores(errores: list[str]):
    if not errores:
        return
    with ui.element("div").style(
        f"background:#FEF2F2; border:1.5px solid #FECACA; border-radius:10px; "
        f"padding:16px 20px; width:100%; margin-top:16px;"
    ):
        ui.label(f"Advertencias ({len(errores)})").style(
            f"font-size:13px; font-weight:700; color:{ERROR_COLOR}; "
            f"margin-bottom:10px; display:block; font-family:'Inter',sans-serif;"
        )
        for e in errores[:20]:
            with ui.element("div").style(
                f"font-size:13px; color:{NAVY}; padding:5px 0; "
                f"border-bottom:1px solid #FECACA; font-family:'Inter',sans-serif;"
            ):
                ui.label(e)
        if len(errores) > 20:
            ui.label(f"... y {len(errores) - 20} mas.").style(
                f"font-size:12px; color:{NAVY_MED}; margin-top:6px; "
                f"font-family:'Inter',sans-serif;"
            )


# ── Separador ─────────────────────────────────────────────────────────────────

def divider():
    ui.element("hr").style(
        f"border:none; border-top:1px solid {BORDER_COLOR}; margin:22px 0; width:100%;"
    )
