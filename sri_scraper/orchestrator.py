"""
Orquestador principal del proceso de scraping.

Coordina: login SRI -> navegacion -> filtros -> descarga reporte -> descarga por fila.
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
    esperar_tabla,
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
    dia: int          # 0 = Todos
    tipo_comprobante: str
    codigo_colaborador: str = ""
    usuario_colaborador: str = ""


@dataclass
class EstadoProceso:
    fase: str = "Iniciando"
    total: int = 0
    completados: int = 0
    errores: list[str] = field(default_factory=list)
    carpeta_descarga: str = ""
    cancelado: bool = False
    requiere_intervencion: bool = False
    mensaje_intervencion: str = ""
    finalizado: bool = False


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
        es_retencion = p.tipo_comprobante == "6"
        mes_nombre = MESES[p.mes]

        try:
            # --- Fase 1: Login ---
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
                await login_sri(page, p.ruc, p.password, on_reintento=_progreso_login)
            except ErrorRecaptcha as e:
                self._estado.requiere_intervencion = True
                self._estado.mensaje_intervencion = str(e)
                await self._notificar("Verificacion requerida")
                # Esperar hasta que el usuario resuelva el captcha o cancele
                await self._esperar_intervencion(page)
                if self._cancelar:
                    return self._estado
                # Reintentar login despues de la intervencion
                await login_sri(page, p.ruc, p.password)
                self._estado.requiere_intervencion = False

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

            # --- Fase 1.5: Warm-up de sesion ---
            # Visitar el perfil del SRI antes de ir a comprobantes.
            # Google ve actividad humana real en la sesion y sube el score reCAPTCHA.
            await self._notificar("Preparando sesion")
            await self._calentar_sesion(page)

            if self._cancelar:
                return self._estado

            # --- Fase 2: Navegacion ---
            await self._notificar("Navegando a comprobantes electronicos")

            async def _progreso_comprobantes(intento: int, total: int) -> None:
                await self._notificar(
                    f"Accediendo a comprobantes - reCAPTCHA tuportal, reintentando ({intento}/{total})"
                )

            await navegar_a_comprobantes(
                page,
                ruc=p.ruc,
                password=p.password,
                on_reintento=_progreso_comprobantes,
            )

            if self._cancelar:
                return self._estado

            # --- Fase 3: Filtros y consulta ---
            await self._notificar(
                f"Consultando {TIPOS_COMPROBANTE.get(p.tipo_comprobante, 'comprobantes')} "
                f"de {mes_nombre} {p.anio}"
            )

            _captcha_ok = False
            for _intento_captcha in range(1, 4):
                try:
                    await aplicar_filtros(
                        page,
                        str(p.anio),
                        str(p.mes),
                        str(p.dia),
                        p.tipo_comprobante,
                    )
                    _captcha_ok = True
                    break
                except ErrorCaptchaConsulta:
                    if _intento_captcha < 3:
                        logger.info(
                            "CAPTCHA bloqueo el formulario (intento %d/3). Recargando formulario y reintentando...",
                            _intento_captcha,
                        )
                        await self._notificar(
                            f"CAPTCHA en formulario - reintentando ({_intento_captcha}/3)"
                        )
                        # Espera larga y recarga el formulario para obtener token fresco
                        await pausa(5000, 9000)
                        await navegar_a_comprobantes(page, p.ruc, p.password)
                        continue
                    # 3 intentos automaticos fallaron — pedir intervencion manual
                    self._estado.requiere_intervencion = True
                    self._estado.mensaje_intervencion = (
                        "El CAPTCHA del formulario no pudo resolverse automaticamente. "
                        "En la ventana del navegador: resuelva el CAPTCHA y haga clic en 'Consultar'. "
                        "Cuando aparezca la tabla de resultados, presione 'Continuar'."
                    )
                    await self._notificar("CAPTCHA requerido - accion manual necesaria")
                    await self._esperar_intervencion(page)
                    if self._cancelar:
                        return self._estado
                    self._estado.requiere_intervencion = False
                    await esperar_tabla(page)
                    _captcha_ok = True
                    break

            # --- Fase 4: Descargar reporte ---
            if not es_retencion:
                await self._notificar("Descargando reporte de periodo")
                ruta = str(ruta_reporte(p.ruc, p.anio, p.mes))
                ok = await descargar_reporte(page, ruta)
                self._estado.fase = "Reporte descargado" if ok else "Reporte no disponible"
                await self._on_progress(self._estado)

            # --- Fase 5: Conteo previo + descarga pagina a pagina ---
            n_paginas = await obtener_total_paginas(page)
            logger.info("Total de paginas en tabla: %d", n_paginas)

            # Primer recorrido: contar total de comprobantes
            conteo_por_pagina: list[int] = []
            for pag in range(1, n_paginas + 1):
                if pag > 1:
                    await ir_a_pagina(page, pag)
                filas_pag = await obtener_filas_pagina(page, p.tipo_comprobante)
                conteo_por_pagina.append(len(filas_pag))

            self._estado.total = sum(conteo_por_pagina)
            await self._notificar(f"Encontrados {self._estado.total} comprobantes")

            # Segundo recorrido: descargar pagina a pagina usando el indice dentro de cada pagina
            for pag in range(1, n_paginas + 1):
                if self._cancelar:
                    break

                if n_paginas > 1:
                    await ir_a_pagina(page, pag)

                filas = await obtener_filas_pagina(page, p.tipo_comprobante)

                for fila in filas:
                    if self._cancelar:
                        break

                    await self._notificar(
                        f"Pagina {pag}/{n_paginas} - "
                        f"Descargando {self._estado.completados + 1}/{self._estado.total}: "
                        f"{fila.tipo_y_serie}"
                    )

                    # Descargar XML (omitir si ya existe en disco)
                    ruta_xml = str(ruta_descarga(p.ruc, p.anio, p.mes, fila.tipo_y_serie, "xml", fila.es_retencion))
                    if Path(ruta_xml).exists():
                        ok_xml = True
                    else:
                        ok_xml = await descargar_xml(page, fila.indice, ruta_xml)
                    fila.xml_descargado = ok_xml
                    if not ok_xml:
                        msg = f"XML fallido: {fila.tipo_y_serie}"
                        fila.error = msg
                        self._estado.errores.append(msg)

                    # Descargar PDF/RIDE (omitir si ya existe en disco)
                    ruta_pdf = str(ruta_descarga(p.ruc, p.anio, p.mes, fila.tipo_y_serie, "pdf", fila.es_retencion))
                    if Path(ruta_pdf).exists():
                        ok_pdf = True
                    else:
                        ok_pdf = await descargar_pdf(page, fila.indice, ruta_pdf)
                    fila.pdf_descargado = ok_pdf
                    if not ok_pdf:
                        msg = f"PDF fallido: {fila.tipo_y_serie}"
                        self._estado.errores.append(msg)

                    self._estado.completados += 1
                    await self._on_progress(self._estado)

            from storage.file_manager import carpeta_descarga
            carpeta = carpeta_descarga(p.ruc, p.anio, p.mes)
            self._estado.carpeta_descarga = str(carpeta)
            self._estado.finalizado = True

            resumen = (
                f"Proceso completado: {self._estado.completados} comprobantes procesados"
                + (f", {len(self._estado.errores)} con errores" if self._estado.errores else "")
            )
            await self._notificar(resumen)
            await self._registrar_en_bd(p, str(carpeta))

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

        finally:
            await self._browser.cerrar()

        return self._estado

    async def _registrar_en_bd(self, p: "ParametrosConsulta", carpeta: str) -> None:
        from storage.db_logger import registrar_sesion, RegistroSesion
        errores = len(self._estado.errores)
        completados = self._estado.completados
        if self._estado.cancelado:
            estado = "CANCELADO"
        elif errores > 0 and completados > 0:
            estado = "PARCIAL"
        elif errores > 0 and completados == 0:
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
                    tipo_comprobante_cod=p.tipo_comprobante,
                    tipo_comprobante_desc=TIPOS_COMPROBANTE.get(p.tipo_comprobante, ""),
                    total_encontrados=self._estado.total,
                    total_descargados=completados,
                    total_errores=errores,
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
