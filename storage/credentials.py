"""
Manejo seguro de credenciales SRI via Windows Credential Manager (DPAPI).

Las credenciales se cifran con la cuenta de Windows del usuario actual.
Nadie puede leerlas sin estar autenticado en esa sesion de Windows.
"""

import logging

logger = logging.getLogger(__name__)

_SERVICE = "sri-scraper-banco-guayaquil"
# La cedula adicional (acceso delegado, opcional) se guarda como una entrada
# separada bajo el mismo servicio: keyring solo permite un password por
# (servicio, usuario), asi que no puede compartir la entrada del RUC.
_SUFIJO_CI_ADICIONAL = "::ci_adicional"


def guardar(ruc: str, password: str, ci_adicional: str = "") -> None:
    import keyring
    keyring.set_password(_SERVICE, ruc, password)
    if ci_adicional:
        keyring.set_password(_SERVICE, f"{ruc}{_SUFIJO_CI_ADICIONAL}", ci_adicional)
    else:
        eliminar_ci_adicional(ruc)
    logger.info("Credenciales guardadas en Credential Manager para RUC %s", ruc)


def obtener(ruc: str) -> str | None:
    import keyring
    return keyring.get_password(_SERVICE, ruc)


def obtener_ci_adicional(ruc: str) -> str | None:
    import keyring
    return keyring.get_password(_SERVICE, f"{ruc}{_SUFIJO_CI_ADICIONAL}")


def eliminar_ci_adicional(ruc: str) -> None:
    try:
        import keyring
        keyring.delete_password(_SERVICE, f"{ruc}{_SUFIJO_CI_ADICIONAL}")
    except Exception:
        pass


def eliminar(ruc: str) -> None:
    try:
        import keyring
        keyring.delete_password(_SERVICE, ruc)
        logger.info("Credenciales eliminadas del Credential Manager para RUC %s", ruc)
    except Exception:
        pass
    eliminar_ci_adicional(ruc)


def tiene_credenciales(ruc: str) -> bool:
    return obtener(ruc) is not None
