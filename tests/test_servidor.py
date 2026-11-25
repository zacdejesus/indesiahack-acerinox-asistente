"""Tests de la API HTTP y la PWA. Sin LLM ni red: modelo guionizado y almacen en memoria.

Uso:  python -m pytest
"""

import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from asistente import config, roles
from asistente.agente import Asistente
from asistente.datos import InventarioSQLite
from asistente.proveedor import ErrorProveedor
from servidor import avisos
from servidor.almacen import AlmacenApp
from servidor.api import crear_app
from test_asistente import (AGOTADO_316_MADRID, ALT_316L_MADRID, BUSCAR_316_MADRID, INTERNOS, LENTO_430_VALENCIA,
                            ModeloGuionizado, ModeloQueApunta, _claves, dice, pide)


def guion_316_madrid():
    return (pide("buscar_stock", **BUSCAR_316_MADRID),
            pide("alternativas", producto_id=AGOTADO_316_MADRID),
            dice(f"No hay 316 en Madrid. Te propongo {ALT_316L_MADRID} (316L, Madrid)."))


def guion_430():
    return (pide("buscar_stock", grado="430", formato="chapa", acabado="BA"),
            pide("prioridad_comercial", lista_ids=[LENTO_430_VALENCIA]),
            dice(f"Ofrece primero {LENTO_430_VALENCIA}: lleva tiempo parado."))


def cliente_http(*guion, inv=None, modelo=None):
    inv = inv or InventarioSQLite()
    modelo = modelo or ModeloGuionizado(*guion)
    app = crear_app(Asistente(modelo, inv), AlmacenApp(":memory:"))
    return TestClient(app)


def entrar(http: TestClient, usuario: str) -> dict:
    r = http.post("/api/login", json={"usuario": usuario, "clave": usuario})
    assert r.status_code == 200, r.text
    return r.json()


# --- Sesion -----------------------------------------------------------------------

def test_sin_sesion_no_se_puede_preguntar():
    http = cliente_http()
    assert http.post("/api/preguntar", json={"pregunta": "hola"}).status_code == 401
    http.cookies.set("sesion", "inventada")
    assert http.post("/api/preguntar", json={"pregunta": "hola"}).status_code == 401


def test_clave_incorrecta():
    http = cliente_http()
    assert http.post("/api/login", json={"usuario": "comercial", "clave": "mal"}).status_code == 401
    assert http.post("/api/login", json={"usuario": "nadie", "clave": "nadie"}).status_code == 401


def test_login_devuelve_el_rol_y_la_cookie_es_httponly():
    http = cliente_http()
    r = http.post("/api/login", json={"usuario": "cliente", "clave": "cliente"})
    assert r.json() == {"usuario": "cliente", "rol": "cliente", "ve_razonamiento": False}
    assert "httponly" in r.headers["set-cookie"].lower()
    assert http.get("/api/yo").json()["rol"] == "cliente"
    http.post("/api/logout")
    assert http.get("/api/yo").status_code == 401


# --- Chat por rol -----------------------------------------------------------------

def test_comercial_recibe_razonamiento_precio_y_rotacion_pero_no_margen():
    http = cliente_http(*guion_430())
    entrar(http, "comercial")
    r = http.post("/api/preguntar", json={"pregunta": "430 BA fina"}).json()
    assert r["verificada"] and r["productos"][0]["id"] == LENTO_430_VALENCIA
    assert {"precio_eur_kg", "rotacion_dias"} <= set(r["productos"][0])
    assert [p["herramienta"] for p in r["razonamiento"]["traza"]] == ["buscar_stock", "prioridad_comercial"]
    assert "margen_pct" not in _claves(r)


def test_cliente_nunca_recibe_datos_internos_ni_razonamiento():
    modelo = ModeloQueApunta(*guion_430())
    http = cliente_http(modelo=modelo)
    entrar(http, "cliente")
    r = http.post("/api/preguntar", json={"pregunta": "¿Tenéis 430 BA?"}).json()
    assert "razonamiento" not in r
    assert r["productos"] and not _claves(r) & INTERNOS
    assert "prioridad_comercial" not in modelo.herramientas
    assert not _claves(modelo.mensajes_recibidos) & INTERNOS


def test_el_rol_no_se_puede_cambiar_desde_el_navegador():
    modelo = ModeloQueApunta(*guion_430())
    http = cliente_http(modelo=modelo)
    entrar(http, "cliente")
    r = http.post("/api/preguntar", json={"pregunta": "430 BA", "rol": "comercial", "ve_razonamiento": True}).json()
    assert "razonamiento" not in r and not _claves(r) & INTERNOS
    assert modelo.sistemas[0] == roles.CLIENTE.sistema and "prioridad_comercial" not in modelo.herramientas


def test_cada_sesion_tiene_su_conversacion():
    modelo = ModeloGuionizado(dice("Primera"), dice("Segunda"))
    app = crear_app(Asistente(modelo, InventarioSQLite()), AlmacenApp(":memory:"))
    a, b = TestClient(app), TestClient(app)
    entrar(a, "comercial")
    entrar(b, "cliente")
    a.post("/api/preguntar", json={"pregunta": "Hola desde A"})
    b.post("/api/preguntar", json={"pregunta": "Hola desde B"})
    # El segundo usuario empieza de cero: el modelo solo ve su pregunta.
    assert [m.get("texto") for m in modelo.mensajes_recibidos[1]] == ["Hola desde B"]


def test_error_del_modelo_devuelve_502_y_se_puede_reintentar():
    class FallaUnaVez(ModeloGuionizado):
        def completar(self, sistema, mensajes, herramientas):
            if not getattr(self, "fallado", False):
                self.fallado = True
                raise ErrorProveedor("Clave de API no válida.")
            return super().completar(sistema, mensajes, herramientas)

    modelo = FallaUnaVez(dice("Ahora sí."))
    http = cliente_http(modelo=modelo)
    entrar(http, "comercial")
    r = http.post("/api/preguntar", json={"pregunta": "hola"})
    assert r.status_code == 502 and "Clave" in r.json()["detail"]
    assert http.post("/api/preguntar", json={"pregunta": "hola"}).json()["texto"] == "Ahora sí."
    assert [m["rol"] for m in modelo.mensajes_recibidos[0]] == ["usuario"]   # sin la pregunta duplicada


def test_nueva_conversacion_borra_el_historial():
    modelo = ModeloGuionizado(dice("Uno"), dice("Dos"))
    http = cliente_http(modelo=modelo)
    entrar(http, "comercial")
    http.post("/api/preguntar", json={"pregunta": "primera"})
    http.post("/api/conversacion/nueva")
    http.post("/api/preguntar", json={"pregunta": "segunda"})
    assert len(modelo.mensajes_recibidos[1]) == 1


# --- Historial de conversaciones ------------------------------------------------

def test_cada_conversacion_se_guarda_con_titulo_y_turnos():
    http = cliente_http(*guion_316_madrid())
    entrar(http, "comercial")
    r = http.post("/api/preguntar", json={"pregunta": "  Necesito chapa 316\n en Madrid  "}).json()
    lista = http.get("/api/conversaciones").json()
    assert [c["id"] for c in lista] == [r["conversacion"]]
    assert lista[0]["titulo"] == "Necesito chapa 316 en Madrid"
    guardada = http.get(f"/api/conversaciones/{r['conversacion']}").json()
    assert [t["pregunta"] for t in guardada["turnos"]] == ["  Necesito chapa 316\n en Madrid  "]
    # Se guarda lo que vio el usuario, con sus productos y razonamiento, sin botones de aviso.
    assert guardada["turnos"][0]["respuesta"]["texto"] == r["texto"]
    assert guardada["turnos"][0]["respuesta"]["productos"] == r["productos"]
    assert guardada["turnos"][0]["respuesta"]["sin_stock"] == []


def test_titulo_largo_se_recorta():
    http = cliente_http(dice("Vale."))
    entrar(http, "comercial")
    http.post("/api/preguntar", json={"pregunta": "palabra " * 30})
    titulo = http.get("/api/conversaciones").json()[0]["titulo"]
    assert len(titulo) == 60 and titulo.endswith("…")


def test_seguir_una_conversacion_antigua_y_empezar_otra():
    modelo = ModeloGuionizado(dice("Uno"), dice("Dos"), dice("Tres"))
    http = cliente_http(modelo=modelo)
    entrar(http, "comercial")
    primera = http.post("/api/preguntar", json={"pregunta": "primera"}).json()["conversacion"]
    http.post("/api/conversacion/nueva")
    segunda = http.post("/api/preguntar", json={"pregunta": "segunda"}).json()["conversacion"]
    assert segunda != primera
    # Volver a la primera: el modelo recibe su historial, no el de la segunda.
    r = http.post("/api/preguntar", json={"pregunta": "tercera", "conversacion": primera}).json()
    assert r["conversacion"] == primera
    assert [m.get("texto") for m in modelo.mensajes_recibidos[2]] == ["primera", "Uno", "tercera"]
    # La mas reciente sale primero, como en claude.ai.
    assert [c["id"] for c in http.get("/api/conversaciones").json()] == [primera, segunda]


def test_nadie_ve_ni_usa_ni_borra_conversaciones_de_otro():
    modelo = ModeloGuionizado(dice("Privado"), dice("No debería llegar"))
    app = crear_app(Asistente(modelo, InventarioSQLite()), AlmacenApp(":memory:"))
    a, b = TestClient(app), TestClient(app)
    entrar(a, "comercial")
    entrar(b, "cliente")
    ajena = a.post("/api/preguntar", json={"pregunta": "margen del 316"}).json()["conversacion"]
    assert b.get("/api/conversaciones").json() == []
    assert b.get(f"/api/conversaciones/{ajena}").status_code == 404
    assert b.post("/api/preguntar", json={"pregunta": "hola", "conversacion": ajena}).status_code == 404
    assert b.delete(f"/api/conversaciones/{ajena}").status_code == 404
    assert len(modelo.mensajes_recibidos) == 1   # al cliente no se le llego a llamar al modelo
    assert a.get(f"/api/conversaciones/{ajena}").status_code == 200


def test_borrar_conversacion():
    http = cliente_http(dice("Uno"), dice("Dos"))
    entrar(http, "comercial")
    conv = http.post("/api/preguntar", json={"pregunta": "primera"}).json()["conversacion"]
    assert http.delete(f"/api/conversaciones/{conv}").json() == {"ok": True}
    assert http.get("/api/conversaciones").json() == []
    assert http.get(f"/api/conversaciones/{conv}").status_code == 404
    # La siguiente pregunta empieza una conversacion nueva.
    assert http.post("/api/preguntar", json={"pregunta": "otra"}).json()["conversacion"] != conv


def test_un_fallo_del_modelo_no_crea_conversacion_vacia():
    class Falla(ModeloGuionizado):
        def completar(self, sistema, mensajes, herramientas):
            raise ErrorProveedor("Sin servicio.")

    http = cliente_http(modelo=Falla())
    entrar(http, "comercial")
    assert http.post("/api/preguntar", json={"pregunta": "hola"}).status_code == 502
    assert http.get("/api/conversaciones").json() == []


def test_conversacion_se_retoma_tras_reiniciar_el_servidor():
    """Sesion nueva y contexto vacio en memoria: se reconstruye desde el historial."""
    almacen = AlmacenApp(":memory:")
    antes = TestClient(crear_app(Asistente(ModeloGuionizado(*guion_316_madrid()), InventarioSQLite()), almacen))
    entrar(antes, "comercial")
    conv = antes.post("/api/preguntar", json={"pregunta": "chapa 316 en Madrid"}).json()["conversacion"]

    # El modelo cita el id de la respuesta anterior sin volver a buscar: el
    # validador lo acepta porque la evidencia se guardo con la conversacion.
    modelo = ModeloGuionizado(dice(f"Sí, {ALT_316L_MADRID} sigue siendo la mejor opción."))
    despues = TestClient(crear_app(Asistente(modelo, InventarioSQLite()), almacen))
    entrar(despues, "comercial")
    r = despues.post("/api/preguntar", json={"pregunta": "¿y la primera opción?", "conversacion": conv}).json()
    assert r["verificada"] and r["conversacion"] == conv
    assert [m["rol"] for m in modelo.mensajes_recibidos[0]] == ["usuario", "asistente", "usuario"]
    assert modelo.mensajes_recibidos[0][0]["texto"] == "chapa 316 en Madrid"
    assert len(despues.get(f"/api/conversaciones/{conv}").json()["turnos"]) == 2


# --- Ofertas ----------------------------------------------------------------------

def test_cliente_pide_oferta_y_el_comercial_la_ve_y_recibe_notificacion():
    app = crear_app(Asistente(ModeloGuionizado(), InventarioSQLite()), AlmacenApp(":memory:"))
    cliente, comercial = TestClient(app), TestClient(app)
    entrar(cliente, "cliente")
    entrar(comercial, "comercial")

    r = cliente.post("/api/ofertas", json={"producto_id": ALT_316L_MADRID, "cantidad_kg": 2000, "comentario": "urgente"})
    assert r.status_code == 200
    assert cliente.get("/api/ofertas").status_code == 403   # la lista es solo del comercial
    ofertas = comercial.get("/api/ofertas").json()
    assert ofertas[0]["producto_id"] == ALT_316L_MADRID and ofertas[0]["usuario"] == "cliente"
    notas = comercial.get("/api/notificaciones").json()
    assert ALT_316L_MADRID in notas[0]["texto"]
    assert cliente.get("/api/notificaciones").json() == []   # al cliente no le llega


def test_oferta_de_producto_inexistente():
    http = cliente_http()
    entrar(http, "cliente")
    assert http.post("/api/ofertas", json={"producto_id": "ACX-99999"}).status_code == 404


# --- Avisos de reposicion -----------------------------------------------------------

def test_respuesta_sin_stock_ofrece_aviso_con_los_criterios_buscados():
    http = cliente_http(*guion_316_madrid())
    entrar(http, "cliente")
    r = http.post("/api/preguntar", json={"pregunta": "chapa 316 en Madrid"}).json()
    assert r["sin_stock"] == [{"busqueda": 0, "descripcion": "316 chapa acabado 2B 2 mm en Madrid"}]
    a = http.post("/api/avisos", json={"busqueda": 0})
    assert a.status_code == 200
    assert http.get("/api/avisos").json()[0]["criterios"]["almacen"] == "Madrid"
    assert http.post("/api/avisos", json={"busqueda": 5}).status_code == 404


def test_comprobar_avisos_solo_comercial_y_sin_stock_no_cumple():
    http = cliente_http(*guion_316_madrid())
    entrar(http, "cliente")
    http.post("/api/preguntar", json={"pregunta": "chapa 316 en Madrid"})
    http.post("/api/avisos", json={"busqueda": 0})
    assert http.post("/api/avisos/comprobar").status_code == 403
    entrar(http, "comercial")
    assert http.post("/api/avisos/comprobar").json() == {"cumplidos": []}


@pytest.fixture
def inventario_modificable(tmp_path):
    """Copia del inventario en la que se puede simular una reposicion."""
    ruta = tmp_path / "inventario.db"
    shutil.copy(config.RUTA_DB, ruta)
    return ruta


def test_al_reponer_stock_se_cumple_el_aviso_y_se_notifica(inventario_modificable):
    almacen = AlmacenApp(":memory:")
    criterios = {"grado": "316", "formato": "chapa", "acabado": "2B", "espesor_min": 2.0, "espesor_max": 2.0,
                 "almacen": "Madrid", "cantidad_kg": 2000.0}
    aviso_id = almacen.crear_aviso("cliente", criterios)

    assert avisos.comprobar(almacen, InventarioSQLite(inventario_modificable)) == []

    with sqlite3.connect(inventario_modificable) as con:
        con.execute("UPDATE inventario SET stock_kg = 1000 WHERE id = ?", (AGOTADO_316_MADRID,))
    # No alcanza los 2.000 kg pedidos: el aviso sigue pendiente.
    assert avisos.comprobar(almacen, InventarioSQLite(inventario_modificable)) == []

    with sqlite3.connect(inventario_modificable) as con:
        con.execute("UPDATE inventario SET stock_kg = 5000 WHERE id = ?", (AGOTADO_316_MADRID,))
    cumplidos = avisos.comprobar(almacen, InventarioSQLite(inventario_modificable))
    assert [a["id"] for a in cumplidos] == [aviso_id]
    assert almacen.avisos(pendientes=True) == []
    assert almacen.avisos()[0]["cumplido"]   # queda como registro de demanda
    assert "316" in almacen.notificaciones("cliente", "cliente")[0]["texto"]
    # Una segunda comprobacion no vuelve a notificar.
    assert avisos.comprobar(almacen, InventarioSQLite(inventario_modificable)) == []


def test_describir_criterios():
    assert avisos.describir({"grado": "304", "formato": "bobina", "espesor_min": 1.0, "espesor_max": 2.0}) == \
        "304 bobina espesor desde 1 mm hasta 2 mm"
    assert avisos.describir({}) == "cualquier material"


# --- PWA --------------------------------------------------------------------------

def test_sirve_la_pwa():
    http = cliente_http()
    assert "manifest.webmanifest" in http.get("/").text
    manifest = http.get("/manifest.webmanifest").json()
    assert manifest["display"] == "standalone" and {i["sizes"] for i in manifest["icons"]} == {"192x192", "512x512"}
    sw = http.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    assert http.get("/iconos/icono-512.png").headers["content-type"] == "image/png"


def test_usuarios_y_claves_se_guardan_con_hash():
    almacen = AlmacenApp(":memory:")
    almacen.crear_usuario("ana", "secreta", "cliente")
    assert almacen.autenticar("ana", "secreta") == "cliente"
    assert almacen.autenticar("ana", "otra") is None
    assert "secreta" not in str(almacen._filas("SELECT * FROM usuarios"))
