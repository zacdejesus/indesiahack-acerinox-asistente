"""Tests del backend. No llaman a ningun LLM: el modelo se sustituye por uno guionizado.

Uso:  python -m pytest
"""

import pytest

from asistente import dominio, herramientas, roles
from asistente.agente import Asistente, Conversacion
from asistente.datos import InventarioSQLite
from asistente.proveedor import Llamada, RespuestaLLM
from asistente.validador import ids_en_resultado, validar

# Productos fijos del escenario de demo (ver ESCENARIOS_DEMO en generar_datos.py)
AGOTADO_316_MADRID = "ACX-00495"
ALT_316L_MADRID = "ACX-00496"
ALT_316_BARCELONA = "ACX-00497"
LENTO_430_VALENCIA = "ACX-00499"


@pytest.fixture(scope="module")
def inv():
    return InventarioSQLite()


class ModeloGuionizado:
    """Proveedor falso: devuelve las respuestas del guion, una por llamada."""

    def __init__(self, *guion: RespuestaLLM):
        self._guion = list(guion)
        self.mensajes_recibidos = []

    def completar(self, sistema, mensajes, herramientas):
        self.mensajes_recibidos.append(list(mensajes))
        return self._guion.pop(0)


def pide(nombre, **argumentos) -> RespuestaLLM:
    return RespuestaLLM(texto="", llamadas=[Llamada("t1", nombre, argumentos)], fin="herramientas")


def dice(texto) -> RespuestaLLM:
    return RespuestaLLM(texto=texto)


BUSCAR_316_MADRID = dict(grado="316", formato="chapa", acabado="2B", espesor_min=2, espesor_max=2, almacen="Madrid")


# --- Dominio --------------------------------------------------------------------

@pytest.mark.parametrize("texto, grado", [
    ("316L", "316L"), ("aisi 316l", "316L"), ("1.4404", "316L"), ("EN 1.4301", "304"),
    ("S31603", "316L"), ("AISI-304", "304"), ("duplex 2205", "2205"), ("317", None), ("acero", None),
    ("316/316L", "316L"), ("304/304L", "304L"), ("1.4404 (316L)", "316L"), ("W.Nr. 1.4541", "321"),
    ("dúplex 2205", "2205"), ("AISI 310", "310S"), ("304/316", None),
])
def test_normalizar_grado(texto, grado):
    assert dominio.normalizar_grado(texto) == grado


def test_sustitutos_existen_y_nunca_bajan_de_grado():
    assert all(s in dominio.SUSTITUTOS for alts in dominio.SUSTITUTOS.values() for s, _ in alts)
    assert "304" not in [s for s, _ in dominio.SUSTITUTOS["316L"]]


# --- Herramientas ---------------------------------------------------------------

def test_buscar_sin_stock_avisa(inv):
    r, error = herramientas.ejecutar(inv, "buscar_stock", BUSCAR_316_MADRID)
    assert not error and r["productos"] == [] and "alternativas" in r["aviso"]


def test_buscar_solo_devuelve_productos_con_stock_y_del_filtro(inv):
    r, _ = herramientas.ejecutar(inv, "buscar_stock", {"grado": "1.4404", "formato": "sheet"})
    assert r["productos"]
    assert all(p["grado"] == "316L" and p["formato"] == "chapa" and p["stock_kg"] > 0 for p in r["productos"])


def test_buscar_no_expone_el_margen(inv):
    r, _ = herramientas.ejecutar(inv, "buscar_stock", {"grado": "304"})
    assert all("margen_pct" not in p for p in r["productos"])


def test_ajuste_penaliza_corte_y_stock_parcial(inv):
    r, _ = herramientas.ejecutar(inv, "buscar_stock", dict(
        grado="304", formato="bobina", espesor_min=1, espesor_max=1, ancho_min=1250, cantidad_kg=60000))
    p = r["productos"][0]
    assert p["ajuste"] < 100
    assert any("corte" in n for n in p["notas"]) and any("parcial" in n for n in p["notas"])


def test_barra_exige_diametro_exacto(inv):
    r, _ = herramientas.ejecutar(inv, "buscar_stock", {"formato": "barra", "ancho_min": 20})
    assert all(p["ancho_mm"] == 20 for p in r["productos"])


def test_alternativas_del_escenario_demo(inv):
    r, error = herramientas.ejecutar(inv, "alternativas", {"producto_id": AGOTADO_316_MADRID})
    assert not error
    tipos = {a["id"]: a["tipo"] for a in r["alternativas"]}
    assert tipos[ALT_316_BARCELONA] == "otro_almacen"
    assert tipos[ALT_316L_MADRID] == "grado_equivalente"
    assert all(a["stock_kg"] > 0 for a in r["alternativas"])


def test_prioridad_pone_primero_el_stock_parado(inv):
    r, _ = herramientas.ejecutar(inv, "buscar_stock", {"grado": "430", "formato": "chapa", "acabado": "BA"})
    ids = [p["id"] for p in r["productos"]]
    r, _ = herramientas.ejecutar(inv, "prioridad_comercial", {"lista_ids": ids + ["ACX-99999"]})
    assert r["productos"][0]["id"] == LENTO_430_VALENCIA
    assert r["ids_desconocidos"] == ["ACX-99999"]


@pytest.mark.parametrize("nombre, argumentos", [
    ("buscar_stock", {"grado": "317"}),          # grado que no existe
    ("buscar_stock", {"grosor": 3}),             # argumento inventado
    ("buscar_stock", {"espesor_min": "fino"}),   # tipo incorrecto
    ("ficha_tecnica", {}),                       # falta un obligatorio
    ("borrar_stock", {}),                        # herramienta inventada
])
def test_los_errores_del_modelo_vuelven_como_mensaje(inv, nombre, argumentos):
    r, error = herramientas.ejecutar(inv, nombre, argumentos)
    assert error and r["error"]


def test_inventario_es_de_solo_lectura(inv):
    with pytest.raises(Exception, match="readonly"):
        inv._con.execute("DELETE FROM inventario")


# --- Validador ------------------------------------------------------------------

def test_validador_distingue_inventado_y_sin_evidencia(inv):
    v = validar(f"Te ofrezco {ALT_316L_MADRID}, {ALT_316_BARCELONA} y ACX-99999.", {ALT_316L_MADRID}, inv)
    assert v.citados == [ALT_316L_MADRID, ALT_316_BARCELONA, "ACX-99999"]
    assert v.inventados == ["ACX-99999"] and v.sin_evidencia == [ALT_316_BARCELONA] and not v.ok


def test_ids_en_resultado_recorre_estructuras_anidadas():
    assert ids_en_resultado({"productos": [{"id": "A"}, {"x": [{"id": "B"}]}], "ids_desconocidos": ["C"]}) == {"A", "B"}


# --- Agente (con modelo guionizado) ---------------------------------------------

def test_flujo_completo_sin_stock_y_alternativa(inv):
    modelo = ModeloGuionizado(
        pide("buscar_stock", **BUSCAR_316_MADRID),
        pide("alternativas", **{k: v for k, v in BUSCAR_316_MADRID.items() if not k.startswith("espesor")},
             espesor_mm=2),
        dice(f"No hay 316 en Madrid. Te propongo {ALT_316L_MADRID} (316L, Madrid)."),
    )
    r = Asistente(modelo, inv).responder("Chapa 316 2B de 2 mm en Madrid")

    assert r.verificada and r.llamadas_llm == 3
    assert [p.herramienta for p in r.traza] == ["buscar_stock", "alternativas"]
    assert [p["id"] for p in r.productos] == [ALT_316L_MADRID]
    assert r.productos[0]["almacen"] == "Madrid"  # la tabla sale de la base de datos, no del texto
    # El modelo recibio el resultado de la herramienta en la vuelta siguiente
    assert modelo.mensajes_recibidos[1][-1]["rol"] == "herramientas"


def test_id_inventado_se_corrige_al_segundo_intento(inv):
    modelo = ModeloGuionizado(
        pide("alternativas", producto_id=AGOTADO_316_MADRID),
        dice("Te recomiendo ACX-99999."),
        dice(f"Te recomiendo {ALT_316L_MADRID}."),
    )
    r = Asistente(modelo, inv).responder("Chapa 316 en Madrid")
    assert r.verificada and r.validacion.citados == [ALT_316L_MADRID]
    assert r.reintentada and r.rechazados == ["ACX-99999"]
    assert "Verificador" in modelo.mensajes_recibidos[2][-1]["texto"]


def test_si_insiste_en_inventar_se_sustituye_el_texto(inv):
    modelo = ModeloGuionizado(
        pide("alternativas", producto_id=AGOTADO_316_MADRID),
        dice("Te recomiendo ACX-99999."),
        dice("Insisto: ACX-99999."),
    )
    r = Asistente(modelo, inv).responder("Chapa 316 en Madrid")
    assert not r.verificada
    assert "ACX-99999" not in r.texto and ALT_316_BARCELONA in r.texto
    assert r.validacion.ok  # el texto sustituto solo cita productos reales
    assert r.rechazados == ["ACX-99999"]  # se conserva lo que cito el modelo, para enseñarlo


def test_no_puede_citar_un_producto_real_que_no_ha_buscado(inv):
    modelo = ModeloGuionizado(dice(f"Te recomiendo {LENTO_430_VALENCIA}."), dice(f"Sigo: {LENTO_430_VALENCIA}."))
    r = Asistente(modelo, inv).responder("Algo de 430")
    assert not r.verificada and r.productos == []


def test_pregunta_de_aclaracion_no_llama_herramientas(inv):
    r = Asistente(ModeloGuionizado(dice("¿Para qué aplicación lo necesitas?")), inv).responder("Necesito acero")
    assert r.verificada and r.traza == [] and r.productos == []


def test_bucle_infinito_de_herramientas_se_corta(inv):
    modelo = ModeloGuionizado(*[pide("buscar_stock", grado="304") for _ in range(3)])
    r = Asistente(modelo, inv, max_pasos=3).responder("304")
    assert not r.verificada and r.llamadas_llm == 3


def test_la_conversacion_conserva_historial_y_evidencia(inv):
    conv = Conversacion()
    modelo = ModeloGuionizado(
        pide("alternativas", producto_id=AGOTADO_316_MADRID),
        dice(f"Te propongo {ALT_316L_MADRID}."),
        dice(f"Sí, también está {ALT_316_BARCELONA} en Barcelona."),  # segunda pregunta, sin volver a buscar
    )
    asistente = Asistente(modelo, inv)
    asistente.responder("Chapa 316 en Madrid", conv)
    r = asistente.responder("¿Y en otro almacén?", conv)
    assert r.verificada and [p["id"] for p in r.productos] == [ALT_316_BARCELONA]
    assert [m["rol"] for m in conv.mensajes] == ["usuario", "asistente", "herramientas", "asistente", "usuario", "asistente"]


def test_si_el_proveedor_falla_la_conversacion_queda_como_estaba(inv):
    from asistente.proveedor import ErrorProveedor

    class ModeloCaido:
        def completar(self, sistema, mensajes, herramientas):
            raise ErrorProveedor("sin red")

    conv = Conversacion()
    with pytest.raises(ErrorProveedor):
        Asistente(ModeloCaido(), inv).responder("Chapa 316", conv)
    assert conv.mensajes == [] and conv.evidencia == set()


@pytest.mark.parametrize("texto, esperado", [("No. 4", "N4"), ("bright", "BA"), ("Satinado", "N4"), ("rugoso", None)])
def test_normalizar_acabado(texto, esperado):
    assert dominio.normalizar_acabado(texto) == esperado


@pytest.mark.parametrize("texto, esperado", [("round bar", "barra"), ("Tubería", "tubo"), ("Sheets", "chapa")])
def test_normalizar_formato(texto, esperado):
    assert dominio.normalizar_formato(texto) == esperado


def test_alternativas_en_barra_no_propone_otro_diametro(inv):
    r, _ = herramientas.ejecutar(inv, "alternativas", {"grado": "316", "formato": "barra", "espesor_mm": 30})
    assert all(a["tipo"] != "espesor_cercano" for a in r["alternativas"])


def test_un_id_mas_largo_no_se_confunde_con_uno_real(inv):
    assert validar("Mira ACX-004990.", {LENTO_430_VALENCIA}, inv).citados == []


# --- Roles ------------------------------------------------------------------------
# Lo que el cliente no puede ver no llega ni al modelo: se filtra en el nucleo.

INTERNOS = {"margen_pct", "rotacion_dias", "precio_eur_kg", "prioridad", "motivos", "valor_stock_eur",
            "cobertura_meses"}


def _claves(valor) -> set[str]:
    if isinstance(valor, dict):
        return set(valor) | set().union(*map(_claves, valor.values()))
    if isinstance(valor, list):
        return set().union(*map(_claves, valor))
    return set()


class ModeloQueApunta(ModeloGuionizado):
    """Como el guionizado, pero guarda tambien el prompt y las herramientas que recibe."""

    def completar(self, sistema, mensajes, herramientas):
        self.sistemas = getattr(self, "sistemas", []) + [sistema]
        self.herramientas = [h["nombre"] for h in herramientas]
        return super().completar(sistema, mensajes, herramientas)


def test_el_cliente_no_tiene_prioridad_comercial():
    assert "prioridad_comercial" not in [h["nombre"] for h in herramientas.esquemas(roles.CLIENTE)]
    assert "prioridad_comercial" in [h["nombre"] for h in herramientas.esquemas(roles.COMERCIAL)]


def test_herramienta_vetada_responde_como_inexistente(inv):
    r, error = herramientas.ejecutar(inv, "prioridad_comercial", {"lista_ids": [LENTO_430_VALENCIA]}, roles.CLIENTE)
    assert error and "No existe" in r["error"] and "prioridad_comercial" not in r["error"].split("Disponibles:")[1]


def test_resultados_para_el_cliente_sin_datos_internos(inv):
    for nombre, argumentos in [("buscar_stock", {"grado": "304"}), ("alternativas", {"producto_id": AGOTADO_316_MADRID})]:
        r, error = herramientas.ejecutar(inv, nombre, argumentos, roles.CLIENTE)
        assert not error and not _claves(r) & INTERNOS
        # El comercial sigue viendo precio y rotacion (pero nunca el margen).
        r, _ = herramientas.ejecutar(inv, nombre, argumentos)
        assert {"precio_eur_kg", "rotacion_dias"} <= _claves(r) and "margen_pct" not in _claves(r)


def test_asistente_del_cliente_usa_su_prompt_herramientas_y_filtro(inv):
    modelo = ModeloQueApunta(
        pide("buscar_stock", grado="430", formato="chapa", acabado="BA"),
        pide("prioridad_comercial", lista_ids=[LENTO_430_VALENCIA]),
        dice(f"Hay stock: {LENTO_430_VALENCIA} en Valencia."))
    r = Asistente(modelo, inv, rol=roles.CLIENTE).responder("¿Tenéis 430 BA?")
    assert modelo.sistemas[0] == roles.CLIENTE.sistema
    assert "prioridad_comercial" not in modelo.herramientas
    assert r.traza[1].error   # el intento de usar la herramienta interna falla
    # Ni el modelo ni la respuesta reciben datos internos.
    assert not _claves(modelo.mensajes_recibidos) & INTERNOS
    assert r.productos and not _claves(r.productos) & INTERNOS


def test_con_rol_conserva_modelo_y_datos(inv):
    base = Asistente(ModeloGuionizado(dice("Hola")), inv)
    cliente = base.con_rol(roles.CLIENTE)
    assert base.rol is roles.COMERCIAL and cliente.rol is roles.CLIENTE and cliente.inventario is inv
