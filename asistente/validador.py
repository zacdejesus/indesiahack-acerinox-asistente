"""Guardarrail determinista: el modelo no puede citar productos que no ha visto.

"Evidencia" son los ids que las herramientas han devuelto en esta conversacion.
Un id citado en la respuesta tiene que estar en la evidencia. Esto no depende
de que el modelo obedezca al prompt: lo comprueba codigo.
"""

import re
from dataclasses import dataclass, field

from . import config
from .datos import Inventario


@dataclass
class Validacion:
    citados: list[str] = field(default_factory=list)        # ids que aparecen en el texto, en orden
    inventados: list[str] = field(default_factory=list)     # no existen en el inventario
    sin_evidencia: list[str] = field(default_factory=list)  # existen, pero ninguna herramienta los devolvio

    @property
    def ok(self) -> bool:
        return not self.inventados and not self.sin_evidencia


def ids_en_texto(texto: str) -> list[str]:
    return list(dict.fromkeys(re.findall(config.PATRON_ID, texto)))


def ids_en_resultado(resultado) -> set[str]:
    """Recorre el resultado de una herramienta y recoge todos los valores de la clave "id"."""
    if isinstance(resultado, dict):
        ids = {resultado["id"]} if isinstance(resultado.get("id"), str) else set()
        return ids.union(*(ids_en_resultado(v) for v in resultado.values()))
    if isinstance(resultado, list):
        return set().union(*(ids_en_resultado(v) for v in resultado))
    return set()


def validar(texto: str, evidencia: set[str], inv: Inventario) -> Validacion:
    citados = ids_en_texto(texto)
    existentes = {p["id"] for p in inv.por_ids(citados)}
    return Validacion(
        citados=citados,
        inventados=[i for i in citados if i not in existentes],
        sin_evidencia=[i for i in citados if i in existentes and i not in evidencia],
    )
