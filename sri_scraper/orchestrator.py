"""
Orquestador principal del proceso de scraping.

Coordina: login SRI -> navegacion -> (filtros -> descarga reporte -> descarga
por fila) por cada tipo de comprobante solicitado. El login y la navegacion al
formulario de comprobantes se hacen UNA SOLA VEZ por corrida, sin importar
cuantos tipos de comprobante se pidan: reabrir el navegador y volver a loguear
por cada tipo desperdicia tiempo y le agrega reintentos de login innecesarios
que solo bajan el score de reCAPTCHA. Dentro de la misma sesion ya logueada,
solo se repite "aplicar filtros -> descargar" para cada tipo adicional.

Actualiza el estado en tiempo real via callbacks para que la UI pueda mostrar progreso.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Awaitable

from .browser import BrowserManager
from .humanize import pausa, simular_lectura
from .login import login_sri, ErrorCredenciales, ErrorRecaptcha, ErrorLoginTimeout
from .consulta import (
    navegar_a_comprobantes,
    aplicar_filtros,
    obtener_filas_pagina,
    total_paginas as obtener_total_paginas,
    ir_a_pagina,
    descargar_reporte,
    descargar_xml,
    descargar_pdf,
    TIPOS_COMPROBANTE,
    ErrorCaptchaConsulta,
)
from storage.file_manager import (
    carpeta_descarga,
    ruta_descarga,
    ruta_reporte,
    MESES,
)

logger = logging.getLogger(__name__)


@dataclass
class ParametrosConsulta:
    ruc: str
    password: str
    anio: int
    mes: int
    dia: int                    # 0 = Todos
    tipos_comprobante: list[str]  # uno o mas; mismo periodo/dia para todos
    codigo_colaborador: str = ""
    usuario_colaborador: str = ""


@dataclass
class EstadoProceso:
    fase: str = "Iniciando"
    total: int = 0                    # acumulado de TODOS los tipos pedidos
    completados: int = 0              # acumulado de TODOS los tipos pedidos
    errores: list[str] = field(default_factory=list)
    carpeta_descarga: str = ""
    cancelado: bool = False
    requiere_intervencion: bool = False
    mensaje_intervencion: str = ""
    finalizado: bool = False

    # --- Info del tipo de comprobante que se esta procesando en este momento ---
    # Cuando se piden varios tipos (ej. Factura + Retencion), la UI usa estos
    # campos para mostrar claramente "en que tipo va" y una barra de progreso
    # que arranca en 0 para cada tipo, en vez de seguir acumulando la del anterior.
    tipos_nombres: list[str] = field(default_factory=list)   # todos los tipos pedidos, en orden
    tipo_actual_indice: int = 1                              # 1-based
    tipo_actual_nombre: str = ""
    total_tipo_actual: int = 0
    completados_tipo_actual: int = 0
    tipos_finalizados: list[str] = field(default_factory=list)  # nombres ya completados


# Tipo del callback de progreso: recibe el EstadoProceso actualizado
ProgressCallback = Callable[[EstadoProceso], Awaitable[None]]


class ScrapingOrchestrator:
    def __init__(self, params: ParametrosConsulta, on_progress: ProgressCallback):
        self._params = params
        self._on_progress = on_progress
        self._estado = EstadoProceso()
        self._browser = BrowserManager()
        self._cancelar = False

    async def _notificar(self, fase: str) -> None:
        self._estado.fase = fase
        try:
            await self._on_progress(self._estado)
        except Exception as e:
            logger.warning("Error en callback de progreso: %s", e)

    def solicitar_cancelacion(self) -> None:
        self._cancelar = True
        self._estado.cancelado = True

    async def ejecutar(self) -> EstadoProceso:
        p = self._params
        tipos = p.tipos_comprobante
        multi_tipo = len(tipos) > 1
        mes_nombre = MESES[p.mes]

        try:
            # --- Fase 1: Login (una sola vez para toda la corrida) ---
            await self._notificar("Iniciando navegador")
            page = await self._browser.iniciar()
            # No se minimiza la ventana: mantenerla visible evita que el navegador
            # entre en estado "hidden" (document.visibilityState/rAF pausado), que
            # reCAPTCHA Enterprise usa como senal de comportamiento no humano.

            await self._notificar("Autenticando en SRI en linea")

            async def _progreso_login(intento: int, total: int) -> None:
                if intento == 1:
                    await self._notificar("Autenticando en SRI en linea")
                else:
                    await self._notificar(
                        f"reCAPTCHA no resuelto - reintentando ({intento}/{total})"
                    )

            try:
                # login_sri ya reintenta internamente varias veces el flujo
                # humanizado completo. El reCAPTCHA del SRI es invisible/score-based:
                # nunca presenta un desafio que un humano pueda resolver a mano, asi
                # que si ErrorRecaptcha llega aqui es porque ya se agotaron los
                # reintentos automaticos, no porque falte que alguien haga clic en algo.
                await login_sri(page, p.ruc, p.password, on_reintento=_progreso_login)
            except ErrorRecaptcha as e:
                self._estado.errores.append(str(e))
                self._estado.finalizado = True
                await self._notificar("No se pudo verificar el acceso automaticamente")
                return self._estado

            except ErrorCredenciales as e:
                self._estado.errores.append(str(e))
                self._estado.finalizado = True
                await self._notificar("Error de credenciales")
                return self._estado

            except ErrorLoginTimeout as e:
                self._estado.errores.append(str(e))
                self._estado.finalizado = True
                await self._notificar("Error de conexion")
                return self._estado

            if self._cancelar:
                return self._estado

            # --- Fase 1.5: Warm-up de sesion (una sola vez) ---
            # Visitar el perfil del SRI antes de ir a comprobantes.
            # Google ve actividad humana real en la sesion y sube el score reCAPTCHA.
            await self._notificar("Preparando sesion")
            await self._calentar_sesion(page)

            if self._cancelar:
                return self._estado

            # --- Fase 2: Navegacion al formulario de comprobantes (una sola vez) ---
            # navegar_a_comprobantes ya reintenta internamente el flujo de
            # redireccion/login de tuportal-internet varias veces (ver consulta.py).
            await self._notificar("Navegando a comprobantes electronicos")
            await navegar_a_comprobantes(page, ruc=p.ruc, password=p.password)

            if self._cancelar:
                return self._estado

            # La carpeta general del periodo es la misma para todos los tipos;
            # se muestra siempre esta (no una subcarpeta especifica) para que
            # "Guardar en mi equipo" en el resultado tome todo junto.
            carpeta_general = carpeta_descarga(p.ruc, p.anio, p.mes)
            self._estado.carpeta_descarga = str(carpeta_general)

            total_previos = 0
            completados_previos = 0
            self._estado.tipos_nombres = [TIPOS_COMPROBANTE.get(t, t) for t in tipos]

            # --- Fase 3 en adelante: repetir consulta+descarga por cada tipo,
            #     sin volver a loguear ni renavegar (misma sesion ya autenticada) ---
            for idx, tipo in enumerate(tipos, start=1):
                if self._cancelar:
                    break

                es_retencion = tipo == "6"
                nombre_tipo = TIPOS_COMPROBANTE.get(tipo, tipo)
                # Con un solo tipo, todo queda plano en la carpeta general
                # (comportamiento original). Con varios tipos, cada uno en su
                # propia subcarpeta para no mezclarse.
                subcarpeta = nombre_tipo if multi_tipo else ""
                errores_antes = len(self._estado.errores)

                # Info del tipo actual para que la UI muestre una barra de
                # progreso que arranca en 0 (no sigue acumulando la del tipo
                # anterior) y deje claro en cual tipo esta ahora mismo.
                self._estado.tipo_actual_indice = idx
                self._estado.tipo_actual_nombre = nombre_tipo
                self._estado.total_tipo_actual = 0
                self._estado.completados_tipo_actual = 0

                async def _notificar_tipo(mensaje: str) -> None:
                    # Ya no se antepone "[i/n] Tipo —": la UI muestra el tipo
                    # actual con chips + encabezado propio (ver pagina_descarga).
                    await self._notificar(mensaje)

                await _notificar_tipo(f"Consultando {nombre_tipo} de {mes_nombre} {p.anio}")

                # El reCAPTCHA de este formulario tambien es invisible/score-based: un
                # rechazo no deja nada visible que un humano pueda resolver a mano, asi
                # que la unica opcion real es reintentar el envio unas pocas veces con
                # pausas crecientes (recargando el formulario para un token fresco).
                MAX_INTENTOS_FILTROS = 3
                filtro_ok = False
                for intento_filtros in range(1, MAX_INTENTOS_FILTROS + 1):
                    try:
                        await aplicar_filtros(
                            page, str(p.anio), str(p.mes), str(p.dia), tipo,
                        )
                        filtro_ok = True
                        break
                    except ErrorCaptchaConsulta as e:
                        if intento_filtros < MAX_INTENTOS_FILTROS:
                            logger.info(
                                "Consulta rechazada por verificacion (tipo %s, intento %d/%d). Reintentando...",
                                tipo, intento_filtros, MAX_INTENTOS_FILTROS,
                            )
                            await _notificar_tipo(
                                f"Verificacion no superada - reintentando ({intento_filtros}/{MAX_INTENTOS_FILTROS})"
                            )
                            await pausa(5000, 9000)
                            await navegar_a_comprobantes(page, p.ruc, p.password)
                            continue
                        # Se agotaron los reintentos automaticos: no hay nada visible
                        # que un humano pueda resolver. Se registra el error de este
                        # tipo puntual y se sigue con el resto de tipos pedidos.
                        self._estado.errores.append(f"{nombre_tipo}: {e}")

                if not filtro_ok:
                    self._estado.tipos_finalizados = self._estado.tipos_finalizados + [nombre_tipo]
                    await self._registrar_en_bd(
                        tipo=tipo, nombre_tipo=nombre_tipo,
                        total_encontrados=0, total_descargados=0,
                        total_errores=len(self._estado.errores) - errores_antes,
                        carpeta=str(carpeta_descarga(p.ruc, p.anio, p.mes, subcarpeta)),
                    )
                    continue

                if self._cancelar:
                    break

                # --- Descargar reporte del periodo (no aplica a retenciones) ---
                if not es_retencion:
                    await _notificar_tipo("Descargando reporte de periodo")
                    ruta = str(ruta_reporte(p.ruc, p.anio, p.mes, subcarpeta))
                    ok = await descargar_reporte(page, ruta)
                    await _notificar_tipo("Reporte descargado" if ok else "Reporte no disponible")

                # --- Conteo previo + descarga pagina a pagina ---
                n_paginas = await obtener_total_paginas(page)
                logger.info("Tipo %s: total de paginas en tabla: %d", tipo, n_paginas)

                conteo_por_pagina: list[int] = []
                for pag in range(1, n_paginas + 1):
                    if pag > 1:
                        await ir_a_pagina(page, pag)
                    filas_pag = await obtener_filas_pagina(page, tipo)
                    conteo_por_pagina.append(len(filas_pag))

                total_tipo = sum(conteo_por_pagina)
                self._estado.total = total_previos + total_tipo
                self._estado.total_tipo_actual = total_tipo
                await _notificar_tipo(f"Encontrados {total_tipo} comprobantes")

                completados_tipo = 0
                for pag in range(1, n_paginas + 1):
                    if self._cancelar:
                        break

                    if n_paginas > 1:
                        await ir_a_pagina(page, pag)

                    filas = await obtener_filas_pagina(page, tipo)

                    for fila in filas:
                        if self._cancelar:
                            break

                        await _notificar_tipo(
                            f"Pagina {pag}/{n_paginas} - "
                            f"Descargando {completados_tipo + 1}/{total_tipo}: "
                            f"{fila.tipo_y_serie}"
                        )

                        # Descargar XML (omitir si ya existe en disco)
                        ruta_xml = str(ruta_descarga(
                            p.ruc, p.anio, p.mes, fila.tipo_y_serie, "xml", fila.es_retencion, subcarpeta,
                        ))
                        if Path(ruta_xml).exists():
                            ok_xml = True
                        else:
                            ok_xml = await descargar_xml(page, fila.indice, ruta_xml)
                        fila.xml_descargado = ok_xml
                        if not ok_xml:
                            msg = f"{nombre_tipo}: XML fallido: {fila.tipo_y_serie}"
                            fila.error = msg
                            self._estado.errores.append(msg)

                        # Descargar PDF/RIDE (omitir si ya existe en disco)
                        ruta_pdf = str(ruta_descarga(
                            p.ruc, p.anio, p.mes, fila.tipo_y_serie, "pdf", fila.es_retencion, subcarpeta,
                        ))
                        if Path(ruta_pdf).exists():
                            ok_pdf = True
                        else:
                            ok_pdf = await descargar_pdf(page, fila.indice, ruta_pdf)
                        fila.pdf_descargado = ok_pdf
                        if not ok_pdf:
                            msg = f"{nombre_tipo}: PDF fallido: {fila.tipo_y_serie}"
                            self._estado.errores.append(msg)

                        completados_tipo += 1
                        self._estado.completados = completados_previos + completados_tipo
                        self._estado.completados_tipo_actual = completados_tipo
                        await self._on_progress(self._estado)

                total_previos += total_tipo
                completados_previos += completados_tipo
                self._estado.tipos_finalizados = self._estado.tipos_finalizados + [nombre_tipo]

                await self._registrar_en_bd(
                    tipo=tipo, nombre_tipo=nombre_tipo,
                    total_encontrados=total_tipo, total_descargados=completados_tipo,
                    total_errores=len(self._estado.errores) - errores_antes,
                    carpeta=str(carpeta_descarga(p.ruc, p.anio, p.mes, subcarpeta)),
                )

            self._estado.finalizado = True

            resumen = (
                f"Proceso completado: {self._estado.completados} comprobantes procesados"
                + (f", {len(self._estado.errores)} con errores" if self._estado.errores else "")
            )
            await self._notificar(resumen)

            from storage.notificaciones import notificar
            _msg = (
                f"{self._estado.completados} comprobantes descargados para RUC {p.ruc}"
                + (f"\n{len(self._estado.errores)} con errores." if self._estado.errores else "")
            )
            await asyncio.to_thread(notificar, "Descarga completada", _msg)

        except Exception as exc:
            logger.exception("Error no controlado en orquestador: %s", exc)
            self._estado.errores.append(f"Error inesperado: {exc}")
            self._estado.finalizado = True
            await self._notificar("Error inesperado")

            # Evidencia automatica del estado del navegador al momento del error,
            # para diagnosticar sin tener que reproducir el caso a mano.
            pagina_error = locals().get("page")
            if pagina_error is not None:
                try:
                    await pagina_error.screenshot(path="debug_error_orquestador.png")
                    html = await pagina_error.content()
                    with open("debug_error_orquestador.html", "w", encoding="utf-8") as f:
                        f.write(html)
                except Exception:
                    pass

        finally:
            # El navegador se cierra una sola vez, al terminar TODOS los tipos.
            await self._browser.cerrar()

        return self._estado

    async def _registrar_en_bd(
        self, tipo: str, nombre_tipo: str,
        total_encontrados: int, total_descargados: int, total_errores: int,
        carpeta: str,
    ) -> None:
        from storage.db_logger import registrar_sesion, RegistroSesion
        p = self._params
        if self._estado.cancelado:
            estado = "CANCELADO"
        elif total_errores > 0 and total_descargados > 0:
            estado = "PARCIAL"
        elif total_errores > 0 and total_descargados == 0:
            estado = "ERROR"
        else:
            estado = "COMPLETADO"

        try:
            await asyncio.to_thread(
                registrar_sesion,
                RegistroSesion(
                    ruc_contribuyente=p.ruc,
                    periodo_anio=p.anio,
                    periodo_mes=p.mes,
                    tipo_comprobante_cod=tipo,
                    tipo_comprobante_desc=nombre_tipo,
                    total_encontrados=total_encontrados,
                    total_descargados=total_descargados,
                    total_errores=total_errores,
                    carpeta_destino=carpeta,
                    estado=estado,
                    codigo_colaborador=p.codigo_colaborador,
                    usuario_colaborador=p.usuario_colaborador,
                ),
            )
        except Exception as exc:
            logger.warning("Error al registrar sesion en BD (no critico): %s", exc)

    async def _esperar_intervencion(self, page) -> None:
        """
        Restaura la ventana del navegador y espera a que el usuario resuelva
        la intervencion. La ventana se mantiene visible (no se vuelve a minimizar).
        """
        logger.info("Esperando intervencion del usuario...")
        await self._browser.mostrar()
        while self._estado.requiere_intervencion and not self._cancelar:
            await asyncio.sleep(1)

    def resolver_intervencion(self) -> None:
        """Llamado desde la UI cuando el usuario confirma que resolvio el captcha."""
        self._estado.requiere_intervencion = False
        self._estado.mensaje_intervencion = ""

    async def _calentar_sesion(self, page) -> None:
        """
        Visita brevemente el perfil del SRI antes de ir a comprobantes.
        Google ve actividad humana real en la sesion y sube el score reCAPTCHA
        del formulario de comprobantes que viene despues.
        """
        urls_warmup = [
            "https://srienlinea.sri.gob.ec/sri-en-linea/contribuyente/perfil",
        ]
        for url in urls_warmup:
            try:
                logger.info("Warm-up: visitando %s", url)
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                await simular_lectura(page, segundos_min=6.0, segundos_max=11.0)
            except Exception as e:
                logger.debug("Warm-up omitido para %s: %s", url, e)
