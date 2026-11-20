"""Tests de la interfaz. No llaman a ningun LLM: la app recibe un asistente con el modelo guionizado.

Uso:  python -m pytest
"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from asistente.agente import Asistente
from asistente.datos import InventarioSQLite
from asistente.proveedor import ErrorProveedor
from test_asistente import (AGOTADO_316_MADRID, ALT_316_BARCELONA, ALT_316L_MADRID, BUSCAR_316_MADRID,
                            ModeloGuionizado, dice, pide)

APP = str(Path(__file__).resolve().parent.parent / "app.py")
EJEMPLO_316_MADRID = "ejemplo_0"


class ModeloSinClave:
    """Proveedor falso que falla como el real cuando no hay clave de API."""

    def completar(self, sistema, mensajes, herramientas):
        raise ErrorProveedor("Clave de API no válida. Revisa ANTHROPIC_API_KEY en .env.")


def abrir(modelo) -> AppTest:
    app = AppTest.from_file(APP, default_timeout=30)
    app.session_state["asistente"] = Asistente(modelo, InventarioSQLite())
    return app.run()


def guion_316_madrid(*mas):
    return ModeloGuionizado(
        pide("buscar_stock", **BUSCAR_316_MADRID),
        pide("alternativas", producto_id=AGOTADO_316_MADRID),
        dice(f"No hay 316 en Madrid. Te propongo {ALT_316L_MADRID} (316L, Madrid)."),
        *mas)


def textos(elementos) -> str:
    return "\n".join(e.value for e in elementos)


def test_arranca_sin_excepciones_y_sin_mensajes():
    app = abrir(ModeloGuionizado())
    assert not app.exception and not app.error
    assert len(app.chat_message) == 0 and len(app.dataframe) == 0
    assert len([b for b in app.button if b.key.startswith("ejemplo_")]) >= 5


def test_un_ejemplo_produce_respuesta_con_traza_tabla_y_verificador():
    app = abrir(guion_316_madrid())
    app.button(key=EJEMPLO_316_MADRID).click().run()
    assert not app.exception and not app.error

    # Chat: la pregunta del ejemplo y la respuesta
    assert [m.name for m in app.chat_message] == ["user", "assistant"]
    assert "chapa 316 2B de 2 mm" in app.chat_message[0].markdown[0].value
    assert ALT_316L_MADRID in app.chat_message[1].markdown[0].value

    # Tabla: sale de Respuesta.productos y no enseña el margen
    tabla = app.dataframe[0].value
    assert list(tabla["Id"]) == [ALT_316L_MADRID] and tabla["Almacén"][0] == "Madrid"
    assert not any("margen" in c.lower() for c in tabla.columns)

    # Traza: los dos pasos, con sus filtros, y el detalle de la alternativa junto al producto
    panel = textos(app.markdown)
    assert "`buscar_stock`" in panel and "`alternativas`" in panel
    assert "grado: **316**" in panel and "almacen: **Madrid**" in panel
    assert "0 productos con stock" in panel
    assert "Alternativa (grado equivalente o superior): 316L en lugar de 316" in panel

    # Verificador y metricas
    assert "Respuesta verificada" in app.success[0].value and ALT_316L_MADRID in app.success[0].value
    assert "3 llamadas al modelo" in textos(app.caption)


def test_una_segunda_pregunta_conserva_el_historial():
    app = abrir(guion_316_madrid(dice(f"Sí, también está {ALT_316_BARCELONA} en Barcelona.")))
    app.button(key=EJEMPLO_316_MADRID).click().run()
    app.chat_input[0].set_value("¿Y en otro almacén?").run()
    assert not app.exception and not app.error

    assert [m.name for m in app.chat_message] == ["user", "assistant", "user", "assistant"]
    assert ALT_316L_MADRID in app.chat_message[1].markdown[0].value
    assert ALT_316_BARCELONA in app.chat_message[3].markdown[0].value
    # El panel enseña la ultima respuesta; el detalle del producto viene de la busqueda del turno anterior
    assert list(app.dataframe[0].value["Id"]) == [ALT_316_BARCELONA]
    assert "Alternativa (otro almacén)" in textos(app.markdown)
    assert len(app.session_state["conversacion"].mensajes) == 8


def test_texto_sustituido_se_avisa():
    modelo = ModeloGuionizado(pide("alternativas", producto_id=AGOTADO_316_MADRID),
                              dice("Te recomiendo ACX-99999."), dice("Insisto: ACX-99999."))
    app = abrir(modelo)
    app.chat_input[0].set_value("Chapa 316 en Madrid").run()
    assert not app.exception
    assert "Texto sustituido" in app.warning[0].value
    assert "ACX-99999" not in app.chat_message[1].markdown[0].value


def test_error_de_herramienta_se_ve_en_la_traza():
    app = abrir(ModeloGuionizado(pide("buscar_stock", grado="317"), dice("No reconozco ese grado.")))
    app.chat_input[0].set_value("Chapa 317").run()
    assert not app.exception and not app.error
    assert "Grado no reconocido" in app.warning[0].value


def test_error_del_proveedor_se_muestra_y_la_app_sigue_viva():
    app = abrir(ModeloSinClave())
    app.chat_input[0].set_value("Chapa 304").run()
    assert not app.exception
    assert "ANTHROPIC_API_KEY" in app.error[0].value
    # La pregunta fallida no se queda en la conversacion que se envia al modelo
    assert app.session_state["conversacion"].mensajes == []

    # La app sigue viva: con un proveedor que funciona, la siguiente pregunta se responde
    app.session_state["asistente"] = Asistente(ModeloGuionizado(dice("¿Para qué aplicación?")), InventarioSQLite())
    app.chat_input[0].set_value("Necesito acero").run()
    assert not app.exception
    assert app.chat_message[-1].markdown[0].value == "¿Para qué aplicación?"


def test_nueva_conversacion_vacia_el_chat():
    app = abrir(guion_316_madrid())
    app.button(key=EJEMPLO_316_MADRID).click().run()
    app.button(key="nueva").click().run()
    assert not app.exception
    assert len(app.chat_message) == 0 and len(app.dataframe) == 0
    assert app.session_state["conversacion"].mensajes == []
