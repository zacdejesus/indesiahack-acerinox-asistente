"""Repositorio de datos: el contrato que usa el asistente y sus adaptadores.

El resto del codigo pide cosas en terminos de negocio ("busca productos con
estos filtros") y recibe diccionarios. Cada origen de datos (SQLite en local,
Databricks con los datos del reto) es un adaptador que cumple el contrato
`Inventario`. Cual se usa lo decide DATOS_ORIGEN en `crear_inventario()`.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable
import sqlite3

from . import config


class ErrorDatos(Exception):
    """Fallo al leer los datos (configuracion incompleta, origen no disponible...)."""


@runtime_checkable
class Inventario(Protocol):
    """El contrato. Las herramientas solo usan estos metodos, nunca SQL ni rutas."""

    def buscar(self, grados=None, formato=None, acabado=None, espesor_min=None,
               espesor_max=None, ancho_min=None, almacen=None) -> list[dict]: ...

    def por_ids(self, ids) -> list[dict]: ...

    def valores(self, columna: str) -> list[str]: ...

    def stock_total_kg(self, grado: str, formato: str) -> float: ...

    def demanda_media_mensual_kg(self, grado: str, formato: str, meses: int = 6) -> float: ...

    def ficha(self, grado: str) -> str | None: ...


def crear_inventario() -> Inventario:
    """Devuelve el adaptador elegido en DATOS_ORIGEN. Unico sitio que decide el origen."""
    if config.ORIGEN_DATOS == "sqlite":
        return InventarioSQLite()
    if config.ORIGEN_DATOS == "databricks":
        # import aqui: el SDK de Databricks solo hace falta con este origen
        from .datos_databricks import InventarioDatabricks
        return InventarioDatabricks()
    raise ErrorDatos(f"DATOS_ORIGEN no reconocido: {config.ORIGEN_DATOS!r} (sqlite | databricks)")


class InventarioSQLite:
    def __init__(self, ruta_db: Path = config.RUTA_DB, ruta_fichas: Path = config.RUTA_FICHAS):
        # mode=ro: la conexion es de solo lectura. Aunque hubiera un bug o una
        # inyeccion, el asistente no puede modificar el inventario.
        self._con = sqlite3.connect(f"file:{ruta_db}?mode=ro", uri=True, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._fichas = ruta_fichas

    # --- Productos -----------------------------------------------------------

    def buscar(self, grados=None, formato=None, acabado=None, espesor_min=None,
               espesor_max=None, ancho_min=None, almacen=None) -> list[dict]:
        """Productos que cumplen todos los filtros dados (None = sin filtrar)."""
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

        # Los valores van siempre como parametros (?), nunca pegados al texto del SQL.
        sql = "SELECT * FROM inventario"
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        return [dict(f) for f in self._con.execute(sql + " ORDER BY id", params)]

    def por_ids(self, ids) -> list[dict]:
        """Productos por id, en el mismo orden en que se piden. Los que no existen se omiten."""
        ids = list(dict.fromkeys(ids))
        if not ids:
            return []
        sql = f"SELECT * FROM inventario WHERE id IN ({','.join('?' * len(ids))})"
        encontrados = {f["id"]: dict(f) for f in self._con.execute(sql, ids)}
        return [encontrados[i] for i in ids if i in encontrados]

    def valores(self, columna: str) -> list[str]:
        """Valores distintos de una columna de catalogo (para validar filtros)."""
        if columna not in {"grado", "formato", "acabado", "almacen"}:
            raise ValueError(f"columna no permitida: {columna}")
        return [f[0] for f in self._con.execute(f"SELECT DISTINCT {columna} FROM inventario ORDER BY 1")]

    # --- Demanda -------------------------------------------------------------

    def stock_total_kg(self, grado: str, formato: str) -> float:
        fila = self._con.execute(
            "SELECT COALESCE(SUM(stock_kg), 0) FROM inventario WHERE grado = ? AND formato = ?",
            (grado, formato)).fetchone()
        return fila[0]

    def demanda_media_mensual_kg(self, grado: str, formato: str, meses: int = 6) -> float:
        """Media de kg pedidos al mes en los ultimos `meses` meses del historico."""
        fila = self._con.execute(
            """SELECT AVG(kg) FROM (
                   SELECT SUM(kg_pedidos) AS kg FROM demanda
                   WHERE grado = ? AND formato = ?
                   GROUP BY mes ORDER BY mes DESC LIMIT ?)""",
            (grado, formato, meses)).fetchone()
        return fila[0] or 0.0

    # --- Fichas tecnicas -----------------------------------------------------

    def ficha(self, grado: str) -> str | None:
        ruta = self._fichas / f"{grado}.md"
        return ruta.read_text(encoding="utf-8") if ruta.exists() else None
