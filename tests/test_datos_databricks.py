"""Adaptador de Databricks: se prueba con una conexion falsa, sin red ni clave.

Lo que se comprueba: que cumple el contrato, que los valores viajan como
parametros (nunca dentro del SQL), que los nombres de configuracion se validan
y que las columnas reales se renombran a los nombres internos.
"""

from decimal import Decimal

import pytest

from asistente import config, datos, datos_databricks
from asistente.datos import ErrorDatos, Inventario, InventarioSQLite, crear_inventario
from asistente.datos_databricks import InventarioDatabricks, _nombrado


class CursorFalso:
    def __init__(self, filas, registro):
        self._filas = filas
        self._registro = registro
        self.description = []

    def execute(self, texto, parameters=None):
        self._registro.append((texto, parameters))
        self.description = [(c,) for c in self._filas[0].keys()] if self._filas else [("total",)]

    def fetchall(self):
        return [tuple(f.values()) for f in self._filas]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ConexionFalsa:
    """Devuelve siempre las mismas filas y guarda cada SQL que recibe."""

    def __init__(self, filas=None):
        self.filas = filas or []
        self.consultas = []

    def cursor(self):
        return CursorFalso(self.filas, self.consultas)


@pytest.fixture
def config_dbx(monkeypatch):
    monkeypatch.setattr(config, "DBX_HOST", "adb-test.azuredatabricks.net")
    monkeypatch.setattr(config, "DBX_HTTP_PATH", "/sql/1.0/warehouses/x")
    monkeypatch.setattr(config, "DBX_TOKEN", "token-falso")
    monkeypatch.setattr(config, "DBX_CATALOGO", "reto")
    monkeypatch.setattr(config, "DBX_ESQUEMA", "acerinox")
    monkeypatch.setattr(config, "DBX_TABLA_INVENTARIO", "inventario")
    monkeypatch.setattr(config, "DBX_TABLA_DEMANDA", "demanda")
    monkeypatch.setattr(config, "DBX_VOLUMEN_FICHAS", "/Volumes/reto/acerinox/docs/fichas")


def _inv(filas=None, ficha=None):
    conexion = ConexionFalsa(filas)
    inv = InventarioDatabricks(conexion=lambda: conexion, leer_ficha=ficha or (lambda g: None))
    return inv, conexion


def test_cumple_el_contrato(config_dbx):
    inv, _ = _inv()
    assert isinstance(inv, Inventario)
    assert isinstance(InventarioSQLite(), Inventario)


def test_los_valores_viajan_como_parametros_no_en_el_sql(config_dbx):
    inv, conexion = _inv([{"id": "ACX-00001"}])
    peligro = "Bilbao'; DROP TABLE inventario; --"
    inv.buscar(grados=["304", "316"], almacen=peligro, espesor_min=1.5)

    texto, parametros = conexion.consultas[-1]
    assert peligro not in texto and "DROP" not in texto
    assert "?" not in texto                       # los marcadores son con nombre
    assert sorted(parametros.values(), key=str) == sorted(["304", "316", peligro, 1.5], key=str)


def test_las_tablas_van_con_catalogo_y_esquema(config_dbx):
    inv, conexion = _inv([])
    inv.por_ids(["ACX-00001"])
    assert "reto.acerinox.inventario" in conexion.consultas[-1][0]


def test_columnas_reales_se_renombran_a_nombres_internos(config_dbx, monkeypatch):
    monkeypatch.setitem(datos_databricks.MAPEO_INVENTARIO, "stock_kg", "stock_kilos_real")
    inv, conexion = _inv([])
    inv.buscar()
    assert "stock_kilos_real AS stock_kg" in conexion.consultas[-1][0]


def test_nombre_de_tabla_con_caracteres_raros_se_rechaza(config_dbx, monkeypatch):
    monkeypatch.setattr(config, "DBX_TABLA_INVENTARIO", "inventario; DROP TABLE x")
    with pytest.raises(ErrorDatos):
        InventarioDatabricks(conexion=lambda: ConexionFalsa())


def test_falta_configuracion_y_no_se_intenta_conectar(monkeypatch):
    monkeypatch.setattr(config, "DBX_HOST", "")
    monkeypatch.setattr(config, "DBX_TOKEN", "")
    with pytest.raises(ErrorDatos):
        datos_databricks._conexion_real()


def test_stock_y_demanda_devuelven_numeros(config_dbx):
    inv, conexion = _inv([{"total": Decimal("1250.5")}])
    assert inv.stock_total_kg("304", "bobina") == 1250.5
    assert isinstance(inv.stock_total_kg("304", "bobina"), float)

    inv2, conexion2 = _inv([{"media": None}])
    assert inv2.demanda_media_mensual_kg("304", "bobina", meses=3) == 0.0
    assert "LIMIT 3" in conexion2.consultas[-1][0]


def test_grado_con_ruta_no_llega_al_volumen(config_dbx):
    inv, _ = _inv(ficha=lambda g: "no deberia llamarse")
    with pytest.raises(ValueError):
        inv.ficha("../secretos")
    with pytest.raises(ValueError):
        inv.ficha("304/../../x")


def test_ficha_inexistente_devuelve_none(config_dbx):
    inv, _ = _inv(ficha=lambda g: None)
    assert inv.ficha("304") is None


def test_nombrado_exige_mismo_numero_de_valores():
    assert _nombrado("a = ? AND b IN (?,?)", [1, 2, 3]) == ("a = :p0 AND b IN (:p1,:p2)", {"p0": 1, "p1": 2, "p2": 3})
    with pytest.raises(ValueError):
        _nombrado("a = ?", [1, 2])


def test_crear_inventario_elige_el_origen_por_configuracion(config_dbx, monkeypatch):
    monkeypatch.setattr(config, "ORIGEN_DATOS", "databricks")
    assert isinstance(crear_inventario(), InventarioDatabricks)

    monkeypatch.setattr(config, "ORIGEN_DATOS", "sqlite")
    assert isinstance(crear_inventario(), InventarioSQLite)

    monkeypatch.setattr(config, "ORIGEN_DATOS", "oracle")
    with pytest.raises(ErrorDatos):
        crear_inventario()
