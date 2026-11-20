"""Tests de la evaluacion. No llaman a ningun LLM: el modelo se sustituye por uno guionizado.

Comprueban dos cosas: que las comprobaciones aciertan y fallan cuando deben, y
que eval/preguntas.yaml es coherente con el inventario (lo que espera cada caso
se puede cumplir con los datos que hay).

Uso:  python -m pytest
"""

import json

import pytest

from asistente import herramientas
from asistente.agente import Asistente
from asistente.datos import InventarioSQLite
from eval import evaluar
from test_asistente import (AGOTADO_316_MADRID, ALT_316L_MADRID, BUSCAR_316_MADRID, LENTO_430_VALENCIA,
                            ModeloGuionizado, dice, pide)

CHAPA_304_MADRID = "ACX-00472"  # 304 2B de 2 mm: un grado inferior al 316 que pide el caso sin-01


@pytest.fixture(scope="module")
def inv():
    return InventarioSQLite()


@pytest.fixture(scope="module")
def casos():
    return {c["id"]: c for c in evaluar.cargar_casos()}


@pytest.fixture(scope="module")
def con_stock(inv):
    return [p for p in inv.buscar() if p["stock_kg"] > 0]


def responder(inv, caso, *guion):
    return Asistente(ModeloGuionizado(*guion), inv).responder(caso["pregunta"])


def condiciones(espera: dict) -> list[dict]:
    """Todas las condiciones sobre productos que usa un caso."""
    todos = espera.get("todos", [])
    return (todos if isinstance(todos, list) else [todos]) + espera.get("alguno", []) + \
        ([espera["primero"]] if "primero" in espera else [])


def candidatos(espera: dict, productos: list[dict]) -> list[dict]:
    """Productos que la comprobacion `todos` del caso deja citar."""
    todos = espera.get("todos", {})
    todos = todos if isinstance(todos, list) else [todos]
    return [p for p in productos if any(evaluar.cumple(p, c) for c in todos)]


# --- Comprobaciones ---------------------------------------------------------------

def test_condicion_valor_lista_y_rango():
    producto = {"grado": "304", "formato": "chapa", "espesor_mm": 3.0, "almacen": "Madrid"}
    assert evaluar.cumple(producto, {"grado": "304", "formato": ["chapa", "bobina"]})
    assert evaluar.cumple(producto, {"grado": 304})  # un grado sin comillas en el YAML llega como numero
    assert evaluar.cumple(producto, {"espesor_mm": {"min": 3, "max": 3.2}})
    assert not evaluar.cumple(producto, {"espesor_mm": {"min": 3.1}})
    assert not evaluar.cumple(producto, {"grado": ["316", "316L"]})
    assert not evaluar.cumple(producto, {"grado": "304", "almacen": "Bilbao"})


def test_caso_bien_resuelto_pasa(inv, casos):
    caso = casos["sin-01"]
    r = responder(
        inv, caso,
        pide("buscar_stock", **BUSCAR_316_MADRID),
        pide("alternativas", producto_id=AGOTADO_316_MADRID),
        dice(f"No hay 316 en Madrid. Te propongo {ALT_316L_MADRID}: 316L, 4.308 kg en Madrid."))
    assert evaluar.comprobar(caso["espera"], r) == []


def test_caso_mal_resuelto_falla_con_motivo_legible(inv, casos):
    # El modelo baja de grado (ofrece 304 a quien pidio 316) y no busca alternativas.
    caso = casos["sin-01"]
    r = responder(
        inv, caso,
        pide("buscar_stock", grado="304", formato="chapa", espesor_min=2, espesor_max=2),
        dice(f"Te propongo {CHAPA_304_MADRID}."))
    motivos = evaluar.comprobar(caso["espera"], r)
    assert len(motivos) == 3
    texto = "\n".join(motivos)
    assert f"todos: cita productos que no debía: {CHAPA_304_MADRID} (304 chapa 2B" in texto
    assert "alguno: ningún producto citado cumple {grado = 316 o 316L" in texto
    assert "herramientas: no llamó a alternativas (llamó a: buscar_stock)" in texto


def test_respuesta_sustituida_por_el_verificador_falla(inv, casos):
    caso = casos["sin-01"]
    r = responder(
        inv, caso,
        pide("buscar_stock", **BUSCAR_316_MADRID),
        pide("alternativas", producto_id=AGOTADO_316_MADRID),
        dice("Te recomiendo ACX-99999."), dice("Insisto: ACX-99999."))
    assert evaluar.comprobar(caso["espera"], r) == [
        "verificada: el verificador sustituyó la respuesta del modelo por el texto seguro"]


def test_pregunta_ambigua(inv, casos):
    caso = casos["amb-01"]
    r = responder(inv, caso, dice("Para afinar la búsqueda:\n¿Qué grado o para qué aplicación lo necesita?"))
    assert evaluar.comprobar(caso["espera"], r) == []

    # Recomendar en vez de preguntar falla por las dos comprobaciones.
    r = responder(inv, caso, pide("buscar_stock", grado="304", formato="chapa", acabado="2B", espesor_min=2, espesor_max=2),
                  dice(f"Te ofrezco {CHAPA_304_MADRID}."))
    motivos = evaluar.comprobar(caso["espera"], r)
    assert [m.split(":")[0] for m in motivos] == ["num_productos", "termina_en_pregunta"]
    assert CHAPA_304_MADRID in motivos[0]


def test_primero_y_stock_total(inv, casos):
    buscar = pide("buscar_stock", grado="430", formato="chapa", acabado="BA", espesor_min=0.6, espesor_max=0.6)
    prioridad = pide("prioridad_comercial", lista_ids=[LENTO_430_VALENCIA, "ACX-00042"])
    caso = casos["pri-01"]
    r = responder(inv, caso, buscar, prioridad, dice(f"Primero {LENTO_430_VALENCIA}; después ACX-00042."))
    assert evaluar.comprobar(caso["espera"], r) == []
    r = responder(inv, caso, buscar, prioridad, dice(f"Primero ACX-00042; después {LENTO_430_VALENCIA}."))
    assert evaluar.comprobar(caso["espera"], r) == [
        "primero: el primer producto citado es ACX-00042 (430 chapa BA, 0.6 x 1000 mm, 443.2 kg, Madrid) "
        "y debía cumplir {id = ACX-00499}"]

    # Una sola bobina de 41 t no cubre las 80 t del caso can-01.
    caso = casos["can-01"]
    r = responder(inv, caso, pide("buscar_stock", grado="304", formato="bobina", acabado="BA"),
                  dice("Te ofrezco ACX-00365."))
    motivos = evaluar.comprobar(caso["espera"], r)
    assert any("suman 41640 kg y hacían falta 80000 kg" in m for m in motivos)


def test_detectar_idioma():
    assert evaluar.detectar_idioma("Te recomiendo la chapa ACX-00496: hay 4.308 kg en el almacén de Madrid.") == "es"
    assert evaluar.detectar_idioma("I recommend ACX-00496: we have 4,308 kg of it in the Madrid warehouse.") == "en"


# --- El YAML ----------------------------------------------------------------------

def test_el_yaml_se_carga_y_solo_usa_comprobaciones_que_existen(casos):
    assert len(casos) >= 30
    for caso in casos.values():
        assert caso["pregunta"] and caso["categoria"]
        assert set(caso["espera"]) <= set(evaluar.COMPROBACIONES)
        assert caso["espera"]["verificada"] is True  # viene de por_defecto
        assert len(caso["espera"]) > 1, f"{caso['id']} no comprueba nada propio"


def test_el_yaml_cubre_todas_las_categorias_pedidas(casos):
    por_categoria = {}
    for caso in casos.values():
        por_categoria[caso["categoria"]] = por_categoria.get(caso["categoria"], 0) + 1
    assert set(por_categoria) == {"directa", "sin_stock", "aplicacion", "designacion", "unidades", "imposible",
                                  "ambigua", "ingles", "cantidad", "prioridad", "correo"}
    assert por_categoria["correo"] >= 4


def test_cargar_rechaza_una_comprobacion_que_no_existe(tmp_path):
    ruta = tmp_path / "preguntas.yaml"
    ruta.write_text("casos:\n  - {id: x, categoria: directa, pregunta: hola, espera: {grado_correcto: 304}}\n")
    with pytest.raises(ValueError, match="grado_correcto"):
        evaluar.cargar_casos(ruta)


def test_las_condiciones_usan_valores_que_existen_en_el_inventario(inv, casos, con_stock):
    columnas = set(con_stock[0])
    catalogo = {c: set(inv.valores(c)) for c in ("grado", "formato", "acabado", "almacen")}
    for caso in casos.values():
        for condicion in condiciones(caso["espera"]):
            for campo, esperado in condicion.items():
                assert campo in columnas, f"{caso['id']}: {campo} no es una columna del inventario"
                if isinstance(esperado, dict):
                    assert set(esperado) <= {"min", "max"}, f"{caso['id']}: rango mal escrito en {campo}"
                elif campo in catalogo:
                    valores = esperado if isinstance(esperado, list) else [esperado]
                    assert {str(v) for v in valores} <= catalogo[campo], f"{caso['id']}: {campo} = {esperado}"
        for nombre in caso["espera"].get("herramientas", []):
            assert nombre in {h["nombre"] for h in herramientas.REGISTRO}, f"{caso['id']}: {nombre}"
        assert caso["espera"].get("idioma", "es") in ("es", "en")


def test_lo_esperado_se_puede_cumplir_con_el_inventario(casos, con_stock):
    """Para cada caso existe una respuesta correcta: productos con stock que cumplen lo que se pide."""
    for caso in casos.values():
        espera = caso["espera"]
        validos = candidatos(espera, con_stock)
        for condicion in espera.get("alguno", []) + ([espera["primero"]] if "primero" in espera else []):
            assert any(evaluar.cumple(p, condicion) for p in validos), \
                f"{caso['id']}: ningún producto con stock cumple {condicion}"
        if "stock_total_min_kg" in espera:
            assert sum(p["stock_kg"] for p in validos) >= espera["stock_total_min_kg"], caso["id"]
        if espera.get("num_productos", {}).get("min"):
            assert len(validos) >= espera["num_productos"]["min"], caso["id"]


def test_los_casos_que_no_deben_recomendar_no_esperan_productos(casos):
    for caso in casos.values():
        if caso["categoria"] in ("imposible", "ambigua"):
            assert caso["espera"]["num_productos"] == {"max": 0}, caso["id"]
            assert not condiciones(caso["espera"]), caso["id"]
        if caso["categoria"] == "ambigua":
            assert caso["espera"]["termina_en_pregunta"] is True, caso["id"]


def test_cantidad_ningun_producto_cubre_el_pedido_por_si_solo(casos, con_stock):
    for caso in casos.values():
        if caso["categoria"] == "cantidad":
            pedido = caso["espera"]["stock_total_min_kg"]
            assert all(p["stock_kg"] < pedido for p in candidatos(caso["espera"], con_stock)), caso["id"]


def test_prioridad_el_primero_esperado_es_el_que_da_la_herramienta(inv, casos, con_stock):
    for caso in casos.values():
        if caso["categoria"] == "prioridad":
            ids = [p["id"] for p in candidatos(caso["espera"], con_stock)]
            assert len(ids) >= 3, caso["id"]
            r, _ = herramientas.ejecutar(inv, "prioridad_comercial", {"lista_ids": ids})
            assert r["productos"][0]["id"] == caso["espera"]["primero"]["id"], caso["id"]


def test_sin_stock_la_busqueda_literal_no_devuelve_nada(inv):
    """Los casos sin_stock lo son de verdad: lo que se pide al pie de la letra no tiene stock."""
    literales = {
        "sin-01": BUSCAR_316_MADRID,
        "sin-02": dict(grado="310S", formato="chapa", espesor_min=12, espesor_max=12, almacen="Barcelona"),
        "sin-03": dict(grado="2205", formato="bobina", acabado="2B", espesor_min=1.5, espesor_max=1.5),
        "sin-04": dict(grado="2205", formato="tubo", ancho_min=60.3),
    }
    for caso_id, filtros in literales.items():
        r, error = herramientas.ejecutar(inv, "buscar_stock", filtros)
        assert not error and r["productos"] == [], caso_id
    # Y el sin-04 tampoco tiene alternativas: lo correcto es no ofrecer nada.
    r, _ = herramientas.ejecutar(inv, "alternativas", dict(grado="2205", formato="tubo", espesor_mm=2.77, ancho_min=60.3))
    assert r["alternativas"] == []


def test_imposibles_el_inventario_no_tiene_nada_parecido(inv):
    for filtros in (dict(grado="304", formato="chapa", espesor_min=500),
                    dict(grado="304", formato="bobina", ancho_min=4000)):
        r, error = herramientas.ejecutar(inv, "buscar_stock", filtros)
        assert not error and r["productos"] == []
    for grado in ("420", "5754"):
        assert herramientas.ejecutar(inv, "buscar_stock", {"grado": grado})[1]


# --- Ejecucion y metricas ---------------------------------------------------------

def test_ejecutar_caso_mide_los_ids_inventados_aunque_se_corrijan(inv, casos):
    modelo = ModeloGuionizado(
        pide("buscar_stock", **BUSCAR_316_MADRID),
        pide("alternativas", producto_id=AGOTADO_316_MADRID),
        dice("Te recomiendo ACX-99999."),
        dice(f"Te recomiendo {ALT_316L_MADRID}."))
    r = evaluar.ejecutar_caso(Asistente(modelo, inv), inv, casos["sin-01"])
    assert r["aprobado"] and r["verificada"] and r["citados"] == [ALT_316L_MADRID]
    assert r["ids_inventados"] == ["ACX-99999"] and r["inventados_en_respuesta"] == []
    assert r["reintentada"] and r["ids_sin_evidencia"] == []
    assert r["ids_escritos"] == [ALT_316L_MADRID, "ACX-99999"]
    assert [p["herramienta"] for p in r["traza"]] == ["buscar_stock", "alternativas"]
    json.dumps(r)  # el resultado se puede guardar tal cual


def resultado(**cambios) -> dict:
    base = dict(id="x", categoria="directa", aprobado=True, motivos=[], error=None, verificada=True,
                ids_escritos=["A", "B"], ids_inventados=[], ids_sin_evidencia=[], inventados_en_respuesta=[], reintentada=False,
                llamadas_llm=3, tokens_entrada=10_000, tokens_salida=1_000, ms=1000, pregunta="p")
    return base | cambios


def test_resumir_calcula_las_metricas():
    resultados = [resultado(ms=m) for m in range(1000, 9000, 1000)]                     # 8 aprobados
    resultados.append(resultado(categoria="correo", aprobado=False, motivos=["todos: mal"], verificada=False,
                                ids_inventados=["B"], ms=9000))
    resultados.append(resultado(categoria="correo", ms=10000, reintentada=True))
    resultados.append(evaluar.caso_con_error(resultado(id="roto"), "Límite de peticiones alcanzado."))
    s = evaluar.resumir(resultados)

    assert (s["casos"], s["aprobados"], s["aciertos"], s["errores_proveedor"]) == (10, 9, 0.9, 1)
    assert s["por_categoria"]["correo"] == {"casos": 2, "aprobados": 1, "aciertos": 0.5}
    assert (s["ids_inventados"], s["ids_escritos"], s["tasa_ids_inventados"]) == (1, 20, 0.05)
    assert (s["sustituidas"], s["tasa_sustituidas"], s["corregidas_al_reintentar"]) == (1, 0.1, 1)
    assert (s["latencia_media_ms"], s["latencia_p90_ms"]) == (5500, 9000)
    # 10.000 tokens de entrada a 4 $/M y 1.000 de salida a 20 $/M = 0,04 + 0,02
    assert s["coste_medio_usd"] == 0.06 and s["coste_total_usd"] == 0.6

    texto = evaluar.informe(s, resultados)
    assert "Aciertos: 9/10 (90%)" in texto
    assert "x [correo] p\n    - todos: mal" in texto
    assert "roto: Límite de peticiones alcanzado." in texto


def test_guardar_escribe_un_json_con_fecha(tmp_path):
    resultados = [resultado()]
    ruta = evaluar.guardar(evaluar.resumir(resultados), resultados, tmp_path)
    detalle = json.loads(ruta.read_text(encoding="utf-8"))
    assert ruta.suffix == ".json" and detalle["fecha"][:10] == ruta.name[:10]
    assert detalle["resumen"]["casos"] == 1 and detalle["casos"][0]["id"] == "x"


def test_filtros_por_categoria_id_y_limite(casos):
    todos = list(casos.values())
    assert {c["categoria"] for c in evaluar.filtrar(todos, categorias=["correo"])} == {"correo"}
    assert [c["id"] for c in evaluar.filtrar(todos, ids=["sin-01", "pri-03"])] == ["sin-01", "pri-03"]
    assert len(evaluar.filtrar(todos, limite=5)) == 5


def test_sin_clave_de_api_termina_con_un_mensaje_claro(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(evaluar.config, "PROVEEDOR", "anthropic")
    monkeypatch.setattr(evaluar, "guardar", lambda *a, **k: pytest.fail("no debe guardar nada"))
    assert evaluar.main(["--limite", "1"]) == 2
    assert "Falta la clave de API" in capsys.readouterr().err


def test_listar_no_necesita_clave(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert evaluar.main(["--listar", "--categoria", "ambigua"]) == 0
    salida = capsys.readouterr().out
    assert "amb-01" in salida and "2 casos" in salida
