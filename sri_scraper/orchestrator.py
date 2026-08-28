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

from playwright.async_api import TimeoutError as PlaywrightTimeout

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

# Red de seguridad adicional a los reintentos/timeouts internos de consulta.py:
# si una descarga individual o la actualizacion de progreso de la UI se cuelgan
# por una causa imprevista (ej. un dialogo nativo del SO, un cliente
# desconectado), esto evita que TODA la corrida se congele indefinidamente.
TIMEOUT_DESCARGA_ITEM = 90       # segundos
TIMEOUT_CALLBACK_PROGRESO = 10   # segundos
TIMEOUT_PAGINACION = 60          # segundos (cambio de pagina / lectura de filas)
TIMEOUT_INTERVENCION = 300       # segundos: limite de espera por intervencion manual


@dataclass
class ParametrosConsulta:
    ruc: str
    password: str
    anio: int
    mes: int
    dia: int                    # 0 = Todos
    tipos_comprobante: list[str]  # uno o mas; mismo periodo/dia para todos
    # Cedula adicional (opcional): acceso delegado que el contribuyente genera
    # en el SRI con clave propia, distinta a la del RUC original, para no
    # compartir sus credenciales principales.
    ci_adicional: str = ""
    codigo_colaborador: str = ""
    usuario_colaborador: str = ""


@dataclass
class EstadoProceso:
    fase: str = "Iniciando"
    total: int = 0                    # acumulado de TODOS los tipos pedidos
    completados: int = 0              # acumulado de TODOS los tipos pedidos
    errores: list[str] = field(default_factory=list)
    carpeta_descarga: str = ""
    # De los `completados`, cuantos ya existian en disco (se omitio la descarga)
    # vs cuantos se bajaron de verdad en esta corrida. Un comprobante cuenta como
    # "nuevo" si al menos uno de sus dos archivos (XML o PDF) no existia todavia.
    ya_existian: int = 0
    nuevos_descargados: int = 0
    # Rutas (absolutas, en el servidor) de los archivos efectivamente bajados en
    # esta corrida -- permite ofrecer un ZIP con "solo lo nuevo" en vez de forzar
    # a volver a empaquetar toda la carpeta del periodo cuando la mayoria ya se
    # habia descargado antes.
    archivos_nuevos: list[str] = field(default_factory=list)
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
        await self._notificar_progreso_directo()

    async def _notificar_progreso_directo(self) -> None:
        """
        Envia el estado actual a la UI sin cambiar de fase (se usa tanto en
        cambios de fase como en el progreso item a item de la descarga).
        Acotado con timeout: si el callback de la UI se cuelga (ej. cliente
        desconectado), no debe frenar el resto del scraping.
        """
        try:
            await asyncio.wait_for(self._on_progress(self._estado), timeout=TIMEOUT_CALLBACK_PROGRESO)
        except Exception as e:
            logger.warning("Error o timeout en callback de progreso: %s", e)

    async def _con_watchdog(self, corutina, timeout: float, descripcion: str, valor_default):
        """
        Ejecuta cualquier paso puntual del scraping (descarga de un archivo,
        cambio de pagina, lectura de filas) con un limite de tiempo total.
        consulta.py ya reintenta y acota cada operacion por su cuenta; este
        watchdog es la ultima linea de defensa para que un cuelgue imprevisto
        (ej. un elemento que nunca queda clickeable, un cliente desconectado)
        no congele el resto de la corrida — se registra la advertencia y se
        continua con `valor_default` en vez de quedar esperando para siempre.
        """
        try:
            return await asyncio.wait_for(corutina, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "%s excedio %ds sin responder — se continua con valor por defecto.",
                descripcion, timeout,
            )
            return valor_default
        except Exception as e:
            logger.warning("Error inesperado en %s: %s", descripcion, e)
            return valor_default

    async def _descargar_con_watchdog(self, corutina) -> bool:
        return await self._con_watchdog(corutina, TIMEOUT_DESCARGA_ITEM, "Descarga individual", False)

    async def _paginar_con_watchdog(self, corutina) -> None:
        await self._con_watchdog(corutina, TIMEOUT_PAGINACION, "Cambio de pagina", None)

    async def _leer_filas_con_watchdog(self, corutina) -> list:
        return await self._con_watchdog(corutina, TIMEOUT_PAGINACION, "Lectura de filas de la tabla", [])

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
                await login_sri(page, p.ruc, p.password, ci_adicional=p.ci_adicional, on_reintento=_progreso_login)
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

                # El navegador corre en la maquina servidor, no en el equipo del
                # usuario: nadie puede ver la ventana ni resolver nada a mano ahi,
                # asi que la UNICA palanca real ante un rechazo es reintentar en
                # automatico. El reCAPTCHA de este formulario tambien es
                # invisible/score-based (nunca presenta un desafio visual, ni
                # aunque alguien estuviera mirando la pantalla del servidor).
                MAX_INTENTOS_FILTROS = 4
                filtro_ok = False
                ultimo_error: Exception | None = None
                for intento_filtros in range(1, MAX_INTENTOS_FILTROS + 1):
                    if self._cancelar:
                        break
                    # Lectura larga solo en el primer tipo pedido (primer envio
                    # puntuado de la corrida) o si este tipo ya fue rechazado por
                    # verificacion antes (intento > 1): ahi conviene recuperar
                    # confianza con la lectura completa. Para el 2do+ tipo, en su
                    # primer intento, la sesion ya viene con confianza establecida
                    # del envio anterior y no hace falta repetirla.
                    lectura_completa = idx == 1 or intento_filtros > 1
                    try:
                        await aplicar_filtros(
                            page, str(p.anio), str(p.mes), str(p.dia), tipo,
                            lectura_completa=lectura_completa,
                        )
                        filtro_ok = True
                        break
                    except (ErrorCaptchaConsulta, RuntimeError, PlaywrightTimeout) as e:
                        # RuntimeError cubre el caso de esperar_tabla() (la tabla no
                        # cargo por cualquier motivo, no solo un mensaje explicito de
                        # captcha) y PlaywrightTimeout cubre esperas puntuales que
                        # revienten dentro de aplicar_filtros. Antes solo se atrapaba
                        # ErrorCaptchaConsulta aqui, asi que estas otras fallas se
                        # escapaban del loop y tumbaban TODA la corrida (todos los
                        # tipos, no solo el actual) via el catch-all de mas abajo.
                        ultimo_error = e
                        if intento_filtros < MAX_INTENTOS_FILTROS:
                            logger.info(
                                "Consulta fallida (tipo %s, intento %d/%d): %s. Reintentando...",
                                tipo, intento_filtros, MAX_INTENTOS_FILTROS, e,
                            )
                            await _notificar_tipo(
                                f"Verificacion no superada - reintentando ({intento_filtros}/{MAX_INTENTOS_FILTROS})"
                            )
                            await pausa(5000, 9000)
                            await navegar_a_comprobantes(page, p.ruc, p.password)
                            # En la ultima oportunidad antes de rendirse, un warm-up
                            # adicional (revisitar el perfil, igual que al inicio de
                            # la corrida) intenta reconstruir confianza de sesion.
                            if intento_filtros == MAX_INTENTOS_FILTROS - 1:
                                await self._calentar_sesion(page)

                if not filtro_ok and ultimo_error is not None:
                    self._estado.errores.append(f"{nombre_tipo}: {ultimo_error}")

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
                n_paginas = await self._con_watchdog(
                    obtener_total_paginas(page), TIMEOUT_PAGINACION, "Lectura del total de paginas", 1,
                )
                logger.info("Tipo %s: total de paginas en tabla: %d", tipo, n_paginas)

                conteo_por_pagina: list[int] = []
                for pag in range(1, n_paginas + 1):
                    if pag > 1:
                        await self._paginar_con_watchdog(ir_a_pagina(page, pag))
                    filas_pag = await self._leer_filas_con_watchdog(obtener_filas_pagina(page, tipo))
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
                        await self._paginar_con_watchdog(ir_a_pagina(page, pag))

                    filas = await self._leer_filas_con_watchdog(obtener_filas_pagina(page, tipo))

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
                            p.ruc, p.anio, p.mes, fila.nombre_archivo, "xml", fila.es_retencion, subcarpeta,
                        ))
                        xml_ya_existia = Path(ruta_xml).exists()
                        if xml_ya_existia:
                            ok_xml = True
                        else:
                            ok_xml = await self._descargar_con_watchdog(
                                descargar_xml(page, fila.indice, ruta_xml)
                            )
                        fila.xml_descargado = ok_xml
                        if not ok_xml:
                            msg = f"{nombre_tipo}: XML fallido: {fila.tipo_y_serie}"
                            fila.error = msg
                            self._estado.errores.append(msg)

                        # Descargar PDF/RIDE (omitir si ya existe en disco)
                        ruta_pdf = str(ruta_descarga(
                            p.ruc, p.anio, p.mes, fila.nombre_archivo, "pdf", fila.es_retencion, subcarpeta,
                        ))
                        pdf_ya_existia = Path(ruta_pdf).exists()
                        if pdf_ya_existia:
                            ok_pdf = True
                        else:
                            ok_pdf = await self._descargar_con_watchdog(
                                descargar_pdf(page, fila.indice, ruta_pdf)
                            )
                        fila.pdf_descargado = ok_pdf
                        if not ok_pdf:
                            msg = f"{nombre_tipo}: PDF fallido: {fila.tipo_y_serie}"
                            self._estado.errores.append(msg)

                        # Un comprobante es "nuevo" si al menos uno de sus dos
                        # archivos no existia antes de esta corrida. Se guardan
                        # las rutas de ambos (si quedaron en disco) para poder
                        # ofrecer despues un ZIP con unicamente lo nuevo.
                        if xml_ya_existia and pdf_ya_existia:
                            self._estado.ya_existian += 1
                        else:
                            self._estado.nuevos_descargados += 1
                            for ruta in (ruta_xml, ruta_pdf):
                                if Path(ruta).exists():
                                    self._estado.archivos_nuevos.append(ruta)

                        completados_tipo += 1
                        self._estado.completados = completados_previos + completados_tipo
                        self._estado.completados_tipo_actual = completados_tipo
                        await self._notificar_progreso_directo()

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

    async def _esperar_intervencion(self, page) -> bool:
        """
        Restaura la ventana del navegador y espera a que el usuario resuelva
        la intervencion (ej. hacer clic en 'Consultar' a mano si el CAPTCHA
        bloqueo los envios automaticos). La ventana se mantiene visible.

        Acotado a TIMEOUT_INTERVENCION: `_orquestador_activo` es un singleton
        global en pages.py, asi que una espera indefinida aqui bloquearia
        cualquier otra corrida en todo el proceso si nadie responde. Si se
        agota el tiempo, se retoma el flujo automatico (que terminara
        marcando el tipo actual como fallido) en vez de colgar la corrida.

        Retorna True si el usuario confirmo con 'Continuar', False si se
        cancelo el proceso o se agoto el tiempo de espera.
        """
        logger.info("Esperando intervencion manual del usuario (limite %ds)...", TIMEOUT_INTERVENCION)
        await self._browser.mostrar()
        transcurrido = 0
        agotado = False
        while self._estado.requiere_intervencion and not self._cancelar:
            if transcurrido >= TIMEOUT_INTERVENCION:
                logger.warning("Se agoto el tiempo de espera de intervencion manual.")
                agotado = True
                break
            await asyncio.sleep(1)
            transcurrido += 1
        self._estado.requiere_intervencion = False
        self._estado.mensaje_intervencion = ""
        return not agotado and not self._cancelar

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
