"""Las herramientas que el modelo puede llamar.

Cada herramienta es una funcion normal de Python (recibe el inventario y unos
argumentos, devuelve un diccionario) mas una descripcion en JSON Schema, que es
lo unico que ve el modelo. REGISTRO une las dos cosas.
"""

from . import dominio
from .datos import Inventario
from .roles import COMERCIAL, Rol

MAX_RESULTADOS = 8
TOLERANCIA_DIAMETRO_MM = 1.0


class ErrorHerramienta(Exception):
    """Argumentos invalidos. El mensaje se le devuelve al modelo para que se corrija."""


# --- Utilidades ---------------------------------------------------------------

# Columnas que ve el modelo. El margen se queda fuera a proposito: es un dato
# interno y la respuesta puede acabar en un correo al cliente.
_COLUMNAS_VISIBLES = ("id", "grado", "formato", "acabado", "espesor_mm", "ancho_mm", "largo_mm",
                      "stock_kg", "almacen", "precio_eur_kg", "rotacion_dias", "norma")


def _visible(producto: dict, **extra) -> dict:
    return {c: producto[c] for c in _COLUMNAS_VISIBLES} | extra


def _numero(nombre: str, valor):
    if valor is None:
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        raise ErrorHerramienta(f"{nombre} debe ser un número, no {valor!r}") from None
    if numero <= 0:
        raise ErrorHerramienta(f"{nombre} debe ser mayor que 0")
    return numero


def _grado(valor) -> str | None:
    if valor is None:
        return None
    grado = dominio.normalizar_grado(valor)
    if grado is None:
        raise ErrorHerramienta(
            f"Grado no reconocido: {valor!r}. Grados en inventario: {', '.join(dominio.SUSTITUTOS)}")
    return grado


def _formato(valor) -> str | None:
    if valor is None:
        return None
    formato = dominio.normalizar_formato(valor)
    if formato is None:
        raise ErrorHerramienta(f"Formato no reconocido: {valor!r}. Válidos: bobina, chapa, fleje, barra, tubo")
    return formato


def _acabado(valor) -> str | None:
    if valor is None:
        return None
    acabado = dominio.normalizar_acabado(valor)
    if acabado is None:
        raise ErrorHerramienta(f"Acabado no reconocido: {valor!r}. Válidos: 2B, BA, N4, 1D")
    return acabado


def _almacen(inv: Inventario, valor) -> str | None:
    if valor is None:
        return None
    almacenes = {a.lower(): a for a in inv.valores("almacen")}
    almacen = almacenes.get(str(valor).strip().lower())
    if almacen is None:
        raise ErrorHerramienta(f"Almacén no reconocido: {valor!r}. Válidos: {', '.join(almacenes.values())}")
    return almacen


def _cumple_ancho(producto: dict, ancho_min) -> bool:
    """Un plano mas ancho vale (se corta). En barra y tubo el diametro tiene que coincidir."""
    if ancho_min is None:
        return True
    if producto["formato"] in dominio.FORMATOS_PLANOS:
        return producto["ancho_mm"] >= ancho_min
    return abs(producto["ancho_mm"] - ancho_min) <= TOLERANCIA_DIAMETRO_MM


def _candidatos(inv: Inventario, ancho_min=None, **filtros) -> list[dict]:
    """Productos con stock que cumplen los filtros, aplicando la regla de ancho."""
    ancho_sql = ancho_min - TOLERANCIA_DIAMETRO_MM if ancho_min is not None else None
    filas = inv.buscar(ancho_min=ancho_sql, **filtros)
    return [f for f in filas if f["stock_kg"] > 0 and _cumple_ancho(f, ancho_min)]


def _ajuste(producto: dict, ancho_min, cantidad_kg) -> tuple[int, list[str]]:
    """Puntuacion 0-100 de lo bien que encaja un producto, con el porque de cada descuento."""
    puntos, notas = 100.0, []
    if ancho_min and producto["formato"] in dominio.FORMATOS_PLANOS and producto["ancho_mm"] > ancho_min:
        exceso = (producto["ancho_mm"] - ancho_min) / ancho_min
        puntos -= min(25, 5 + 20 * exceso)
        notas.append(f"requiere corte de ancho: de {producto['ancho_mm']:g} a {ancho_min:g} mm")
    if cantidad_kg and producto["stock_kg"] < cantidad_kg:
        cubierto = producto["stock_kg"] / cantidad_kg
        puntos -= 50 * (1 - cubierto)
        notas.append(f"stock parcial: cubre el {cubierto:.0%} de los {cantidad_kg:g} kg pedidos")
    return round(puntos), notas or ["cumple todos los requisitos"]


# --- Herramienta 1: buscar_stock ----------------------------------------------

def buscar_stock(inv: Inventario, grado=None, formato=None, acabado=None, espesor_min=None,
                 espesor_max=None, ancho_min=None, almacen=None, cantidad_kg=None) -> dict:
    filtros = {
        "grado": _grado(grado), "formato": _formato(formato), "acabado": _acabado(acabado),
        "espesor_min": _numero("espesor_min", espesor_min), "espesor_max": _numero("espesor_max", espesor_max),
        "ancho_min": _numero("ancho_min", ancho_min), "almacen": _almacen(inv, almacen),
        "cantidad_kg": _numero("cantidad_kg", cantidad_kg),
    }
    filtros = {k: v for k, v in filtros.items() if v is not None}

    encontrados = _candidatos(
        inv, ancho_min=filtros.get("ancho_min"),
        grados=[filtros["grado"]] if "grado" in filtros else None,
        formato=filtros.get("formato"), acabado=filtros.get("acabado"),
        espesor_min=filtros.get("espesor_min"), espesor_max=filtros.get("espesor_max"),
        almacen=filtros.get("almacen"))

    productos = []
    for p in encontrados:
        puntos, notas = _ajuste(p, filtros.get("ancho_min"), filtros.get("cantidad_kg"))
        productos.append(_visible(p, ajuste=puntos, notas=notas))
    # A igual ajuste, primero el que lleva mas tiempo parado en el almacen.
    productos.sort(key=lambda p: (-p["ajuste"], -p["rotacion_dias"]))

    resultado = {
        "filtros_aplicados": filtros,
        "total_con_stock": len(productos),
        "stock_total_kg": round(sum(p["stock_kg"] for p in productos), 1),
        "productos": productos[:MAX_RESULTADOS],
    }
    if not productos:
        resultado["aviso"] = "Sin stock con estos filtros. Usa `alternativas` con los mismos criterios."
    elif len(productos) > MAX_RESULTADOS:
        resultado["aviso"] = f"Se muestran los {MAX_RESULTADOS} mejores de {len(productos)}. Añade filtros para afinar."
    return resultado


# --- Herramienta 2: ficha_tecnica ---------------------------------------------

def ficha_tecnica(inv: Inventario, grado) -> dict:
    grado = _grado(grado)
    return {"grado": grado, "ficha": inv.ficha(grado) or "No hay ficha técnica para este grado."}


# --- Herramienta 3: alternativas ----------------------------------------------

def alternativas(inv: Inventario, producto_id=None, grado=None, formato=None, acabado=None,
                 espesor_mm=None, ancho_min=None, almacen=None) -> dict:
    """Relaja los criterios de uno en uno, del cambio menos molesto al mas molesto."""
    if producto_id is not None:
        encontrados = inv.por_ids([producto_id])
        if not encontrados:
            raise ErrorHerramienta(f"No existe el producto {producto_id!r} en el inventario.")
        ref = encontrados[0]
        grado, formato, acabado = ref["grado"], ref["formato"], ref["acabado"]
        espesor_mm, almacen = ref["espesor_mm"], ref["almacen"]
        ancho_min = ancho_min if ancho_min is not None else ref["ancho_mm"]
    grado, formato, acabado = _grado(grado), _formato(formato), _acabado(acabado)
    espesor_mm, ancho_min = _numero("espesor_mm", espesor_mm), _numero("ancho_min", ancho_min)
    almacen = _almacen(inv, almacen)
    if grado is None or formato is None:
        raise ErrorHerramienta("Hacen falta al menos grado y formato (o un producto_id).")

    base = dict(formato=formato, acabado=acabado, espesor_min=espesor_mm, espesor_max=espesor_mm)
    propuestas, vistos = [], {producto_id}

    def proponer(tipo, nota, filas):
        # Primero los del almacen pedido, luego los de mas stock.
        filas.sort(key=lambda f: (f["almacen"] != almacen, -f["stock_kg"]))
        for f in [f for f in filas if f["id"] not in vistos][:3]:
            vistos.add(f["id"])
            propuestas.append(_visible(f, tipo=tipo, nota=nota))

    # 1. Mismo producto en otro almacen: el cliente recibe exactamente lo que pidio.
    if almacen is not None:
        filas = [f for f in _candidatos(inv, ancho_min, grados=[grado], **base) if f["almacen"] != almacen]
        proponer("otro_almacen", "Mismo material en otro almacén: solo cambia el plazo de transporte.", filas)

    # 2. Grado equivalente o superior.
    for sustituto, porque in dominio.SUSTITUTOS[grado]:
        proponer("grado_equivalente", f"{sustituto} en lugar de {grado}. {porque}",
                 _candidatos(inv, ancho_min, grados=[sustituto], **base))

    # 3. Espesor cercano (hasta un 25 % de diferencia). Solo en planos, y en tubos si
    # se conoce el diametro: en una barra "otro espesor" es otro diametro y no sirve.
    admite_otro_espesor = formato in dominio.FORMATOS_PLANOS or (formato == "tubo" and ancho_min is not None)
    if espesor_mm is not None and admite_otro_espesor:
        cerca = dict(base, espesor_min=espesor_mm * 0.75, espesor_max=espesor_mm * 1.25)
        filas = [f for f in _candidatos(inv, ancho_min, grados=[grado], **cerca) if f["espesor_mm"] != espesor_mm]
        filas.sort(key=lambda f: abs(f["espesor_mm"] - espesor_mm))
        proponer("espesor_cercano", "Espesor distinto al pedido: hay que confirmarlo con el cliente.", filas[:3])

    # 4. Otro acabado superficial.
    if acabado is not None:
        filas = [f for f in _candidatos(inv, ancho_min, grados=[grado], **dict(base, acabado=None))
                 if f["acabado"] != acabado]
        proponer("otro_acabado", "Acabado superficial distinto: hay que confirmarlo con el cliente.", filas)

    referencia = {k: v for k, v in dict(grado=grado, formato=formato, acabado=acabado, espesor_mm=espesor_mm,
                                        ancho_min=ancho_min, almacen=almacen).items() if v is not None}
    resultado = {"referencia": referencia, "alternativas": propuestas[:MAX_RESULTADOS]}
    if not propuestas:
        resultado["aviso"] = "No hay alternativas con stock. Dilo con claridad; no propongas productos."
    return resultado


# --- Herramienta 4: prioridad_comercial ---------------------------------------

def _escala(valor: float, tope: float, base: float = 0.0) -> float:
    return max(0.0, min(1.0, (valor - base) / (tope - base)))


def prioridad_comercial(inv: Inventario, lista_ids) -> dict:
    if not isinstance(lista_ids, list) or not lista_ids:
        raise ErrorHerramienta("lista_ids debe ser una lista de ids de producto.")
    productos = inv.por_ids(lista_ids)
    desconocidos = [i for i in lista_ids if i not in {p["id"] for p in productos}]

    ordenados = []
    for p in productos:
        demanda = inv.demanda_media_mensual_kg(p["grado"], p["formato"])
        cobertura = inv.stock_total_kg(p["grado"], p["formato"]) / demanda if demanda else None
        valor = p["stock_kg"] * p["precio_eur_kg"]
        factores = {
            "rotacion": _escala(p["rotacion_dias"], dominio.TOPE_ROTACION_DIAS),
            "valor_stock": _escala(valor, dominio.TOPE_VALOR_STOCK_EUR),
            "margen": _escala(p["margen_pct"], dominio.RANGO_MARGEN_PCT[1], dominio.RANGO_MARGEN_PCT[0]),
            "cobertura": _escala(cobertura or 0, dominio.TOPE_COBERTURA_MESES),
        }
        puntos = sum(dominio.PESOS_PRIORIDAD[f] * v for f, v in factores.items())

        motivos = []
        if factores["rotacion"] >= 0.5:
            motivos.append(f"lleva {p['rotacion_dias']} días de rotación: conviene darle salida")
        if factores["valor_stock"] >= 0.5:
            motivos.append(f"inmoviliza {valor:,.0f} € en stock".replace(",", "."))
        if factores["margen"] >= 0.6:
            motivos.append("margen por encima de la media")
        if cobertura is not None and cobertura >= 3:
            motivos.append(f"hay stock de {p['grado']} en {p['formato']} para {cobertura:.1f} meses de demanda")
        ordenados.append(_visible(
            p, prioridad=round(100 * puntos), valor_stock_eur=round(valor),
            cobertura_meses=round(cobertura, 1) if cobertura is not None else None,
            motivos=motivos or ["sin urgencia comercial especial"]))

    ordenados.sort(key=lambda p: -p["prioridad"])
    resultado = {"productos": ordenados}
    if desconocidos:
        resultado["ids_desconocidos"] = desconocidos
    return resultado


# --- Registro -----------------------------------------------------------------

_NUM = {"type": "number"}
_TXT = {"type": "string"}

REGISTRO = [
    {
        "nombre": "buscar_stock",
        "funcion": buscar_stock,
        "descripcion": (
            "Busca productos con stock en el inventario. Todos los filtros son opcionales y se combinan. "
            "Devuelve los mejores candidatos con una puntuación de ajuste (0-100) y notas que explican "
            "cada descuento (corte necesario, stock parcial). Medidas siempre en milímetros y kilos. "
            "En barras, espesor y ancho son el diámetro. En tubos, espesor es la pared y ancho el diámetro exterior. "
            "Acepta el grado en cualquier designación (316L, AISI 316L, 1.4404, S31603). "
            "Llámala una vez por cada línea de material que pida el cliente."),
        "parametros": {
            "type": "object",
            "properties": {
                "grado": {**_TXT, "description": "Grado del acero, p. ej. 304, 316L o 1.4404"},
                "formato": {**_TXT, "description": "bobina, chapa, fleje, barra o tubo"},
                "acabado": {**_TXT, "description": "2B, BA, N4 o 1D"},
                "espesor_min": {**_NUM, "description": "Espesor mínimo en mm"},
                "espesor_max": {**_NUM, "description": "Espesor máximo en mm. Para un espesor exacto, igual que espesor_min"},
                "ancho_min": {**_NUM, "description": "Ancho que necesita el cliente en mm. Un plano más ancho se puede cortar"},
                "almacen": {**_TXT, "description": "Ciudad del almacén, si el cliente lo exige"},
                "cantidad_kg": {**_NUM, "description": "Cantidad que pide el cliente en kg"},
            },
        },
    },
    {
        "nombre": "ficha_tecnica",
        "funcion": ficha_tecnica,
        "descripcion": (
            "Devuelve la ficha técnica de un grado: composición, resistencia a la corrosión, usos típicos y "
            "cuándo no usarlo. Úsala para elegir grado cuando el cliente describe una aplicación en vez de "
            "un grado, y para justificar técnicamente una recomendación o una sustitución."),
        "parametros": {
            "type": "object",
            "properties": {"grado": {**_TXT, "description": "Grado del acero"}},
            "required": ["grado"],
        },
    },
    {
        "nombre": "alternativas",
        "funcion": alternativas,
        "descripcion": (
            "Propone alternativas con stock cuando lo pedido no está disponible o no alcanza: el mismo material "
            "en otro almacén, un grado equivalente o superior, un espesor cercano u otro acabado. Cada alternativa "
            "trae su tipo y una nota con lo que cambia. Se le puede dar el id de un producto agotado o los criterios."),
        "parametros": {
            "type": "object",
            "properties": {
                "producto_id": {**_TXT, "description": "Id de un producto del inventario que sirve de referencia"},
                "grado": _TXT, "formato": _TXT, "acabado": _TXT,
                "espesor_mm": {**_NUM, "description": "Espesor pedido en mm"},
                "ancho_min": {**_NUM, "description": "Ancho que necesita el cliente en mm"},
                "almacen": {**_TXT, "description": "Almacén donde se pidió originalmente"},
            },
        },
    },
    {
        "nombre": "prioridad_comercial",
        "funcion": prioridad_comercial,
        "descripcion": (
            "Ordena productos por interés comercial para Acerinox: rotación lenta, valor de stock inmovilizado, "
            "margen y meses de demanda cubiertos. Úsala cuando haya varios productos técnicamente válidos, para "
            "recomendar primero el que más conviene vender. Devuelve la prioridad (0-100) y los motivos."),
        "parametros": {
            "type": "object",
            "properties": {"lista_ids": {"type": "array", "items": _TXT, "description": "Ids de producto a ordenar"}},
            "required": ["lista_ids"],
        },
    },
]

_POR_NOMBRE = {h["nombre"]: h for h in REGISTRO}


def esquemas(rol: Rol = COMERCIAL) -> list[dict]:
    """Lo que se le enseña al modelo: nombre, descripcion y parametros de las herramientas del rol."""
    return [{k: h[k] for k in ("nombre", "descripcion", "parametros")} for h in REGISTRO if rol.permite(h["nombre"])]


def ejecutar(inv: Inventario, nombre: str, argumentos: dict, rol: Rol = COMERCIAL) -> tuple[dict, bool]:
    """Ejecuta una herramienta. Devuelve (resultado, es_error) y nunca lanza por culpa del modelo.

    El resultado sale ya filtrado por el rol: lo que el rol no puede ver no llega al modelo.
    """
    herramienta = _POR_NOMBRE.get(nombre)
    if herramienta is None or not rol.permite(nombre):
        # Misma respuesta para una herramienta inexistente que para una vetada:
        # al modelo del cliente no se le confirma que existan herramientas internas.
        disponibles = [n for n in _POR_NOMBRE if rol.permite(n)]
        return {"error": f"No existe la herramienta {nombre!r}. Disponibles: {', '.join(disponibles)}"}, True
    validos = herramienta["parametros"]["properties"]
    sobran = [a for a in argumentos if a not in validos]
    faltan = [a for a in herramienta["parametros"].get("required", []) if a not in argumentos]
    if sobran or faltan:
        return {"error": f"Argumentos incorrectos para {nombre}. Sobran: {sobran}. Faltan: {faltan}. "
                         f"Válidos: {', '.join(validos)}"}, True
    try:
        return rol.filtrar(herramienta["funcion"](inv, **argumentos)), False
    except ErrorHerramienta as e:
        return {"error": str(e)}, True
