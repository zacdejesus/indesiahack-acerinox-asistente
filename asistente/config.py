"""Configuracion: rutas y variables de entorno. Ningun otro modulo lee os.environ."""

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

# Datos
DATA = Path(os.getenv("DATA_DIR", RAIZ / "data"))
RUTA_DB = DATA / "inventario.db"
RUTA_FICHAS = DATA / "fichas_tecnicas"

# Origen de datos: sqlite (local, sintetico) | databricks (datos del reto)
ORIGEN_DATOS = os.getenv("DATOS_ORIGEN", "sqlite")

# Databricks. El host va sin https://, p. ej. adb-123.azuredatabricks.net
DBX_HOST = os.getenv("DATABRICKS_HOST", "")
DBX_HTTP_PATH = os.getenv("DATABRICKS_HTTP_PATH", "")   # ruta del SQL Warehouse
DBX_TOKEN = os.getenv("DATABRICKS_TOKEN", "")
DBX_CATALOGO = os.getenv("DATABRICKS_CATALOGO", "")
DBX_ESQUEMA = os.getenv("DATABRICKS_ESQUEMA", "")
DBX_TABLA_INVENTARIO = os.getenv("DATABRICKS_TABLA_INVENTARIO", "inventario")
DBX_TABLA_DEMANDA = os.getenv("DATABRICKS_TABLA_DEMANDA", "demanda")
DBX_VOLUMEN_FICHAS = os.getenv("DATABRICKS_VOLUMEN_FICHAS", "")  # /Volumes/<cat>/<esq>/<vol>/<carpeta>

# Forma de un id de producto. El validador lo usa para detectar ids en el texto
# del modelo. Con los datos reales de Acerinox habra que ajustarlo.
# Cuatro o cinco digitos: asi un id mal escrito (ACX-0039) tambien se detecta y el verificador
# lo marca como inventado. Con solo cinco pasaba sin que nadie lo viera.
PATRON_ID = os.getenv("PATRON_ID", r"\bACX-\d{4,5}\b")

# LLM
PROVEEDOR = os.getenv("LLM_PROVIDER", "anthropic")
MODELO = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
ESFUERZO = os.getenv("LLM_EFFORT", "medium")  # low | medium | high
MAX_TOKENS = 16000

# Servidor local o cualquier API compatible con OpenAI (LLM_PROVIDER=local)
LOCAL_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1")
LOCAL_MODELO = os.getenv("LLM_MODEL", "")      # vacio = el primero que anuncie el servidor
LOCAL_CLAVE = os.getenv("LLM_API_KEY", "")     # los servidores locales no suelen pedirla
LOCAL_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2048"))
LOCAL_TIMEOUT_S = int(os.getenv("LLM_TIMEOUT_S", "600"))
# Carpeta del servidor MLX: la evaluacion lee su pid y su log para medir memoria y cache.
SERVIDOR_MLX_DIR = Path(os.getenv("SERVERMLX_DIR", RAIZ.parent / "serverMLX"))

# Tope de vueltas del bucle del agente por pregunta (evita bucles infinitos y costes)
MAX_PASOS = 8

# Servidor HTTP y PWA (paquete servidor/). Datos propios de la aplicacion
# (usuarios, peticiones de oferta, avisos): base aparte, el inventario sigue de solo lectura.
RUTA_APP_DB = Path(os.getenv("APP_DB", DATA / "app.db"))
# Usuarios de demo, "usuario:clave:rol" separados por comas. Cambiarlos antes de enseñarlo fuera.
USUARIOS_DEMO = os.getenv("DEMO_USUARIOS", "comercial:comercial:comercial,cliente:cliente:cliente")
SERVIDOR_HOST = os.getenv("SERVIDOR_HOST", "127.0.0.1")
SERVIDOR_PUERTO = int(os.getenv("SERVIDOR_PUERTO", "8000"))
# Solo True detras de HTTPS: con True el navegador no envia la cookie por http.
COOKIE_SEGURA = os.getenv("COOKIE_SEGURA", "0") == "1"
