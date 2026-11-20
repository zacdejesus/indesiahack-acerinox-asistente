"""Tests de dos comprobaciones que fallaban por error nuestro, no del modelo:
  1. Un id mal escrito (ACX-0039, con cuatro digitos) tiene que contar como inventado.
  2. Una pregunta de aclaracion sin signo de interrogacion tiene que contar como pregunta.

Uso:  python -m pytest tests/test_comprobaciones.py
"""

import pytest

from asistente import validador
from asistente.datos import InventarioSQLite
from eval import evaluar


@pytest.fixture(scope="module")
def inv():
    return InventarioSQLite()


def test_id_con_cuatro_digitos_se_detecta_y_cuenta_como_inventado(inv):
    texto = "Te recomiendo ACX-0039 en Madrid, con 69.160 kg."
    assert validador.ids_en_texto(texto) == ["ACX-0039"]
    v = validador.validar(texto, set(), inv)
    assert v.inventados == ["ACX-0039"]
    assert not v.ok


def test_id_real_de_cinco_digitos_sigue_siendo_uno_solo():
    # Con {4,5} y limites de palabra, un id de cinco digitos no se corta en dos.
    assert validador.ids_en_texto("Producto ACX-00496, almacén Madrid.") == ["ACX-00496"]


def test_pide_aclaracion_con_interrogacion_en_cualquier_linea():
    assert evaluar.pide_aclaracion("Para afinar la búsqueda:\n¿Qué grado o para qué aplicación lo necesita?")


def test_pide_aclaracion_sin_interrogacion_pero_pidiendo_datos():
    # El texto real que el modelo dio en amb-01: pide datos en una frase y cierra sin "?".
    texto = ("Para poder ayudarte, necesito que me proporciones más detalles sobre el pedido. "
             "Por favor, indícame el grado de acero, el formato y la cantidad necesaria.")
    assert evaluar.pide_aclaracion(texto)


def test_una_recomendacion_no_cuenta_como_aclaracion():
    assert not evaluar.pide_aclaracion("Te recomiendo ACX-00496 en Madrid, con 4.308 kg disponibles.")
