"""Avisos de reposicion: se guardan los criterios de una busqueda sin stock y se
vuelven a comprobar con la misma herramienta buscar_stock, sin modelo.
"""

from asistente import herramientas
from asistente.agente import Respuesta
from asistente.datos import Inventario

from .almacen import AlmacenApp

_ETIQUETAS = {
    "grado": "{}", "formato": "{}", "acabado": "acabado {}", "espesor_min": "espesor desde {:g} mm",
    "espesor_max": "hasta {:g} mm", "ancho_min": "ancho {:g} mm", "almacen": "en {}",
    "cantidad_kg": "{:g} kg",
}


def describir(criterios: dict) -> str:
    """Texto corto para la interfaz y las notificaciones: '316 chapa acabado 2B 2 mm en Madrid'."""
    partes = []
    for clave, valor in criterios.items():
        if clave not in _ETIQUETAS:
            continue
        if clave == "espesor_min" and valor == criterios.get("espesor_max"):
            partes.append(f"{valor:g} mm")   # espesor exacto: una sola cifra
        elif clave == "espesor_max" and valor == criterios.get("espesor_min"):
            continue
        else:
            partes.append(_ETIQUETAS[clave].format(valor))
    return " ".join(partes) or "cualquier material"


def busquedas_sin_stock(respuesta: Respuesta) -> list[dict]:
    """Criterios de cada buscar_stock de esta respuesta que no encontro nada."""
    vistos, salida = [], []
    for paso in respuesta.traza:
        r = paso.resultado
        if paso.herramienta == "buscar_stock" and not paso.error and r.get("total_con_stock") == 0:
            criterios = r.get("filtros_aplicados", {})
            if criterios and criterios not in vistos:
                vistos.append(criterios)
                salida.append(criterios)
    return salida


def hay_stock(inv: Inventario, criterios: dict) -> bool:
    """Misma comprobacion que haria el asistente: buscar_stock con los criterios guardados."""
    r = herramientas.buscar_stock(inv, **criterios)
    if r["total_con_stock"] == 0:
        return False
    cantidad = criterios.get("cantidad_kg")
    return cantidad is None or r["stock_total_kg"] >= cantidad


def comprobar(almacen: AlmacenApp, inv: Inventario) -> list[dict]:
    """Marca como cumplidos los avisos que ya tienen stock y notifica a su usuario."""
    cumplidos = []
    for aviso in almacen.avisos(pendientes=True):
        try:
            disponible = hay_stock(inv, aviso["criterios"])
        except herramientas.ErrorHerramienta:
            continue  # criterios que ya no valen (p. ej. un almacen cerrado): se dejan como estan
        if disponible:
            almacen.cumplir_aviso(aviso["id"])
            almacen.notificar(aviso["usuario"], "Vuelve a haber stock",
                              f"Ya hay stock de {describir(aviso['criterios'])}.")
            cumplidos.append(aviso)
    return cumplidos
