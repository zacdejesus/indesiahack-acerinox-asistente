"""Interfaz Streamlit del asistente de inventario.

Uso:  streamlit run app.py

A la izquierda, el chat. A la derecha, el razonamiento de la ultima respuesta:
que herramientas se llamaron, que productos se recomiendan (leidos de la base
de datos) y que dijo el verificador. La barra lateral lleva los ejemplos de la demo.
"""

import sqlite3

import pandas as pd
import streamlit as st

from asistente.agente import Asistente, Conversacion, Paso, Respuesta
from asistente.datos import crear_inventario
from asistente.proveedor import ErrorProveedor, crear_proveedor

# Preguntas con resultado predecible (ver ESCENARIOS_DEMO en generar_datos.py).
EJEMPLOS = [
    ("Chapa 316 agotada en Madrid",
     "Necesito 2.000 kg de chapa 316 2B de 2 mm, 1500 x 3000, en el almacén de Madrid."),
    ("Barandilla en primera línea de playa",
     "Un cliente va a fabricar una barandilla exterior en primera línea de playa. "
     "No sabe qué grado pedir. ¿Qué tubo le ofrezco?"),
    ("Chapa para un horno industrial",
     "Chapa de 3 mm para el interior de un horno industrial que trabaja a unos 1.000 °C. ¿Qué tenemos?"),
    ("Stock parado: 430 BA",
     "Un fabricante de electrodomésticos busca chapa 430 BA fina, entre 0,5 y 0,8 mm. "
     "¿Qué nos interesa más venderle?"),
    ("Correo de un cliente (en inglés)",
     "Me ha llegado este correo de un cliente. ¿Qué le ofrezco?\n\n"
     "Hello,\n\n"
     "Please quote the following items:\n\n"
     "1. Sheet EN 1.4404, 2B finish, 2 mm x 1500 x 3000 mm, 3 tonnes\n"
     "2. Coil EN 1.4301, 0.06 in thick, minimum width 48 in, 10 tonnes\n"
     "3. Welded tube AISI 316L, 4-1/2 in outside diameter, about 0.11 in wall, 800 kg\n\n"
     "Delivery as soon as possible.\n\n"
     "Best regards,\n"
     "Purchasing Department"),
]

NOMBRES_HERRAMIENTA = {
    "buscar_stock": "Buscar stock",
    "ficha_tecnica": "Consultar ficha técnica",
    "alternativas": "Buscar alternativas",
    "prioridad_comercial": "Ordenar por prioridad comercial",
}

TIPOS_ALTERNATIVA = {
    "otro_almacen": "otro almacén",
    "grado_equivalente": "grado equivalente o superior",
    "espesor_cercano": "espesor cercano",
    "otro_acabado": "otro acabado",
}

# Columnas de la tabla de productos. El margen se queda fuera, igual que en las
# herramientas: es un dato interno y la pantalla se enseña en la demo.
COLUMNAS_TABLA = {
    "id": "Id", "grado": "Grado", "formato": "Formato", "acabado": "Acabado",
    "espesor_mm": "Espesor (mm)", "ancho_mm": "Ancho (mm)", "largo_mm": "Largo (mm)",
    "stock_kg": "Stock (kg)", "almacen": "Almacén", "precio_eur_kg": "Precio (€/kg)",
    "rotacion_dias": "Rotación (días)",
}


# --- Estado ---------------------------------------------------------------------

@st.cache_resource
def crear_asistente() -> Asistente:
    """Un solo asistente para toda la app: no guarda estado, la conversacion va en la sesion."""
    return Asistente(crear_proveedor(), crear_inventario())


def obtener_asistente() -> Asistente:
    # Los tests dejan en la sesion un asistente con un proveedor falso.
    return st.session_state.get("asistente") or crear_asistente()


def nueva_conversacion() -> None:
    st.session_state.conversacion = Conversacion()
    st.session_state.historial = []   # lo que se pinta: {"rol", "texto", "respuesta" | "error"}
    st.session_state.pendiente = None


def lanzar(pregunta: str) -> None:
    """Deja una pregunta en cola; se responde en esta misma ejecucion, al pintar el chat."""
    st.session_state.pendiente = pregunta


def responder(pregunta: str) -> dict:
    """Llama al asistente y devuelve el mensaje que se guarda en el historial."""
    conv: Conversacion = st.session_state.conversacion
    n_mensajes, evidencia = len(conv.mensajes), set(conv.evidencia)
    try:
        respuesta = obtener_asistente().responder(pregunta, conv)
        return {"rol": "assistant", "texto": respuesta.texto, "respuesta": respuesta}
    except ErrorProveedor as e:
        error = str(e)
    except Exception as e:  # en directo es mejor un aviso que una pantalla de error
        error = f"Error inesperado: {e}"
    # Si la llamada falla a medias, la conversacion vuelve a como estaba: asi la
    # pregunta fallida no se le envia dos veces al modelo cuando se reintenta.
    del conv.mensajes[n_mensajes:]
    conv.evidencia = evidencia
    return {"rol": "assistant", "texto": "", "error": error}


# --- Formato --------------------------------------------------------------------

def numero(valor, decimales: int = 0) -> str:
    """Numero con formato español: 1.234,5"""
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def texto_seguro(texto: str) -> str:
    # Streamlit interpreta $...$ como una formula; en un correo con precios lo estropearia.
    return texto.replace("$", "\\$")


def lista_de_filtros(filtros: dict) -> str:
    return " · ".join(f"{clave}: **{valor}**" for clave, valor in filtros.items()) or "sin filtros"


def resumen_resultado(paso: Paso) -> str:
    """Una linea con lo que devolvio la herramienta."""
    r = paso.resultado
    if paso.herramienta == "buscar_stock":
        return f"{r['total_con_stock']} productos con stock ({numero(r['stock_total_kg'])} kg en total)"
    if paso.herramienta == "alternativas":
        tipos = dict.fromkeys(TIPOS_ALTERNATIVA.get(a["tipo"], a["tipo"]) for a in r["alternativas"])
        return f"{len(r['alternativas'])} alternativas con stock" + (f": {', '.join(tipos)}" if tipos else "")
    if paso.herramienta == "ficha_tecnica":
        return f"Ficha técnica del grado {r['grado']}"
    if paso.herramienta == "prioridad_comercial":
        return "Orden: " + ", ".join(f"{p['id']} ({p['prioridad']}/100)" for p in r["productos"])
    return ""


def detalles_por_producto(respuestas: list[Respuesta]) -> dict[str, dict]:
    """Lo que las herramientas dijeron de cada producto: ajuste, tipo de alternativa y prioridad.

    Se recorren las trazas de toda la conversacion porque una repregunta puede
    citar un producto que se busco en un turno anterior.
    """
    detalles: dict[str, dict] = {}
    for respuesta in respuestas:
        for paso in respuesta.traza:
            if paso.error:
                continue
            filas = paso.resultado.get("productos", []) + paso.resultado.get("alternativas", [])
            for fila in filas:
                claves = ("ajuste", "notas", "tipo", "nota", "prioridad", "motivos")
                detalles.setdefault(fila["id"], {}).update({c: fila[c] for c in claves if c in fila})
    return detalles


def lineas_de_detalle(d: dict) -> list[str]:
    lineas = []
    if "tipo" in d:
        lineas.append(f"Alternativa ({TIPOS_ALTERNATIVA.get(d['tipo'], d['tipo'])}): {d['nota']}")
    if "ajuste" in d:
        lineas.append(f"Ajuste a lo pedido {d['ajuste']}/100: {'; '.join(d['notas'])}")
    if "prioridad" in d:
        lineas.append(f"Prioridad comercial {d['prioridad']}/100: {'; '.join(d['motivos'])}")
    return lineas


# --- Piezas de la pantalla --------------------------------------------------------

def pintar_barra_lateral() -> None:
    with st.sidebar:
        st.header("Ejemplos para la demo")
        for i, (etiqueta, pregunta) in enumerate(EJEMPLOS):
            st.button(etiqueta, key=f"ejemplo_{i}", on_click=lanzar, args=(pregunta,),
                      help=pregunta, use_container_width=True)
        st.divider()
        st.button("Nueva conversación", key="nueva", on_click=nueva_conversacion,
                  type="primary", use_container_width=True)
        st.caption("Todo producto citado se comprueba contra el inventario antes de mostrarse.")


def pintar_mensaje(mensaje: dict) -> None:
    with st.chat_message(mensaje["rol"]):
        if mensaje.get("error"):
            st.error(mensaje["error"])
        else:
            st.markdown(texto_seguro(mensaje["texto"]))


def pintar_chat(pregunta: str | None) -> None:
    historial = st.session_state.historial
    if not historial and not pregunta:
        st.info("Escribe una consulta, pega el correo de un cliente o pulsa un ejemplo de la barra lateral.")
    for mensaje in historial:
        pintar_mensaje(mensaje)
    if not pregunta:
        return
    # La pregunta se pinta antes de llamar al modelo para que se vea mientras piensa.
    historial.append({"rol": "user", "texto": pregunta})
    pintar_mensaje(historial[-1])
    with st.spinner("Consultando el inventario..."):
        historial.append(responder(pregunta))
    pintar_mensaje(historial[-1])


def pintar_paso(n: int, paso: Paso) -> None:
    nombre = NOMBRES_HERRAMIENTA.get(paso.herramienta, paso.herramienta)
    with st.container(border=True):
        st.markdown(f"**{n}. {nombre}** · `{paso.herramienta}` · {paso.ms} ms")
        if paso.error:
            st.markdown("Argumentos: " + lista_de_filtros(paso.argumentos))
            st.warning(f"Llamada rechazada: {paso.resultado.get('error', 'error desconocido')} "
                       "El error se devolvió al modelo para que se corrigiera.")
            return
        # Se enseñan los filtros ya normalizados (1.4404 pasa a 316L), no lo que escribio el modelo.
        filtros = paso.resultado.get("filtros_aplicados") or paso.resultado.get("referencia") or paso.argumentos
        st.markdown("Filtros: " + lista_de_filtros(filtros))
        st.markdown("Resultado: " + resumen_resultado(paso))
        if paso.resultado.get("aviso"):
            st.caption(paso.resultado["aviso"])
        with st.expander("Ver la llamada completa"):
            st.json({"argumentos": paso.argumentos, "resultado": paso.resultado}, expanded=1)


def pintar_verificador(respuesta: Respuesta) -> None:
    citados = respuesta.validacion.citados
    ids = ", ".join(citados)
    if not respuesta.verificada:
        st.warning("Texto sustituido: la respuesta del modelo citaba productos que el verificador no pudo "
                   "dar por buenos, así que se muestra un texto construido solo con los resultados de las "
                   f"herramientas. Ids citados: {ids or 'ninguno'}.")
    elif citados:
        st.success(f"Respuesta verificada: los ids citados ({ids}) existen en el inventario "
                   "y los devolvió una herramienta en esta conversación.")
    else:
        st.success("Respuesta verificada: no cita ningún producto.")
    # El momento mas vistoso del verificador: enseñar que cazo algo.
    if respuesta.rechazados:
        rechazados = ", ".join(respuesta.rechazados)
        if respuesta.verificada:
            st.info(f"El verificador rechazó un primer borrador que citaba {rechazados}; "
                    "el modelo lo reescribió y la versión final pasó la comprobación.")
        else:
            st.info(f"Ids rechazados por el verificador: {rechazados}.")


def pintar_productos(respuesta: Respuesta, detalles: dict[str, dict]) -> None:
    if not respuesta.productos:
        st.caption("La respuesta no recomienda ningún producto.")
        return
    tabla = pd.DataFrame(respuesta.productos)[list(COLUMNAS_TABLA)].rename(columns=COLUMNAS_TABLA)
    st.dataframe(tabla, hide_index=True, column_config={
        "Espesor (mm)": st.column_config.NumberColumn(format="%.2f"),
        "Ancho (mm)": st.column_config.NumberColumn(format="%.1f"),
        "Largo (mm)": st.column_config.NumberColumn(format="%d"),
        "Stock (kg)": st.column_config.NumberColumn(format="%.0f"),
        "Precio (€/kg)": st.column_config.NumberColumn(format="%.2f"),
    })
    for producto in respuesta.productos:
        lineas = lineas_de_detalle(detalles.get(producto["id"], {}))
        if lineas:
            st.markdown(f"**{producto['id']}**\n" + "\n".join(f"- {linea}" for linea in lineas))


def pintar_razonamiento() -> None:
    st.subheader("Cómo se ha llegado a la respuesta")
    respuestas = [m["respuesta"] for m in st.session_state.historial if m.get("respuesta")]
    ultimo = st.session_state.historial[-1] if st.session_state.historial else {}
    if not ultimo.get("respuesta"):
        st.caption("Aquí aparecerán los pasos, los productos recomendados y el resultado del "
                   "verificador de la última respuesta.")
        return
    respuesta: Respuesta = ultimo["respuesta"]

    st.caption(f"{numero(respuesta.ms / 1000, 1)} s · {respuesta.llamadas_llm} llamadas al modelo · "
               f"{numero(respuesta.tokens_entrada)} tokens de entrada + {numero(respuesta.tokens_salida)} de salida")

    st.markdown("##### Verificador")
    pintar_verificador(respuesta)

    st.markdown("##### Productos recomendados")
    st.caption("Datos leídos del inventario, no del texto del modelo.")
    pintar_productos(respuesta, detalles_por_producto(respuestas))

    st.markdown("##### Pasos")
    if not respuesta.traza:
        st.caption("El modelo respondió sin consultar el inventario.")
    for n, paso in enumerate(respuesta.traza, start=1):
        pintar_paso(n, paso)


# --- Pantalla ---------------------------------------------------------------------

st.set_page_config(page_title="Asistente de inventario · Acerinox", layout="wide")

if "historial" not in st.session_state:
    nueva_conversacion()

st.title("Asistente de inventario comercial")
st.caption("Asesor técnico de acero inoxidable: interpreta la consulta, busca en el inventario "
           "y recomienda solo productos que existen.")

try:
    obtener_asistente()
except ErrorProveedor as e:
    st.error(str(e))
    st.stop()
except sqlite3.Error:
    st.error("No se ha podido abrir el inventario. Genera los datos con `python generar_datos.py`.")
    st.stop()

pintar_barra_lateral()

# La pregunta llega del cuadro de texto o de un boton de ejemplo.
pregunta = st.chat_input("Escribe la consulta o pega el correo del cliente") or st.session_state.pendiente
st.session_state.pendiente = None

col_chat, col_razonamiento = st.columns([5, 6], gap="large")
with col_chat:
    pintar_chat(pregunta)
with col_razonamiento:
    pintar_razonamiento()
