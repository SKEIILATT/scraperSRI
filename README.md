# SRI Scraper - Banco Guayaquil

Herramienta interna para descarga automatizada de comprobantes electronicos desde el portal SRI en linea.

---

## Flujo de la aplicacion

```
1. Login interno (codigo de colaborador + usuario Banco Guayaquil)
2. Credenciales SRI del contribuyente (solo en memoria)
3. Configuracion del periodo y tipo de comprobante
4. Descarga automatizada (XML + PDF/RIDE de cada comprobante + reporte)
5. Resultado con ZIP descargable
```

---

## Instalacion y primera ejecucion

### Requisitos previos
- Python 3.11+
- En servidor Linux sin GUI: instalar Xvfb (`apt install xvfb`)
- En Windows: ninguna dependencia de sistema adicional

### Pasos

```bash
# 1. Crear entorno virtual
python -m venv .venv
source .venv/bin/activate        # Linux/Mac
.venv\Scripts\activate           # Windows

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Instalar navegador de Playwright
playwright install chromium
playwright install-deps chromium  # Solo Linux: instala librerias del sistema

# 4. Configurar variables de entorno
cp .env.example .env
# Editar .env con los valores reales

# 5. Ejecutar
python main.py
```

La aplicacion estara disponible en `http://localhost:8080`.

---

## Despliegue en produccion (sin Docker)

### Con systemd (Linux)

```bash
# Copiar archivos
sudo cp deploy/sri-scraper.service /etc/systemd/system/
sudo mkdir -p /opt/sri-scraper
sudo cp -r . /opt/sri-scraper/
sudo cp .env /opt/sri-scraper/.env

# Crear usuario de servicio
sudo useradd -r -s /bin/false appuser
sudo chown -R appuser:appuser /opt/sri-scraper

# Habilitar e iniciar
sudo systemctl daemon-reload
sudo systemctl enable sri-scraper
sudo systemctl start sri-scraper
sudo systemctl status sri-scraper

# Ver logs
sudo journalctl -u sri-scraper -f
```

### Con Supervisor (alternativa)

```bash
sudo cp deploy/sri-scraper.conf /etc/supervisor/conf.d/
sudo mkdir -p /var/log/sri-scraper
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start sri-scraper
```

### Nginx como reverse proxy

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/sri-scraper
sudo ln -s /etc/nginx/sites-available/sri-scraper /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### Servidor Linux sin entorno grafico (Xvfb)

Si el servidor no tiene pantalla fisica, Playwright necesita display virtual:

```bash
# Instalar Xvfb
sudo apt install xvfb

# Modificar el ExecStart en sri-scraper.service:
ExecStart=/usr/bin/xvfb-run -a /opt/sri-scraper/.venv/bin/python main.py

# Y en .env:
BROWSER_HEADLESS=false   # Mantener headed con Xvfb, no true
```

---

## Validacion previa del reCAPTCHA

**EJECUTAR ANTES DE DESPLEGAR EN PRODUCCION.**

El SRI usa Google reCAPTCHA Enterprise invisible. La estrategia de automatizacion
se basa en perfil persistente + modo headed + interacciones humanizadas.

```bash
# Editar validar_recaptcha.py y completar RUC_PRUEBA y PASSWORD_PRUEBA
python validar_recaptcha.py
```

El script genera screenshots (`recaptcha_estado_login.png`, etc.) y reporta el resultado.

### Interpretacion de resultados

| Resultado | Significado | Accion |
|-----------|-------------|--------|
| EXITO TOTAL | Flujo completo sin intervencion | Desplegar con confianza |
| RESUELTO_AUTO | reCAPTCHA se resuelve pero lento | Aumentar TIMEOUT_RECAPTCHA |
| NO_RESUELTO_EN_TIEMPO | reCAPTCHA bloquea | Fallback manual es critico |
| Error de credenciales | RUC/password incorrectos | Corregir y re-ejecutar |

### Limitaciones conocidas del reCAPTCHA

- reCAPTCHA Enterprise analiza senales de comportamiento del navegador.
- Un perfil nuevo tendra mayor friccion que uno con historial de uso.
- No hay garantia de 100% de ejecuciones desatendidas de forma indefinida.
- El fallback de intervencion manual (paso 4 de la UI) esta siempre activo como red de seguridad.
- NO se usan servicios de terceros de resolucion de captchas (fuera de alcance del proyecto).

---

## Conectar validacion real de colaboradores

**Estado actual:** `AUTH_BYPASS_MODE=true` (login siempre aprobado sin consultar BD).

**Para conectar la base real:**

1. Editar `.env`:
   ```
   AUTH_BYPASS_MODE=false
   DB_HOST=GYEINTNEGDB01
   DB_PORT=4433
   DB_NAME=PROCESOS_BI
   ```
   La conexion usa autenticacion integrada de Windows (`Trusted_Connection=yes`),
   no requiere usuario ni contrasena — usa las credenciales de Windows del proceso
   que ejecuta la app.

2. La funcion `validar_colaborador()` en `auth/colaborador.py` ya contiene la logica
   SQL para consultar `STAGING.DBO.DW_DIM_COLABORADORES`. No se necesita modificar
   ningun otro archivo.

3. Asegurar que el Driver ODBC 17 for SQL Server este instalado en el servidor:
   ```bash
   # Ubuntu/Debian
   curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
   curl https://packages.microsoft.com/config/ubuntu/22.04/prod.list > /etc/apt/sources.list.d/mssql-release.list
   apt update && ACCEPT_EULA=Y apt install -y msodbcsql17
   ```

---

## Variables de entorno

Ver `.env.example` para la lista completa documentada.

| Variable | Descripcion | Default |
|----------|-------------|---------|
| `AUTH_BYPASS_MODE` | Saltar validacion de BD de colaboradores | `true` |
| `VERIFICAR_AREA` | Exigir centro de costo autorizado ademas del login | `false` |
| `DB_HOST` | Host SQL Server | `GYEINTNEGDB01` |
| `DB_PORT` | Puerto SQL Server | `4433` |
| `DB_NAME` | Nombre de base de datos (produccion: `PROCESOS_BI`) | `PROCESOS_BI` |
| `APP_HOST` | IP de escucha de la app | `0.0.0.0` |
| `APP_PORT` | Puerto de la app | `8080` |
| `APP_SECRET_KEY` | Clave de sesiones (cambiar en produccion) | `change_me` |
| `BROWSER_PROFILE_DIR` | Carpeta del perfil persistente de Chrome | `./browser_profile` |
| `BROWSER_HEADLESS` | Modo headless (false recomendado) | `false` |
| `DOWNLOADS_BASE_DIR` | Carpeta raiz de descargas | `./downloads` |
| `LOG_LEVEL` | Nivel de log | `INFO` |

---

## Estructura del proyecto

```
sri-scraper/
├── main.py                    # Punto de entrada unico
├── config.py                  # Variables de entorno
├── validar_recaptcha.py       # Script de validacion previa (ejecutar 1 vez)
├── requirements.txt
├── .env.example
├── auth/
│   ├── __init__.py
│   └── colaborador.py         # Validacion de colaboradores + bypass documentado
├── sri_scraper/
│   ├── __init__.py
│   ├── browser.py             # BrowserManager (perfil persistente, anti-deteccion)
│   ├── humanize.py            # Interacciones humanizadas (delays, mouse)
│   ├── login.py               # Login en SRI en linea
│   ├── consulta.py            # Navegacion, filtros, descarga de documentos
│   └── orchestrator.py        # Orquestador del proceso completo con callbacks
├── ui/
│   ├── __init__.py
│   ├── styles.py              # CSS y constantes de marca Banco Guayaquil
│   ├── state.py               # Estado de sesion por usuario
│   ├── components.py          # Componentes reutilizables (stepper, progreso, etc.)
│   └── pages.py               # Paginas NiceGUI (5 pasos del wizard)
├── storage/
│   ├── __init__.py
│   └── file_manager.py        # Carpetas, nombres de archivo, generacion de ZIP
└── deploy/
    ├── sri-scraper.service    # Systemd
    ├── sri-scraper.conf       # Supervisor
    └── nginx.conf             # Nginx reverse proxy
```

---

## Pasos pendientes antes de produccion

- [ ] Ejecutar `validar_recaptcha.py` con credenciales reales y documentar resultado
- [ ] Configurar `AUTH_BYPASS_MODE=false` y verificar conexion a SQL Server
- [ ] Cambiar `APP_SECRET_KEY` a un valor aleatorio seguro en `.env`
- [ ] Instalar Playwright: `playwright install chromium && playwright install-deps chromium`
- [ ] En servidor Linux: configurar Xvfb si no hay pantalla fisica
- [ ] Configurar Nginx con el dominio interno del banco
- [ ] Revisar permisos de escritura en `DOWNLOADS_BASE_DIR` y `BROWSER_PROFILE_DIR`
- [ ] Definir politica de limpieza de archivos descargados (no implementada aun)
