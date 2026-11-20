"""Adaptador de Databricks para el contrato `Inventario` (asistente/datos.py).

Lee las tablas de inventario y demanda con un SQL Warehouse (databricks-sql-connector)
y las fichas tecnicas de un volumen de Unity Catalog (databricks-sdk).

Los nombres reales de columnas de Acerinox se ajustan en MAPEO_*: a la izquierda
el nombre que usa el asistente, a la derecha el de la tabla. Si Acerinox llama
distinto a una columna, se cambia solo aqui. Las tablas y el volumen se eligen
con variables de entorno (ver asistente/config.py).
"""

import re
import threading
from typing import Callable

from . import config
from .datos import ErrorDatos

# nombre interno -> nombre real en Databricks
MAPEO_INVENTARIO = {c: c for c in (
    "id", "grado", "familia", "formato", "acabado", "espesor_mm", "ancho_mm", "largo_mm",
    "peso_kg", "stock_kg", "almacen", "precio_eur_kg", "margen_pct", "rotacion_dias", "norma")}
MAPEO_DEMANDA = {c: c for c in ("mes", "grado", "formato", "num_pedidos", "kg_pedidos")}

# Solo letras, digitos y guion bajo: los nombres van pegados al SQL, asi que se validan.
_IDENTIFICADOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Un grado llega del modelo y acaba en una ruta del volumen: sin barras ni "..".
_GRADO = re.compile(r"^[A-Za-z0-9_.-]+$")


def _identificador(nombre: str) -> str:
    if not _IDENTIFICADOR.match(nombre or ""):
        raise ErrorDatos(f"Nombre no valido en la configuracion: {nombre!r}")
    return nombre


def _tabla(nombre: str) -> str:
    """catalogo.esquema.tabla, todo validado."""
    partes = (config.DBX_CATALOGO, config.DBX_ESQUEMA, nombre)
    if not all(partes):
        raise ErrorDatos("Faltan DATABRICKS_CATALOGO o DATABRICKS_ESQUEMA")
    return ".".join(_identificador(p) for p in partes)


def _subconsulta(tabla: str, mapeo: dict) -> str:
    """Tabla real con sus columnas ya renombradas a los nombres internos."""
    columnas = ", ".join(f"{_identificador(real)} AS {interno}" for interno, real in mapeo.items())
    return f"(SELECT {columnas} FROM {tabla})"


def _nombrado(sql: str, params: list) -> tuple[str, dict]:
    """Cambia los ? del SQL por marcadores con nombre (:p0, :p1...), que es lo que
    espera el conector. Los valores viajan aparte, nunca dentro del texto."""
    partes = sql.split("?")
    if len(partes) - 1 != len(params):
        raise ValueError("numero de parametros distinto al de marcadores")
    texto, nombrados = partes[0], {}
    for i, (trozo, valor) in enumerate(zip(partes[1:], params)):
        texto += f":p{i}" + trozo
        nombrados[f"p{i}"] = valor
    return texto, nombrados


def _conexion_real():
    # import aqui: el conector solo hace falta con este origen
    from databricks import sql
    if not (config.DBX_HOST and config.DBX_HTTP_PATH and config.DBX_TOKEN):
        raise ErrorDatos("Faltan DATABRICKS_HOST, DATABRICKS_HTTP_PATH o DATABRICKS_TOKEN")
    return sql.connect(server_hostname=config.DBX_HOST, http_path=config.DBX_HTTP_PATH,
                       access_token=config.DBX_TOKEN)


def _ficha_real(grado: str) -> str | None:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.errors import NotFound
    if not config.DBX_VOLUMEN_FICHAS:
        raise ErrorDatos("Falta DATABRICKS_VOLUMEN_FICHAS")
    w = WorkspaceClient(host=f"https://{config.DBX_HOST}", token=config.DBX_TOKEN)
    try:
        return w.files.download(f"{config.DBX_VOLUMEN_FICHAS}/{grado}.md").contents.read().decode("utf-8")
    except NotFound:
        return None


class InventarioDatabricks:
    def __init__(self, conexion: Callable = _conexion_real, leer_ficha: Callable = _ficha_real):
        # Las dos funciones son inyectables: los tests les pasan una falsa, sin red.
        self._conexion = conexion
        self._leer_ficha = leer_ficha
        self._con = None
        self._lock = threading.Lock()  # una conexion, usada desde varios hilos de Streamlit
        self._inv = _subconsulta(_tabla(config.DBX_TABLA_INVENTARIO), MAPEO_INVENTARIO)
        self._dem = _subconsulta(_tabla(config.DBX_TABLA_DEMANDA), MAPEO_DEMANDA)

    def _consulta(self, sql: str, params=()) -> list[dict]:
        texto, nombrados = _nombrado(sql, list(params))
        with self._lock:
            if self._con is None:
                self._con = self._conexion()
            with self._con.cursor() as cur:
                cur.execute(texto, parameters=nombrados or None)
                columnas = [d[0] for d in cur.description]
                return [dict(zip(columnas, fila)) for fila in cur.fetchall()]

    # --- Productos -----------------------------------------------------------

    def buscar(self, grados=None, formato=None, acabado=None, espesor_min=None,
               espesor_max=None, ancho_min=None, almacen=None) -> list[dict]:
        cond, params = [], []
        if grados:
            cond.append(f"grado IN ({','.join('?' * len(grados))})")
            params += list(grados)
        for columna, valor in (("formato", formato), ("acabado", acabado), ("almacen", almacen)):
            if valor is not None:
                cond.append(f"{columna} = ?")
                params.append(valor)
        if espesor_min is not None:
            cond.append("espesor_mm >= ?")
            params.append(espesor_min)
        if espesor_max is not None:
            cond.append("espesor_mm <= ?")
            params.append(espesor_max)
        if ancho_min is not None:
            cond.append("ancho_mm >= ?")
            params.append(ancho_min)

        sql = f"SELECT * FROM {self._inv} AS inventario"
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        return self._consulta(sql + " ORDER BY id", params)

    def por_ids(self, ids) -> list[dict]:
        ids = list(dict.fromkeys(ids))
        if not ids:
            return []
        sql = f"SELECT * FROM {self._inv} AS inventario WHERE id IN ({','.join('?' * len(ids))})"
        encontrados = {f["id"]: f for f in self._consulta(sql, ids)}
        return [encontrados[i] for i in ids if i in encontrados]

    def valores(self, columna: str) -> list[str]:
        if columna not in {"grado", "formato", "acabado", "almacen"}:
            raise ValueError(f"columna no permitida: {columna}")
        filas = self._consulta(f"SELECT DISTINCT {columna} FROM {self._inv} AS inventario ORDER BY 1")
        return [f[columna] for f in filas]

    # --- Demanda -------------------------------------------------------------

    def stock_total_kg(self, grado: str, formato: str) -> float:
        filas = self._consulta(
            f"SELECT COALESCE(SUM(stock_kg), 0) AS total FROM {self._inv} AS inventario "
            "WHERE grado = ? AND formato = ?", (grado, formato))
        return float(filas[0]["total"])

    def demanda_media_mensual_kg(self, grado: str, formato: str, meses: int = 6) -> float:
        # LIMIT no admite marcador en todas las versiones: el numero se fuerza a entero aqui.
        limite = int(meses)
        filas = self._consulta(
            f"""SELECT AVG(kg) AS media FROM (
                    SELECT SUM(kg_pedidos) AS kg FROM {self._dem} AS demanda
                    WHERE grado = ? AND formato = ?
                    GROUP BY mes ORDER BY mes DESC LIMIT {limite})""",
            (grado, formato))
        return float(filas[0]["media"] or 0.0)

    # --- Fichas tecnicas -----------------------------------------------------

    def ficha(self, grado: str) -> str | None:
        if not _GRADO.match(grado or ""):
            raise ValueError(f"grado no valido para una ficha: {grado!r}")
        return self._leer_ficha(grado)
