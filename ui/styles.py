"""
Estilos CSS y constantes de marca para Banco Guayaquil.
Paleta oficial:
  Magenta principal : #D2006E
  Navy oscuro       : #160F41
  Navy medio        : #5C577A
  Gris claro        : #E7E7EC
  Cyan acento       : #9FDCEE
"""

# ── Paleta de marca ──────────────────────────────────────────────────────────
MAGENTA          = "#D2006E"
MAGENTA_HOVER    = "#A8005A"
MAGENTA_LIGHT    = "#E04D9A"
MAGENTA_LIGHTEST = "#FAE5F0"

NAVY             = "#160F41"
NAVY_MED         = "#5C577A"
NAVY_LIGHT       = "#E7E7EC"

CYAN             = "#9FDCEE"
CYAN_LIGHT       = "#E2F4FA"

BLANCO           = "#FFFFFF"
FONDO            = "#F4F3F8"

ERROR_COLOR      = "#C0392B"
SUCCESS_COLOR    = "#1A7A4A"
WARNING_COLOR    = "#856404"
BORDER_COLOR     = "#E0DEEA"

# Aliases para compatibilidad con codigo existente
GRIS_OSCURO = NAVY
GRIS_MEDIO  = NAVY_MED
GRIS_CLARO  = FONDO


CSS_GLOBAL = f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">

<style>
  /* ── Reset base ────────────────────────────────── */
  *, *::before, *::after {{
    box-sizing: border-box;
  }}

  html, body {{
    height: 100%;
    width: 100%;
    overflow-x: hidden;
  }}

  body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background-color: {FONDO};
    color: {NAVY};
    font-size: 15px;
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
    margin: 0;
    padding: 0;
  }}

  /* ── Forzar layout full-width en Quasar/NiceGUI ─ */
  #app, .q-app {{
    width: 100% !important;
    min-height: 100vh !important;
  }}

  .q-layout, .q-page-container {{
    width: 100% !important;
    max-width: none !important;
    padding: 0 !important;
  }}

  .q-page {{
    padding: 0 !important;
    min-height: 100vh !important;
    width: 100% !important;
  }}

  .nicegui-content {{
    width: 100% !important;
    max-width: none !important;
    padding: 0 !important;
    display: flex !important;
    flex-direction: column !important;
    min-height: 100vh !important;
  }}

  /* ── Tipografia global ─────────────────────────── */
  .material-icons, .q-icon {{
    font-family: 'Material Icons' !important;
  }}

  /* ── Inputs Quasar ─────────────────────────────── */
  .q-field--outlined .q-field__control {{
    border-radius: 8px !important;
  }}
  .q-field--outlined.q-field--focused .q-field__control::after {{
    border-color: {MAGENTA} !important;
    border-width: 2px !important;
  }}
  .q-field__label {{
    font-family: 'Inter', sans-serif !important;
    font-size: 14px !important;
    color: {NAVY_MED} !important;
  }}
  .q-field__native, .q-field__input {{
    font-family: 'Inter', sans-serif !important;
    font-size: 15px !important;
    color: {NAVY} !important;
  }}

  /* ── Select/Dropdown ───────────────────────────── */
  .q-menu {{
    border-radius: 8px !important;
    border: 1px solid {BORDER_COLOR} !important;
    box-shadow: 0 4px 20px rgba(22,15,65,0.12) !important;
    font-family: 'Inter', sans-serif !important;
  }}
  .q-item__label {{
    font-family: 'Inter', sans-serif !important;
    font-size: 14px !important;
    color: {NAVY} !important;
  }}
  .q-item--active {{
    color: {MAGENTA} !important;
  }}

  /* ── Links ─────────────────────────────────────── */
  a {{ color: {MAGENTA}; text-decoration: none; }}
  a:hover {{ text-decoration: underline; opacity: 0.85; }}

  /* ── Scrollbar ─────────────────────────────────── */
  ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
  ::-webkit-scrollbar-track {{ background: {FONDO}; }}
  ::-webkit-scrollbar-thumb {{ background: {NAVY_LIGHT}; border-radius: 3px; }}
  ::-webkit-scrollbar-thumb:hover {{ background: {NAVY_MED}; }}
</style>
"""
