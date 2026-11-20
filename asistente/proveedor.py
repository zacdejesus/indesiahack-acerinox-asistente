"""Capa fina de proveedor de LLM.

El agente no sabe que proveedor hay detras: trabaja con mensajes neutros y con
RespuestaLLM. Cada proveedor es un adaptador que traduce en las dos direcciones.

Mensajes neutros (diccionarios):
  {"rol": "usuario", "texto": "..."}
  {"rol": "asistente", "texto": "...", "crudo": <lo que devolvio el proveedor>}
  {"rol": "herramientas", "resultados": [{"id", "contenido", "error"}]}
"""

import http.client
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import config


@dataclass
class Llamada:
    """El modelo pide ejecutar una herramienta."""
    id: str
    nombre: str
    argumentos: dict


@dataclass
class RespuestaLLM:
    texto: str
    llamadas: list[Llamada] = field(default_factory=list)
    fin: str = "fin"            # fin | herramientas | max_tokens | rechazo
    crudo: Any = None           # contenido original, para reenviarlo tal cual en la siguiente vuelta
    tokens_entrada: int = 0
    tokens_salida: int = 0


class ErrorProveedor(Exception):
    """Fallo al hablar con el LLM, con un mensaje que se puede enseñar al usuario."""


class ProveedorLLM(Protocol):
    """El contrato. Cualquier clase con este metodo sirve como proveedor."""

    def completar(self, sistema: str, mensajes: list[dict], herramientas: list[dict]) -> RespuestaLLM: ...


# --- Constructores de mensajes neutros -----------------------------------------

def msg_usuario(texto: str) -> dict:
    return {"rol": "usuario", "texto": texto}


def msg_asistente(texto: str, crudo: Any = None) -> dict:
    return {"rol": "asistente", "texto": texto, "crudo": crudo}


def msg_herramientas(resultados: list[dict]) -> dict:
    return {"rol": "herramientas", "resultados": resultados}


# --- Adaptador de Anthropic ------------------------------------------------------

class ProveedorAnthropic:
    _FIN = {"end_turn": "fin", "tool_use": "herramientas", "max_tokens": "max_tokens", "refusal": "rechazo"}

    def __init__(self, modelo: str = config.MODELO, esfuerzo: str = config.ESFUERZO):
        import anthropic  # import aqui: solo hace falta el SDK si se usa este proveedor
        self._anthropic = anthropic
        self._cliente = anthropic.Anthropic()  # lee ANTHROPIC_API_KEY del entorno
        self._modelo = modelo
        self.modelo = modelo  # nombre publico, para informes
        self._esfuerzo = esfuerzo

    def completar(self, sistema, mensajes, herramientas) -> RespuestaLLM:
        a = self._anthropic
        try:
            r = self._cliente.beta.messages.create(
                model=self._modelo,
                max_tokens=config.MAX_TOKENS,
                system=sistema,
                tools=[self._herramienta(h) for h in herramientas],
                messages=[self._mensaje(m) for m in mensajes],
                output_config={"effort": self._esfuerzo},
                # El prompt de sistema y las herramientas no cambian entre llamadas:
                # se cachean y las siguientes vueltas salen mas baratas y rapidas.
                cache_control={"type": "ephemeral"},
                # Si un filtro de seguridad rechaza la peticion, la API la reintenta
                # en otro modelo dentro de la misma llamada.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.AuthenticationError:
            raise ErrorProveedor("Clave de API no válida. Revisa ANTHROPIC_API_KEY en .env.") from None
        except a.RateLimitError:
            raise ErrorProveedor("Límite de peticiones alcanzado. Espera unos segundos y reintenta.") from None
        except a.APIStatusError as e:
            raise ErrorProveedor(f"Error de la API ({e.status_code}): {e.message}") from None
        except a.APIConnectionError:
            raise ErrorProveedor("No hay conexión con la API. Revisa la red.") from None
        except TypeError as e:  # el SDK lanza TypeError si no encuentra credenciales
            if "authentication" not in str(e):
                raise
            raise ErrorProveedor(
                "Falta la clave de API. Copia .env.example a .env y rellena ANTHROPIC_API_KEY.") from None

        return RespuestaLLM(
            texto="\n".join(b.text for b in r.content if b.type == "text").strip(),
            llamadas=[Llamada(b.id, b.name, dict(b.input)) for b in r.content if b.type == "tool_use"],
            fin=self._FIN.get(r.stop_reason, "fin"),
            crudo=r.content,
            tokens_entrada=r.usage.input_tokens + (r.usage.cache_read_input_tokens or 0)
            + (r.usage.cache_creation_input_tokens or 0),
            tokens_salida=r.usage.output_tokens,
        )

    @staticmethod
    def _herramienta(h: dict) -> dict:
        return {"name": h["nombre"], "description": h["descripcion"], "input_schema": h["parametros"]}

    @staticmethod
    def _mensaje(m: dict) -> dict:
        if m["rol"] == "usuario":
            return {"role": "user", "content": m["texto"]}
        if m["rol"] == "asistente":
            # Se reenvia el contenido original completo (incluye los bloques de
            # razonamiento y de llamada a herramienta), no solo el texto.
            return {"role": "assistant", "content": m["crudo"] or m["texto"] or "(sin respuesta)"}
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r["id"], "is_error": r["error"],
             "content": json.dumps(r["contenido"], ensure_ascii=False)}
            for r in m["resultados"]]}


# --- Adaptador para APIs compatibles con OpenAI (servidor local MLX, Ollama, Azure...) ---

class ProveedorCompatibleOpenAI:
    """Habla el formato /chat/completions. Sirve para un modelo local y no necesita SDK."""

    _FIN = {"tool_calls": "herramientas", "length": "max_tokens", "content_filter": "rechazo"}

    def __init__(self, url: str = config.LOCAL_URL, modelo: str = config.LOCAL_MODELO,
                 clave: str = config.LOCAL_CLAVE):
        self._url = url.rstrip("/")
        self._clave = clave
        self._modelo = modelo or self._peticion("GET", "/models")["data"][0]["id"]
        self.modelo = self._modelo  # nombre publico, para informes

    def completar(self, sistema, mensajes, herramientas) -> RespuestaLLM:
        cuerpo = {
            "model": self._modelo,
            "max_tokens": config.LOCAL_MAX_TOKENS,
            "messages": [{"role": "system", "content": sistema}]
            + [m for neutro in mensajes for m in self._mensajes(neutro)],
            "tools": [{"type": "function", "function": {
                "name": h["nombre"], "description": h["descripcion"], "parameters": h["parametros"]}}
                for h in herramientas],
        }
        r = self._peticion("POST", "/chat/completions", cuerpo)
        opcion = r["choices"][0]
        mensaje = opcion["message"]
        llamadas = [Llamada(c["id"], c["function"]["name"], self._argumentos(c["function"].get("arguments")))
                    for c in mensaje.get("tool_calls") or []]
        uso = r.get("usage") or {}
        return RespuestaLLM(
            texto=(mensaje.get("content") or "").strip(),
            llamadas=llamadas,
            fin="herramientas" if llamadas else self._FIN.get(opcion.get("finish_reason"), "fin"),
            crudo=mensaje,
            tokens_entrada=uso.get("prompt_tokens", 0),
            tokens_salida=uso.get("completion_tokens", 0),
        )

    @staticmethod
    def _argumentos(crudo) -> dict:
        """Un modelo pequeño puede devolver JSON roto. Se convierte en un argumento
        desconocido, y la herramienta le contesta con el error para que lo repita."""
        if isinstance(crudo, dict):
            return crudo
        try:
            argumentos = json.loads(crudo or "{}")
        except json.JSONDecodeError:
            return {"json_invalido": crudo}
        return argumentos if isinstance(argumentos, dict) else {"json_invalido": crudo}

    @staticmethod
    def _mensajes(m: dict) -> list[dict]:
        if m["rol"] == "usuario":
            return [{"role": "user", "content": m["texto"]}]
        if m["rol"] == "asistente":
            if isinstance(m["crudo"], dict):
                return [{"role": "assistant", "content": m["crudo"].get("content") or "",
                         "tool_calls": m["crudo"].get("tool_calls") or []} if m["crudo"].get("tool_calls")
                        else {"role": "assistant", "content": m["crudo"].get("content") or ""}]
            return [{"role": "assistant", "content": m["texto"] or "(sin respuesta)"}]
        # Este formato quiere un mensaje por cada resultado de herramienta.
        return [{"role": "tool", "tool_call_id": r["id"],
                 "content": json.dumps(r["contenido"], ensure_ascii=False)} for r in m["resultados"]]

    def _peticion(self, metodo: str, ruta: str, cuerpo: dict | None = None) -> dict:
        cabeceras = {"content-type": "application/json"}
        if self._clave:
            cabeceras["authorization"] = f"Bearer {self._clave}"
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        peticion = urllib.request.Request(self._url + ruta, data=datos, headers=cabeceras, method=metodo)
        try:
            with urllib.request.urlopen(peticion, timeout=config.LOCAL_TIMEOUT_S) as respuesta:
                return json.loads(respuesta.read())
        except urllib.error.HTTPError as e:
            detalle = e.read().decode(errors="replace")[:300]
            raise ErrorProveedor(f"El servidor del modelo respondió {e.code}: {detalle}") from None
        # OSError y HTTPException cubren la conexion cortada a media respuesta, que es
        # lo que ocurre cuando el servidor local se queda sin memoria.
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            raise ErrorProveedor(
                f"No hay respuesta del servidor del modelo en {self._url} ({e}). ¿Está arrancado?") from None


# --- Fabrica ---------------------------------------------------------------------

_PROVEEDORES = {"anthropic": ProveedorAnthropic, "local": ProveedorCompatibleOpenAI}


def crear_proveedor(nombre: str = config.PROVEEDOR) -> ProveedorLLM:
    if nombre not in _PROVEEDORES:
        raise ErrorProveedor(f"Proveedor desconocido: {nombre!r}. Disponibles: {', '.join(_PROVEEDORES)}")
    return _PROVEEDORES[nombre]()
