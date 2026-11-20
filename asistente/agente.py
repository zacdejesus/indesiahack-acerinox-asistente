"""El agente: el bucle que une modelo, herramientas y validador.

    pregunta -> modelo -> (herramientas -> modelo)* -> texto -> validador -> respuesta

El agente no guarda estado. La conversacion viaja en un objeto Conversacion que
guarda quien llama (la interfaz, el script de evaluacion o un test).
"""

import time
from dataclasses import dataclass, field

from . import config, herramientas
from .datos import Inventario
from .proveedor import ErrorProveedor, ProveedorLLM, msg_asistente, msg_herramientas, msg_usuario
from .validador import Validacion, ids_en_resultado, validar

SISTEMA = """\
Eres el asesor técnico del inventario comercial de Acerinox. Trabajas para el equipo comercial: \
te escriben comerciales con una pregunta suelta o pegando el correo de un cliente, y necesitan \
saber qué producto del inventario ofrecer y por qué.

Cómo trabajar
- Todo lo que sabes del inventario viene de las herramientas. Recomienda solo productos que una \
herramienta te haya devuelto en esta conversación y cítalos siempre por su id. Las cifras de stock, \
precio y almacén son las que devuelve la herramienta, sin redondear ni estimar. Un verificador \
automático comprueba cada id de tu respuesta contra el inventario.
- Si el cliente describe una aplicación (ambiente marino, cocina industrial, un horno) en lugar de \
un grado, consulta la ficha técnica para elegir el grado y explicar el motivo.
- Si el mensaje pide varios materiales, trata cada línea por separado.
- Las herramientas trabajan en milímetros y kilos. Si el cliente usa otras unidades, conviértelas \
y di la conversión que has hecho.
- Cuando no haya stock o no alcance la cantidad, busca alternativas y explica qué cambia para el \
cliente. Si tampoco hay alternativas, dilo claramente.
- Cuando varios productos sirvan técnicamente, consulta la prioridad comercial y propón primero el \
que más interesa vender, diciendo el motivo.
- Si falta un dato sin el que no puedes buscar con sentido (por ejemplo, no se sabe ni el grado ni \
el uso), haz una sola pregunta de aclaración. Si puedes avanzar con una suposición razonable, \
avanza y di qué has supuesto.

Cómo responder
- Responde en el idioma en que te escriben, en español por defecto.
- Empieza por la recomendación: producto (id), almacén y stock. Después el porqué técnico en una o \
dos frases y, si la hay, la alternativa. Sé breve: quien lee es un comercial con prisa.
- Si te han pegado el correo de un cliente, añade al final un borrador de respuesta para el \
cliente, en el idioma del cliente.
"""

_AVISO_VERIFICADOR = (
    "[Verificador automático] Tu respuesta cita ids que no puedo dar por buenos: {detalle}. "
    "Reescribe la respuesta usando solo productos devueltos por las herramientas. "
    "Si necesitas comprobar algo, vuelve a buscar.")


@dataclass
class Conversacion:
    """Estado de una conversacion: los mensajes y los ids que las herramientas han devuelto."""
    mensajes: list[dict] = field(default_factory=list)
    evidencia: set[str] = field(default_factory=set)


@dataclass
class Paso:
    """Una llamada a herramienta, para enseñar el razonamiento en la interfaz."""
    herramienta: str
    argumentos: dict
    resultado: dict
    error: bool
    ms: int


@dataclass
class Respuesta:
    texto: str
    productos: list[dict]       # filas del inventario de los productos citados, leidas de la base de datos
    traza: list[Paso]
    validacion: Validacion
    verificada: bool            # False = se sustituyo el texto del modelo por uno seguro
    rechazados: list[str] = field(default_factory=list)  # ids que el verificador no dio por buenos
    reintentada: bool = False   # True = el modelo tuvo que reescribir su respuesta
    llamadas_llm: int = 0
    tokens_entrada: int = 0
    tokens_salida: int = 0
    ms: int = 0


class Asistente:
    def __init__(self, proveedor: ProveedorLLM, inventario: Inventario, max_pasos: int = config.MAX_PASOS):
        self._llm = proveedor
        self._inv = inventario
        self._max_pasos = max_pasos

    def responder(self, pregunta: str, conversacion: Conversacion | None = None) -> Respuesta:
        conv = conversacion if conversacion is not None else Conversacion()
        # Copia del estado: si el proveedor falla a medias, la conversacion vuelve a
        # como estaba y la pregunta se puede reintentar sin quedar duplicada.
        n_mensajes, evidencia = len(conv.mensajes), set(conv.evidencia)
        try:
            return self._responder(pregunta, conv)
        except ErrorProveedor:
            del conv.mensajes[n_mensajes:]
            conv.evidencia = evidencia
            raise

    def _responder(self, pregunta: str, conv: Conversacion) -> Respuesta:
        conv.mensajes.append(msg_usuario(pregunta))
        inicio = time.perf_counter()
        traza: list[Paso] = []
        llamadas_llm = tokens_in = tokens_out = 0
        texto, validacion, reintentado = "", Validacion(), False
        rechazados: list[str] = []

        for _ in range(self._max_pasos):
            r = self._llm.completar(SISTEMA, conv.mensajes, herramientas.esquemas())
            llamadas_llm += 1
            tokens_in += r.tokens_entrada
            tokens_out += r.tokens_salida

            # Caso 1: el modelo pide herramientas. Se ejecutan y se le devuelven los resultados.
            if r.fin == "herramientas" and r.llamadas:
                conv.mensajes.append(msg_asistente(r.texto, r.crudo))
                resultados = []
                for llamada in r.llamadas:
                    t0 = time.perf_counter()
                    resultado, error = herramientas.ejecutar(self._inv, llamada.nombre, llamada.argumentos)
                    traza.append(Paso(llamada.nombre, llamada.argumentos, resultado, error,
                                      round((time.perf_counter() - t0) * 1000)))
                    conv.evidencia |= ids_en_resultado(resultado)
                    resultados.append({"id": llamada.id, "contenido": resultado, "error": error})
                conv.mensajes.append(msg_herramientas(resultados))
                continue

            # Caso 2: respuesta cortada o rechazada. Se guarda solo como texto y se termina.
            if r.fin in ("max_tokens", "rechazo"):
                texto = r.texto or "No he podido completar la respuesta. Reformula la pregunta, por favor."
                conv.mensajes.append(msg_asistente(texto))
                validacion = validar(texto, conv.evidencia, self._inv)
                break

            # Caso 3: respuesta final. Pasa por el validador antes de darla por buena.
            texto = r.texto
            conv.mensajes.append(msg_asistente(texto, r.crudo))
            validacion = validar(texto, conv.evidencia, self._inv)
            if validacion.ok or reintentado:
                break
            reintentado = True  # una sola oportunidad de corregirse
            rechazados += validacion.inventados + validacion.sin_evidencia
            detalle = ", ".join(validacion.inventados + validacion.sin_evidencia)
            conv.mensajes.append(msg_usuario(_AVISO_VERIFICADOR.format(detalle=detalle)))
        else:
            # Se agotaron las vueltas sin respuesta final.
            validacion = Validacion(inventados=["(sin respuesta)"])

        verificada = validacion.ok
        if not verificada:
            rechazados += [i for i in validacion.inventados + validacion.sin_evidencia
                           if i not in rechazados and i != "(sin respuesta)"]
            texto = self._respuesta_segura(traza)
            conv.mensajes.append(msg_asistente(texto))
            validacion = validar(texto, conv.evidencia, self._inv)

        return Respuesta(
            texto=texto, productos=self._inv.por_ids(validacion.citados), traza=traza,
            validacion=validacion, verificada=verificada, rechazados=rechazados,
            reintentada=reintentado, llamadas_llm=llamadas_llm,
            tokens_entrada=tokens_in, tokens_salida=tokens_out,
            ms=round((time.perf_counter() - inicio) * 1000))

    def _respuesta_segura(self, traza: list[Paso]) -> str:
        """Texto construido por codigo, sin modelo, a partir de lo que devolvieron las herramientas."""
        # Un producto de cada busqueda por turnos, para que un correo con varias
        # lineas no enseñe solo los productos de la primera.
        por_paso = [_ids_en_orden(paso.resultado) for paso in traza]
        vistos = []
        for ronda in range(max(map(len, por_paso), default=0)):
            vistos += [ids[ronda] for ids in por_paso if ronda < len(ids) and ids[ronda] not in vistos]
        productos = [p for p in self._inv.por_ids(vistos) if p["stock_kg"] > 0][:5]
        if not productos:
            return ("No he podido generar una respuesta verificada y la búsqueda no ha devuelto "
                    "productos con stock. Reformula la consulta, por favor.")
        lineas = [f"- {p['id']}: {p['grado']} {p['formato']} {p['acabado']}, {p['espesor_mm']:g} mm, "
                  f"{p['stock_kg']:g} kg en {p['almacen']}" for p in productos]
        return ("No he podido verificar la respuesta redactada, así que te muestro directamente los "
                "productos encontrados en el inventario:\n" + "\n".join(lineas))


def _ids_en_orden(resultado) -> list[str]:
    """Como ids_en_resultado, pero conservando el orden en que la herramienta los devolvio."""
    if isinstance(resultado, dict):
        ids = [resultado["id"]] if isinstance(resultado.get("id"), str) else []
        return ids + [i for v in resultado.values() for i in _ids_en_orden(v)]
    if isinstance(resultado, list):
        return [i for v in resultado for i in _ids_en_orden(v)]
    return []
