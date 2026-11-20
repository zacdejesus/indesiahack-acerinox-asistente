"""Reglas del dominio del acero inoxidable, como datos y no como prompt.

Aqui vive lo que un tecnico de Acerinox querria poder revisar y corregir:
como se nombra cada grado, que grado puede sustituir a cual y como se
puntua la prioridad comercial.
"""

import re
import unicodedata

# --- Alias de grados ----------------------------------------------------------
# Un cliente puede pedir el mismo acero como "316L", "AISI 316L", "1.4404" (EN)
# o "S31603" (UNS). Todo se traduce al nombre que usa el inventario.

_ALIAS_GRADO = {
    "304": ["304", "1.4301", "S30400", "V2A"],
    "304L": ["304L", "1.4307", "1.4306", "S30403"],
    "316": ["316", "1.4401", "S31600"],
    "316L": ["316L", "1.4404", "S31603", "V4A"],
    "321": ["321", "1.4541", "S32100"],
    "310S": ["310S", "310", "1.4845", "S31008"],  # 310S es el 310 de bajo carbono: lo cumple
    "430": ["430", "1.4016", "S43000"],
    "2205": ["2205", "1.4462", "S32205", "S31803", "DUPLEX", "DUPLEX2205"],
}
_PREFIJOS = ("WERKSTOFF", "W.NR.", "WNR.", "WNR", "AISI", "ASTM", "TYPE", "TIPO", "UNS", "EN", "TP", "SS")


def _sin_tildes(texto) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(texto)) if unicodedata.category(c) != "Mn")


def _clave(texto) -> str:
    """Mayusculas, sin tildes y sin espacios ni separadores: 'aisi 316-l' -> 'AISI316L'."""
    return re.sub(r"[\s\-_]", "", _sin_tildes(texto).upper())


_GRADO_POR_CLAVE = {_clave(a): grado for grado, alias in _ALIAS_GRADO.items() for a in alias}


def normalizar_grado(texto) -> str | None:
    """Devuelve el grado del inventario o None si no se reconoce.

    Acepta dos designaciones juntas, como "316/316L" (doble certificacion) o
    "1.4404 (316L)". Si nombran grados distintos se queda con el de bajo carbono,
    que es el que cumple los dos.
    """
    partes = [p for p in re.split(r"[/()]", str(texto)) if p.strip()]
    if len(partes) > 1:
        grados = {_normalizar_un_grado(p) for p in partes}
        if None in grados:
            return None
        if len(grados) == 1:
            return grados.pop()
        bajos = [g for g in grados if g.endswith("L") and g[:-1] in grados]
        return bajos[0] if len(grados) == 2 and bajos else None
    return _normalizar_un_grado(texto)


def _normalizar_un_grado(texto) -> str | None:
    clave = _clave(texto)
    for _ in range(3):  # quita prefijos encadenados, p. ej. "EN AISI 304"
        if clave in _GRADO_POR_CLAVE:
            return _GRADO_POR_CLAVE[clave]
        for prefijo in _PREFIJOS:
            if clave.startswith(prefijo):
                clave = clave[len(prefijo):]
                break
    return _GRADO_POR_CLAVE.get(clave)


# --- Alias de formato y acabado -----------------------------------------------

_ALIAS_FORMATO = {
    "bobina": ["bobina", "bobinas", "coil", "coils", "rollo"],
    "chapa": ["chapa", "chapas", "plancha", "planchas", "sheet", "sheets", "plate", "plates"],
    "fleje": ["fleje", "flejes", "strip", "strips", "banda"],
    "barra": ["barra", "barras", "barra redonda", "barras redondas", "redondo", "redondos",
              "bar", "bars", "round bar", "round bars", "rod", "rods"],
    "tubo": ["tubo", "tubos", "tuberia", "tubería", "tube", "tubes", "pipe", "pipes"],
}
_ALIAS_ACABADO = {
    "2B": ["2B", "mate"],
    "BA": ["BA", "brillante", "recocido brillante", "bright annealed", "bright", "espejo", "mirror"],
    "N4": ["N4", "NO4", "#4", "satinado", "esmerilado", "pulido", "brushed"],
    "1D": ["1D", "NO1", "#1", "laminado en caliente", "hot rolled"],
}


def _clave_simple(texto) -> str:
    """Minusculas, sin tildes, sin puntos y con un solo espacio: 'No. 4' -> 'no4', 'Tubería' -> 'tuberia'."""
    limpio = _sin_tildes(texto).lower().replace(".", "")
    limpio = re.sub(r"\bno\s+(\d)", r"no\1", limpio)
    return " ".join(limpio.split())


def _invertir(alias: dict) -> dict:
    return {_clave_simple(a): canonico for canonico, lista in alias.items() for a in lista}


_FORMATO = _invertir(_ALIAS_FORMATO)
_ACABADO = _invertir(_ALIAS_ACABADO)


def normalizar_formato(texto) -> str | None:
    return _FORMATO.get(_clave_simple(texto))


def normalizar_acabado(texto) -> str | None:
    return _ACABADO.get(_clave_simple(texto))


# Formatos planos: se pueden cortar a un ancho menor. Barras y tubos no.
FORMATOS_PLANOS = {"bobina", "chapa", "fleje"}

# --- Sustituciones de grado ---------------------------------------------------
# Solo "hacia arriba": un sustituto debe cumplir o superar al grado pedido.
# Nunca se ofrece 304 a quien pidio 316L. Cada sustitucion lleva su porque,
# que es lo que el asistente le cuenta al comercial.

SUSTITUTOS = {
    "304": [
        ("304L", "Misma resistencia a la corrosión y menos carbono: mejor soldabilidad."),
        ("316L", "Superior en corrosión por su molibdeno; es más caro."),
    ],
    "304L": [
        ("316L", "Superior en corrosión por su molibdeno y también de bajo carbono; es más caro."),
    ],
    "316": [
        ("316L", "Misma resistencia a la corrosión y menos carbono: mejor soldabilidad. Sustitución habitual."),
        ("2205", "Dúplex: superior frente a cloruros y el doble de resistencia mecánica; se conforma peor."),
    ],
    "316L": [
        ("2205", "Dúplex: superior frente a cloruros y el doble de resistencia mecánica; se conforma peor."),
    ],
    "321": [
        ("310S", "Aguanta temperaturas más altas; es más caro."),
    ],
    "430": [
        ("304", "Austenítico: mejor corrosión y conformado; no es magnético y es más caro."),
    ],
    "310S": [],
    "2205": [],
}

# --- Prioridad comercial ------------------------------------------------------
# Entre productos tecnicamente validos, conviene vender antes el que lleva mas
# tiempo parado, inmoviliza mas dinero, deja mas margen y esta mas sobrado
# respecto a la demanda. Cada factor se lleva a una escala 0-1 con estos topes.

PESOS_PRIORIDAD = {"rotacion": 0.40, "valor_stock": 0.25, "margen": 0.20, "cobertura": 0.15}
TOPE_ROTACION_DIAS = 365      # un año parado o mas = 1.0
TOPE_VALOR_STOCK_EUR = 100_000
RANGO_MARGEN_PCT = (8, 22)
TOPE_COBERTURA_MESES = 6      # stock para 6 meses de demanda o mas = 1.0
