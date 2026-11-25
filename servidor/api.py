"""API HTTP del asistente y servidor de la PWA.

Capa fina: traduce peticiones HTTP a llamadas al nucleo (asistente/) y al
almacen de la aplicacion, y las respuestas a JSON. No decide que se recomienda
ni que ve cada rol: eso lo hace el nucleo con el Rol de la sesion.

El rol sale siempre de la sesion del servidor (cookie HttpOnly), nunca de un
campo que mande el navegador.

Arranque:  python -m servidor   (o uvicorn --factory servidor.api:crear_app)
"""

import secrets
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from asistente import config
from asistente.agente import Asistente, Conversacion, Respuesta
from asistente.datos import ErrorDatos, Inventario, crear_inventario
from asistente.proveedor import ErrorProveedor, crear_proveedor, msg_asistente, msg_usuario
from asistente.roles import ROLES, Rol

from . import avisos
from .almacen import AlmacenApp

ESTATICOS = Path(__file__).resolve().parent / "static"
COOKIE = "sesion"


# --- Sesiones -------------------------------------------------------------------

@dataclass
class Sesion:
    usuario: str
    rol: Rol
    activa: int | None = None            # conversacion abierta; None = la proxima pregunta empieza una nueva
    # Contexto del modelo de cada conversacion usada en esta sesion. Mientras vive
    # la sesion se conserva entero (con las consultas a herramientas); si no esta,
    # se reconstruye desde el historial guardado.
    contextos: dict[int, Conversacion] = field(default_factory=dict)
    sin_stock: list[dict] = field(default_factory=list)   # criterios ofrecibles como aviso
    # Una pregunta a la vez por sesion: dos pestañas no pueden mezclar la conversacion.
    cerrojo: threading.Lock = field(default_factory=threading.Lock)


class Sesiones:
    """En memoria: un reinicio del servidor cierra todas las sesiones (suficiente para la demo)."""

    def __init__(self):
        self._sesiones: dict[str, Sesion] = {}

    def abrir(self, usuario: str, rol: Rol) -> str:
        token = secrets.token_urlsafe(32)
        self._sesiones[token] = Sesion(usuario, rol)
        return token

    def obtener(self, token: str | None) -> Sesion | None:
        return self._sesiones.get(token) if token else None

    def cerrar(self, token: str | None) -> None:
        self._sesiones.pop(token or "", None)


# --- Cuerpos de las peticiones --------------------------------------------------
# Ninguno lleva rol: aunque el navegador lo mande, pydantic lo descarta.

class Login(BaseModel):
    usuario: str
    clave: str


class Pregunta(BaseModel):
    pregunta: str = Field(min_length=1, max_length=8000)
    conversacion: int | None = None   # None = la conversacion abierta en la sesion (o una nueva)


class PeticionOferta(BaseModel):
    producto_id: str
    cantidad_kg: float | None = Field(default=None, gt=0)
    comentario: str = Field(default="", max_length=2000)


class PeticionAviso(BaseModel):
    busqueda: int = Field(ge=0)   # posicion en la lista sin_stock de la ultima respuesta


# --- Aplicacion -----------------------------------------------------------------

def crear_app(asistente: Asistente | None = None, almacen: AlmacenApp | None = None,
              inventario: Inventario | None = None) -> FastAPI:
    """Los tests pasan un asistente con el modelo guionizado y un almacen en memoria."""
    app = FastAPI(title="Asistente de inventario", docs_url="/api/docs", openapi_url="/api/openapi.json")
    sesiones = Sesiones()
    almacen = almacen or AlmacenApp(config.RUTA_APP_DB)
    for entrada in filter(None, config.USUARIOS_DEMO.split(",")):
        usuario, clave, rol = entrada.strip().split(":")
        almacen.crear_usuario(usuario, clave, rol)

    # El asistente se crea en la primera pregunta: asi el servidor arranca aunque
    # falte la clave del modelo, y el fallo se ve en el chat, no en la consola.
    estado = {"asistente": asistente, "inventario": inventario or (asistente.inventario if asistente else None)}
    cerrojo_creacion = threading.Lock()

    def obtener_asistente() -> Asistente:
        with cerrojo_creacion:
            if estado["asistente"] is None:
                inv = estado["inventario"] or crear_inventario()
                estado["inventario"] = inv
                estado["asistente"] = Asistente(crear_proveedor(), inv)
            return estado["asistente"]

    def obtener_inventario() -> Inventario:
        with cerrojo_creacion:
            if estado["inventario"] is None:
                estado["inventario"] = crear_inventario()
            return estado["inventario"]

    def sesion_actual(request: Request) -> Sesion:
        sesion = sesiones.obtener(request.cookies.get(COOKIE))
        if sesion is None:
            raise HTTPException(401, "Inicia sesión.")
        return sesion

    def solo(rol: str) -> Callable[[Sesion], Sesion]:
        def comprobar(sesion: Sesion = Depends(sesion_actual)) -> Sesion:
            if sesion.rol.nombre != rol:
                raise HTTPException(403, "Tu usuario no tiene acceso a esta función.")
            return sesion
        return comprobar

    def perfil(sesion: Sesion) -> dict:
        return {"usuario": sesion.usuario, "rol": sesion.rol.nombre, "ve_razonamiento": sesion.rol.ve_razonamiento}

    # --- Sesion -----------------------------------------------------------------

    @app.post("/api/login")
    def login(datos: Login, response: Response) -> dict:
        rol = almacen.autenticar(datos.usuario, datos.clave)
        if rol not in ROLES:
            raise HTTPException(401, "Usuario o clave incorrectos.")
        token = sesiones.abrir(datos.usuario, ROLES[rol])
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=config.COOKIE_SEGURA)
        return perfil(sesiones.obtener(token))

    @app.post("/api/logout")
    def logout(request: Request, response: Response) -> dict:
        sesiones.cerrar(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/yo")
    def yo(sesion: Sesion = Depends(sesion_actual)) -> dict:
        return perfil(sesion)

    # --- Chat -------------------------------------------------------------------

    @app.post("/api/conversacion/nueva")
    def nueva(sesion: Sesion = Depends(sesion_actual)) -> dict:
        with sesion.cerrojo:
            sesion.activa, sesion.sin_stock = None, []
        return {"ok": True}

    @app.get("/api/conversaciones")
    def ver_conversaciones(sesion: Sesion = Depends(sesion_actual)) -> list[dict]:
        return almacen.conversaciones(sesion.usuario)

    @app.get("/api/conversaciones/{conversacion_id}")
    def ver_conversacion(conversacion_id: int, sesion: Sesion = Depends(sesion_actual)) -> dict:
        guardada = almacen.conversacion(conversacion_id, sesion.usuario)
        if guardada is None:
            raise HTTPException(404, "No existe esa conversación.")
        return {"id": guardada["id"], "titulo": guardada["titulo"], "turnos": guardada["turnos"]}

    @app.delete("/api/conversaciones/{conversacion_id}")
    def borrar_conversacion(conversacion_id: int, sesion: Sesion = Depends(sesion_actual)) -> dict:
        if not almacen.borrar_conversacion(conversacion_id, sesion.usuario):
            raise HTTPException(404, "No existe esa conversación.")
        sesion.contextos.pop(conversacion_id, None)
        if sesion.activa == conversacion_id:
            sesion.activa, sesion.sin_stock = None, []
        return {"ok": True}

    def contexto(sesion: Sesion, conversacion_id: int) -> Conversacion:
        if conversacion_id not in sesion.contextos:
            guardada = almacen.conversacion(conversacion_id, sesion.usuario)
            if guardada is None:
                raise HTTPException(404, "No existe esa conversación.")
            sesion.contextos[conversacion_id] = _reconstruir(guardada)
        return sesion.contextos[conversacion_id]

    @app.post("/api/preguntar")
    def preguntar(datos: Pregunta, sesion: Sesion = Depends(sesion_actual)) -> dict:
        if not sesion.cerrojo.acquire(blocking=False):
            raise HTTPException(409, "Espera a que termine la respuesta anterior.")
        try:
            try:
                asistente = obtener_asistente().con_rol(sesion.rol)
            except (ErrorProveedor, ErrorDatos) as e:
                raise HTTPException(503, str(e)) from None
            conv_id = datos.conversacion if datos.conversacion is not None else sesion.activa
            conv = contexto(sesion, conv_id) if conv_id is not None else Conversacion()
            n_mensajes, evidencia = len(conv.mensajes), set(conv.evidencia)
            try:
                respuesta = asistente.responder(datos.pregunta, conv)
            except ErrorProveedor as e:
                raise HTTPException(502, str(e)) from None
            except Exception as e:
                # El asistente ya deshace la conversacion ante ErrorProveedor; aqui
                # se cubre cualquier otro fallo para que la pregunta se pueda repetir.
                del conv.mensajes[n_mensajes:]
                conv.evidencia = evidencia
                raise HTTPException(500, f"Error inesperado: {e}") from None
            # La conversacion se crea con la primera respuesta buena: un fallo no deja entradas vacias.
            if conv_id is None:
                conv_id = almacen.crear_conversacion(sesion.usuario, _titulo(datos.pregunta))
                sesion.contextos[conv_id] = conv
            sesion.activa = conv_id
            sesion.sin_stock = avisos.busquedas_sin_stock(respuesta)
            resultado = _a_json(respuesta, sesion)
            # Los botones de aviso dependen de la sesion: en el historial no se guardan.
            almacen.guardar_turno(conv_id, datos.pregunta, resultado | {"sin_stock": []}, conv.evidencia)
            return resultado | {"conversacion": conv_id}
        finally:
            sesion.cerrojo.release()

    # --- Peticiones de oferta ---------------------------------------------------

    @app.post("/api/ofertas")
    def pedir_oferta(datos: PeticionOferta, sesion: Sesion = Depends(sesion_actual)) -> dict:
        if not obtener_inventario().por_ids([datos.producto_id]):
            raise HTTPException(404, f"No existe el producto {datos.producto_id}.")
        oferta_id = almacen.crear_oferta(sesion.usuario, datos.producto_id, datos.cantidad_kg, datos.comentario)
        cantidad = f" ({datos.cantidad_kg:g} kg)" if datos.cantidad_kg else ""
        almacen.notificar("rol:comercial", "Nueva petición de oferta",
                          f"{sesion.usuario} pide oferta de {datos.producto_id}{cantidad}.")
        return {"id": oferta_id}

    @app.get("/api/ofertas")
    def ver_ofertas(sesion: Sesion = Depends(solo("comercial"))) -> list[dict]:
        return almacen.ofertas()

    # --- Avisos de reposicion ---------------------------------------------------

    @app.post("/api/avisos")
    def crear_aviso(datos: PeticionAviso, sesion: Sesion = Depends(sesion_actual)) -> dict:
        # Los criterios salen de la sesion (lo que busco el asistente), no del navegador.
        if datos.busqueda >= len(sesion.sin_stock):
            raise HTTPException(404, "Esa búsqueda ya no está disponible. Vuelve a preguntar.")
        criterios = sesion.sin_stock[datos.busqueda]
        return {"id": almacen.crear_aviso(sesion.usuario, criterios), "descripcion": avisos.describir(criterios)}

    @app.get("/api/avisos")
    def ver_avisos(sesion: Sesion = Depends(sesion_actual)) -> list[dict]:
        # El comercial ve todos: son la demanda no atendida.
        usuario = None if sesion.rol.nombre == "comercial" else sesion.usuario
        return [a | {"descripcion": avisos.describir(a["criterios"])} for a in almacen.avisos(usuario)]

    @app.post("/api/avisos/comprobar")
    def comprobar_avisos(sesion: Sesion = Depends(solo("comercial"))) -> dict:
        cumplidos = avisos.comprobar(almacen, obtener_inventario())
        return {"cumplidos": [a["id"] for a in cumplidos]}

    # --- Notificaciones ---------------------------------------------------------
    # La PWA las consulta cada pocos segundos y las enseña como notificacion del
    # sistema. Web Push con VAPID (avisar con la app cerrada) queda pendiente.

    @app.get("/api/notificaciones")
    def notificaciones(desde: int = 0, sesion: Sesion = Depends(sesion_actual)) -> list[dict]:
        return almacen.notificaciones(sesion.usuario, sesion.rol.nombre, desde)

    # --- PWA --------------------------------------------------------------------

    @app.get("/sw.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        # En la raiz para que su alcance cubra toda la app; sin cache para que se actualice.
        return FileResponse(ESTATICOS / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=ESTATICOS, html=True), name="pwa")
    return app


def _titulo(pregunta: str, largo: int = 60) -> str:
    """Primera frase de la pregunta, sin modelo: gratis e instantaneo."""
    texto = " ".join(pregunta.split())
    return texto if len(texto) <= largo else texto[:largo - 1].rstrip() + "…"


def _reconstruir(guardada: dict) -> Conversacion:
    """Contexto del modelo a partir del historial: preguntas y respuestas en texto.

    Las consultas a herramientas de turnos anteriores no se guardan (su formato
    depende del proveedor); si el modelo necesita datos frescos, vuelve a buscar.
    La evidencia si se conserva, para que el validador acepte los ids ya vistos.
    """
    conv = Conversacion(evidencia=set(guardada["evidencia"]))
    for t in guardada["turnos"]:
        conv.mensajes += [msg_usuario(t["pregunta"]), msg_asistente(t["respuesta"]["texto"])]
    return conv


def _a_json(respuesta: Respuesta, sesion: Sesion) -> dict:
    """Lo que viaja al navegador. Los productos ya vienen filtrados por el nucleo segun el rol."""
    datos = {
        "texto": respuesta.texto,
        "verificada": respuesta.verificada,
        "productos": respuesta.productos,
        "sin_stock": [{"busqueda": i, "descripcion": avisos.describir(c)} for i, c in enumerate(sesion.sin_stock)],
    }
    if sesion.rol.ve_razonamiento:
        datos["razonamiento"] = {
            "traza": [asdict(p) for p in respuesta.traza],
            "citados": respuesta.validacion.citados,
            "rechazados": respuesta.rechazados,
            "reintentada": respuesta.reintentada,
            "llamadas_llm": respuesta.llamadas_llm,
            "tokens_entrada": respuesta.tokens_entrada,
            "tokens_salida": respuesta.tokens_salida,
            "ms": respuesta.ms,
        }
    return datos
