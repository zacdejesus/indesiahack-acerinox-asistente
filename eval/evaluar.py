"""Evaluacion del asistente contra los casos de eval/preguntas.yaml.

Uso:
  python -m eval.evaluar                          todos los casos
  python -m eval.evaluar --categoria correo       solo una categoria (repetible)
  python -m eval.evaluar --id sin-01 --id pri-03  solo esos casos
  python -m eval.evaluar --limite 5               los 5 primeros
  python -m eval.evaluar --listar                 enseña los casos sin llamar al modelo

El fichero tiene dos mitades separadas a proposito:
  1. Comprobaciones: funciones puras sobre un objeto Respuesta. No llaman a
     ningun modelo, asi que se prueban sin API (tests/test_eval.py).
  2. Ejecucion: lanza cada caso contra el asistente real, calcula las metricas,
     imprime el informe y guarda el detalle en eval/resultados/.
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

from asistente import config, herramientas
from asistente.agente import Asistente, Conversacion, Respuesta
from asistente.datos import Inventario, crear_inventario
from asistente.proveedor import ErrorProveedor, crear_proveedor
from eval.memoria import MonitorServidor, pico_cache_gb, pid_servidor, texto_desde

# Precio en dolares por millon de tokens (por defecto, los de claude-opus-5-5).
PRECIO_ENTRADA_USD_M = 4.0
PRECIO_SALIDA_USD_M = 20.0

RUTA_PREGUNTAS = Path(__file__).parent / "preguntas.yaml"
RUTA_RESULTADOS = Path(__file__).parent / "resultados"

# Con tres errores seguidos del proveedor el problema es la clave o la red, no un caso.
MAX_ERRORES_SEGUIDOS = 3


# === 1. Comprobaciones ==========================================================
# Cada comprobacion recibe el valor esperado (lo que pone el YAML) y la
# Respuesta, y devuelve None si se cumple o una frase con el motivo del fallo.

def cumple(producto: dict, condicion: dict) -> bool:
    """Un producto cumple una condicion si cumple todos sus campos."""
    for campo, esperado in condicion.items():
        valor = producto.get(campo)
        if isinstance(esperado, dict):  # rango numerico
            if valor is None or valor < esperado.get("min", -math.inf) or valor > esperado.get("max", math.inf):
                return False
        else:
            aceptados = esperado if isinstance(esperado, list) else [esperado]
            # Los grados se comparan como texto: en YAML es facil escribir 304 sin comillas.
            if isinstance(valor, str):
                aceptados = [str(a) for a in aceptados]
            if valor not in aceptados:
                return False
    return True


def _texto_condicion(condicion: dict) -> str:
    partes = []
    for campo, esperado in condicion.items():
        if isinstance(esperado, dict):
            esperado = " a ".join(f"{k} {v:g}" for k, v in esperado.items())
        elif isinstance(esperado, list):
            esperado = " o ".join(str(e) for e in esperado)
        partes.append(f"{campo} = {esperado}")
    return "{" + ", ".join(partes) + "}"


def _texto_producto(p: dict) -> str:
    return (f"{p['id']} ({p['grado']} {p['formato']} {p['acabado']}, {p['espesor_mm']:g} x {p['ancho_mm']:g} mm, "
            f"{p['stock_kg']:g} kg, {p['almacen']})")


def _num_productos(esperado: dict, r: Respuesta) -> str | None:
    n = len(r.productos)
    if n < esperado.get("min", 0):
        return f"cita {n} productos y se esperaban al menos {esperado['min']}"
    if n > esperado.get("max", math.inf):
        citados = ", ".join(p["id"] for p in r.productos)
        return f"cita {n} productos ({citados}) y se esperaban como mucho {esperado['max']}"
    return None


def _todos(esperado, r: Respuesta) -> str | None:
    condiciones = esperado if isinstance(esperado, list) else [esperado]
    fuera = [p for p in r.productos if not any(cumple(p, c) for c in condiciones)]
    if fuera:
        return (f"cita productos que no debía: {'; '.join(_texto_producto(p) for p in fuera)}. "
                f"Se esperaba {' o '.join(_texto_condicion(c) for c in condiciones)}")
    return None


def _alguno(esperado: list, r: Respuesta) -> str | None:
    faltan = [c for c in esperado if not any(cumple(p, c) for p in r.productos)]
    if faltan:
        return f"ningún producto citado cumple {' ni '.join(_texto_condicion(c) for c in faltan)}"
    return None


def _primero(esperado: dict, r: Respuesta) -> str | None:
    if not r.productos:
        return f"no cita ningún producto y el primero debía cumplir {_texto_condicion(esperado)}"
    if not cumple(r.productos[0], esperado):
        return (f"el primer producto citado es {_texto_producto(r.productos[0])} "
                f"y debía cumplir {_texto_condicion(esperado)}")
    return None


def _stock_total_min_kg(esperado, r: Respuesta) -> str | None:
    total = sum(p["stock_kg"] for p in r.productos)
    if total < esperado:
        return f"los productos citados suman {total:g} kg y hacían falta {esperado:g} kg"
    return None


def _herramientas(esperado: list, r: Respuesta) -> str | None:
    usadas = {paso.herramienta for paso in r.traza if not paso.error}
    faltan = [h for h in esperado if h not in usadas]
    if faltan:
        return f"no llamó a {', '.join(faltan)} (llamó a: {', '.join(sorted(usadas)) or 'ninguna'})"
    return None


def _verificada(esperado: bool, r: Respuesta) -> str | None:
    if r.verificada != esperado:
        return ("el verificador sustituyó la respuesta del modelo por el texto seguro" if esperado
                else "se esperaba que el verificador sustituyera la respuesta")
    return None


# Frases con las que el asistente pide datos que le faltan, con o sin signo de interrogación.
_PIDE_DATOS = ("indícame", "indicame", "proporciona", "dime", "necesito que", "necesito saber",
               "cuéntame", "could you", "please provide", "let me know")


def pide_aclaracion(texto: str) -> bool:
    """El asistente devuelve la pregunta en cualquier parte del texto, no solo al final:
    a veces pide los datos en una lista y cierra con una frase de cortesía."""
    t = texto.lower()
    return "?" in t or any(frase in t for frase in _PIDE_DATOS)


def _termina_en_pregunta(esperado: bool, r: Respuesta) -> str | None:
    # El nombre de la comprobacion se conserva para no tocar preguntas.yaml.
    if pide_aclaracion(r.texto) != esperado:
        return "la respuesta no pide los datos que faltan" if esperado else "la respuesta pide datos que ya tenía"
    return None


# Palabras muy frecuentes que solo existen en uno de los dos idiomas.
_PALABRAS = {
    "es": {"el", "la", "los", "las", "de", "del", "que", "y", "para", "con", "por", "una", "hay", "en", "se"},
    "en": {"the", "of", "and", "for", "with", "is", "we", "you", "have", "to", "this", "it", "are", "from", "at"},
}


def detectar_idioma(texto: str) -> str:
    palabras = re.findall(r"[a-záéíóúñ]+", texto.lower())
    return max(_PALABRAS, key=lambda idioma: sum(p in _PALABRAS[idioma] for p in palabras))


def _idioma(esperado: str, r: Respuesta) -> str | None:
    detectado = detectar_idioma(r.texto)
    if detectado != esperado:
        return f"la respuesta está en '{detectado}' y se esperaba '{esperado}'"
    return None


COMPROBACIONES = {
    "num_productos": _num_productos,
    "todos": _todos,
    "alguno": _alguno,
    "primero": _primero,
    "stock_total_min_kg": _stock_total_min_kg,
    "herramientas": _herramientas,
    "verificada": _verificada,
    "termina_en_pregunta": _termina_en_pregunta,
    "idioma": _idioma,
}


def comprobar(espera: dict, respuesta: Respuesta) -> list[str]:
    """Motivos por los que la respuesta no cumple lo esperado. Lista vacia = caso aprobado."""
    motivos = []
    for nombre, esperado in espera.items():
        motivo = COMPROBACIONES[nombre](esperado, respuesta)
        if motivo:
            motivos.append(f"{nombre}: {motivo}")
    return motivos


# === Carga de casos =============================================================

def cargar_casos(ruta: Path = RUTA_PREGUNTAS) -> list[dict]:
    """Lee el YAML, aplica `por_defecto` a cada caso y valida la estructura."""
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    por_defecto = datos.get("por_defecto") or {}
    casos, vistos = [], set()
    for caso in datos["casos"]:
        faltan = [c for c in ("id", "categoria", "pregunta", "espera") if not caso.get(c)]
        if faltan:
            raise ValueError(f"Al caso {caso.get('id', caso)!r} le falta: {', '.join(faltan)}")
        if caso["id"] in vistos:
            raise ValueError(f"Id de caso repetido: {caso['id']}")
        vistos.add(caso["id"])
        espera = {**por_defecto, **caso["espera"]}
        desconocidas = [n for n in espera if n not in COMPROBACIONES]
        if desconocidas:
            raise ValueError(f"El caso {caso['id']} usa comprobaciones que no existen: {', '.join(desconocidas)}")
        casos.append({"id": caso["id"], "categoria": caso["categoria"],
                      "pregunta": caso["pregunta"].strip(), "espera": espera})
    return casos


def filtrar(casos: list[dict], categorias=None, ids=None, limite=None) -> list[dict]:
    if categorias:
        casos = [c for c in casos if c["categoria"] in categorias]
    if ids:
        casos = [c for c in casos if c["id"] in ids]
    return casos[:limite] if limite else casos


# === 2. Ejecucion ===============================================================

def ejecutar_caso(asistente: Asistente, inv: Inventario, caso: dict) -> dict:
    """Lanza un caso con una conversacion nueva y devuelve su resultado, listo para guardar en JSON."""
    r = asistente.responder(caso["pregunta"], Conversacion())
    motivos = comprobar(caso["espera"], r)

    # El texto final nunca trae ids inventados: o paso el verificador o se
    # sustituyo. Lo que invento el modelo esta en `rechazados`, que mezcla ids
    # que no existen (inventados) con ids reales que ninguna herramienta devolvio.
    existentes = {p["id"] for p in inv.por_ids(r.rechazados)}
    escritos = list(dict.fromkeys(r.validacion.citados + r.rechazados))

    return {
        "id": caso["id"], "categoria": caso["categoria"], "pregunta": caso["pregunta"],
        "aprobado": not motivos, "motivos": motivos, "error": None,
        "texto": r.texto, "citados": r.validacion.citados, "verificada": r.verificada,
        "ids_escritos": escritos,
        "ids_inventados": [i for i in r.rechazados if i not in existentes],
        "ids_sin_evidencia": [i for i in r.rechazados if i in existentes],
        "inventados_en_respuesta": r.validacion.inventados, "reintentada": r.reintentada,
        "traza": [{"herramienta": p.herramienta, "argumentos": p.argumentos, "error": p.error, "ms": p.ms}
                  for p in r.traza],
        "llamadas_llm": r.llamadas_llm, "tokens_entrada": r.tokens_entrada,
        "tokens_salida": r.tokens_salida, "ms": r.ms,
    }


def caso_con_error(caso: dict, mensaje: str) -> dict:
    return {"id": caso["id"], "categoria": caso["categoria"], "pregunta": caso["pregunta"],
            "aprobado": False, "motivos": [], "error": mensaje}


def coste_usd(tokens_entrada: float, tokens_salida: float) -> float:
    return (tokens_entrada * PRECIO_ENTRADA_USD_M + tokens_salida * PRECIO_SALIDA_USD_M) / 1_000_000


def _media(valores: list) -> float:
    return sum(valores) / len(valores) if valores else 0.0


def _percentil(valores: list, q: float) -> float:
    """Percentil por rango mas cercano: el valor que deja por debajo al q por uno de los casos."""
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    return ordenados[max(0, math.ceil(q * len(ordenados)) - 1)]


def _tasa(parte: int, total: int) -> float:
    return parte / total if total else 0.0


def _velocidad(hechos: list[dict]) -> float:
    """Tokens de salida por segundo, sumando todos los casos (no la media de cada caso)."""
    ms_total = sum(r["ms"] for r in hechos)
    return round(sum(r["tokens_salida"] for r in hechos) / (ms_total / 1000), 1) if ms_total else 0.0


def resumir(resultados: list[dict]) -> dict:
    """Metricas agregadas. Los casos con error del proveedor no cuentan: no llegaron a ejecutarse."""
    hechos = [r for r in resultados if not r["error"]]
    por_categoria = {}
    for r in hechos:
        c = por_categoria.setdefault(r["categoria"], {"casos": 0, "aprobados": 0})
        c["casos"] += 1
        c["aprobados"] += r["aprobado"]
    for c in por_categoria.values():
        c["aciertos"] = _tasa(c["aprobados"], c["casos"])

    escritos = sum(len(r["ids_escritos"]) for r in hechos)
    inventados = sum(len(r["ids_inventados"]) for r in hechos)
    tokens_in, tokens_out = _media([r["tokens_entrada"] for r in hechos]), _media([r["tokens_salida"] for r in hechos])
    return {
        "casos": len(hechos),
        "errores_proveedor": len(resultados) - len(hechos),
        "aprobados": sum(r["aprobado"] for r in hechos),
        "aciertos": _tasa(sum(r["aprobado"] for r in hechos), len(hechos)),
        "por_categoria": por_categoria,
        # Lo que el modelo escribio, antes del verificador.
        "ids_escritos": escritos,
        "ids_inventados": inventados,
        "tasa_ids_inventados": _tasa(inventados, escritos),
        "casos_con_ids_inventados": sum(bool(r["ids_inventados"]) for r in hechos),
        "ids_sin_evidencia": sum(len(r["ids_sin_evidencia"]) for r in hechos),
        # Lo que le llega al usuario, despues del verificador.
        "ids_inventados_en_respuesta": sum(len(r["inventados_en_respuesta"]) for r in hechos),
        "corregidas_al_reintentar": sum(r["reintentada"] and r["verificada"] for r in hechos),
        "sustituidas": sum(not r["verificada"] for r in hechos),
        "tasa_sustituidas": _tasa(sum(not r["verificada"] for r in hechos), len(hechos)),
        "latencia_media_ms": round(_media([r["ms"] for r in hechos])),
        "latencia_p90_ms": round(_percentil([r["ms"] for r in hechos], 0.9)),
        # De extremo a extremo: incluye el tiempo de las herramientas y del verificador, no solo el modelo.
        "tokens_salida_por_segundo": _velocidad(hechos),
        "llamadas_llm_media": round(_media([r["llamadas_llm"] for r in hechos]), 1),
        "tokens_entrada_media": round(tokens_in),
        "tokens_salida_media": round(tokens_out),
        "coste_medio_usd": round(coste_usd(tokens_in, tokens_out), 4),
        "coste_total_usd": round(sum(coste_usd(r["tokens_entrada"], r["tokens_salida"]) for r in hechos), 4),
        "precio_usd_millon": {"entrada": PRECIO_ENTRADA_USD_M, "salida": PRECIO_SALIDA_USD_M},
    }


def informe(resumen: dict, resultados: list[dict]) -> str:
    """El resumen como texto para la terminal."""
    s = resumen
    lineas = [
        "", "RESULTADOS", "=" * 78,
        f"Aciertos: {s['aprobados']}/{s['casos']} ({s['aciertos']:.0%})", "",
        f"  {'categoría':<14}{'casos':>6}{'aprobados':>11}{'aciertos':>10}",
    ]
    for nombre, c in sorted(s["por_categoria"].items()):
        lineas.append(f"  {nombre:<14}{c['casos']:>6}{c['aprobados']:>11}{c['aciertos']:>10.0%}")
    lineas += [
        "",
        f"Ids inventados por el modelo:   {s['ids_inventados']} de {s['ids_escritos']} ids escritos "
        f"({s['tasa_ids_inventados']:.1%}), en {s['casos_con_ids_inventados']} casos",
        f"Ids reales citados sin haberlos buscado: {s['ids_sin_evidencia']}",
        f"Ids inventados que llegan al usuario:    {s['ids_inventados_en_respuesta']}",
        f"Corregidas al reintentar:       {s['corregidas_al_reintentar']} de {s['casos']}",
        f"Sustituidas por el verificador: {s['sustituidas']} de {s['casos']} ({s['tasa_sustituidas']:.1%})",
        f"Latencia:                       media {s['latencia_media_ms'] / 1000:.1f} s, "
        f"p90 {s['latencia_p90_ms'] / 1000:.1f} s",
        f"Llamadas al modelo por consulta: {s['llamadas_llm_media']}",
        f"Tokens por consulta:            {s['tokens_entrada_media']} de entrada, {s['tokens_salida_media']} de salida",
        f"Velocidad (tokens de salida/s, de extremo a extremo): {s['tokens_salida_por_segundo']}",
        f"Coste estimado:                 {s['coste_medio_usd']:.4f} $ por consulta, {s['coste_total_usd']:.2f} $ en total "
        f"(a {PRECIO_ENTRADA_USD_M:g} $ y {PRECIO_SALIDA_USD_M:g} $ por millón de tokens de entrada y salida)",
    ]
    if s.get("servidor"):
        m = s["servidor"]
        lineas += [
            "",
            "SERVIDOR LOCAL",
            f"Memoria del servidor (RSS):     pico {_mb(m['rss_pico_mb'])}, media {_mb(m['rss_media_mb'])}",
            f"Memoria libre del sistema:      mínimo {_pct(m['libre_minimo_pct'])}",
            f"Caché de prompts del servidor:  máximo {m['cache_max_gb']:.2f} GB (el límite está en serverMLX/config.env)",
        ]

    fallados = [r for r in resultados if not r["error"] and not r["aprobado"]]
    if fallados:
        lineas += ["", f"CASOS FALLADOS ({len(fallados)})", "-" * 78]
        for r in fallados:
            lineas.append(f"{r['id']} [{r['categoria']}] {_una_linea(r['pregunta'])}")
            lineas += [f"    - {motivo}" for motivo in r["motivos"]]
    errores = [r for r in resultados if r["error"]]
    if errores:
        lineas += ["", f"CASOS NO EJECUTADOS POR ERROR DEL PROVEEDOR ({len(errores)})", "-" * 78]
        lineas += [f"{r['id']}: {r['error']}" for r in errores]
    return "\n".join(lineas)


def _mb(valor: int | None) -> str:
    return f"{valor} MB" if valor is not None else "no disponible"


def _pct(valor: int | None) -> str:
    return f"{valor} %" if valor is not None else "no disponible"


def _iniciar_medicion():
    """Solo con el proveedor local y el servidor en marcha: empieza a medir memoria y guarda
    hasta donde llega el log, para leer despues solo lo escrito durante la evaluacion."""
    if config.PROVEEDOR != "local":
        return None
    carpeta = config.SERVIDOR_MLX_DIR
    pid = pid_servidor(carpeta / ".server.pid")
    if pid is None:
        return None
    ruta_log = carpeta / "logs" / "server.log"
    inicio_log = ruta_log.stat().st_size if ruta_log.exists() else 0
    monitor = MonitorServidor(pid)
    monitor.iniciar()
    return monitor, ruta_log, inicio_log


def _terminar_medicion(medicion) -> dict | None:
    if medicion is None:
        return None
    monitor, ruta_log, inicio_log = medicion
    monitor.parar()
    cifras = monitor.resultado()
    cifras["cache_max_gb"] = pico_cache_gb(texto_desde(ruta_log, inicio_log))
    return cifras


def _una_linea(texto: str, ancho: int = 60) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= ancho else texto[:ancho - 1] + "…"


def guardar(resumen: dict, resultados: list[dict], carpeta: Path = RUTA_RESULTADOS,
            modelo: str = config.MODELO) -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    ahora = datetime.now()
    ruta = carpeta / f"{ahora:%Y-%m-%d_%H%M%S}.json"
    detalle = {"fecha": ahora.isoformat(timespec="seconds"), "modelo": modelo,
               "esfuerzo": config.ESFUERZO, "resumen": resumen, "casos": resultados}
    ruta.write_text(json.dumps(detalle, ensure_ascii=False, indent=2), encoding="utf-8")
    return ruta


def falta_clave() -> bool:
    return config.PROVEEDOR == "anthropic" and not os.getenv("ANTHROPIC_API_KEY")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evalúa el asistente contra eval/preguntas.yaml.")
    parser.add_argument("--categoria", action="append", help="solo esta categoría (se puede repetir)")
    parser.add_argument("--id", action="append", help="solo este caso (se puede repetir)")
    parser.add_argument("--limite", type=int, help="número máximo de casos")
    parser.add_argument("--listar", action="store_true", help="enseña los casos elegidos sin llamar al modelo")
    args = parser.parse_args(argv)

    casos = filtrar(cargar_casos(), args.categoria, args.id, args.limite)
    if not casos:
        print("Ningún caso coincide con los filtros.", file=sys.stderr)
        return 2
    if args.listar:
        for caso in casos:
            print(f"{caso['id']:<8}{caso['categoria']:<13}{_una_linea(caso['pregunta'], 70)}")
        print(f"\n{len(casos)} casos")
        return 0
    if falta_clave():
        print("Falta la clave de API: copia .env.example a .env y rellena ANTHROPIC_API_KEY. "
              "No se ha ejecutado ningún caso.", file=sys.stderr)
        return 2

    try:
        inv = crear_inventario()
        proveedor = crear_proveedor()
        asistente = Asistente(proveedor, inv)
    except ErrorProveedor as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    modelo = getattr(proveedor, "modelo", config.MODELO)
    print(f"Evaluando {len(casos)} casos con {modelo}\n")
    medicion = _iniciar_medicion()
    resultados, errores_seguidos = [], 0
    for n, caso in enumerate(casos, 1):
        try:
            resultado = ejecutar_caso(asistente, inv, caso)
            errores_seguidos = 0
            estado = "ok   " if resultado["aprobado"] else "FALLO"
            detalle = f"{resultado['ms'] / 1000:.1f} s"
        except ErrorProveedor as e:
            resultado = caso_con_error(caso, str(e))
            errores_seguidos += 1
            estado, detalle = "ERROR", str(e)
        resultados.append(resultado)
        print(f"[{n:>2}/{len(casos)}] {estado} {caso['id']:<8}{caso['categoria']:<13}{detalle}", flush=True)
        if errores_seguidos == MAX_ERRORES_SEGUIDOS:
            print(f"\n{MAX_ERRORES_SEGUIDOS} errores seguidos del proveedor: se interrumpe la evaluación.",
                  file=sys.stderr)
            break

    if all(r["error"] for r in resultados):
        print("No se ha podido ejecutar ningún caso. Revisa la clave de API y la conexión.", file=sys.stderr)
        return 1
    resumen = resumir(resultados)
    servidor = _terminar_medicion(medicion)
    if servidor:
        resumen["servidor"] = servidor
    print(informe(resumen, resultados))
    if config.PROVEEDOR != "anthropic":
        print("\nNota: el coste estimado usa precios de la API de Claude; con otro proveedor no aplica.")
    print(f"\nDetalle guardado en {guardar(resumen, resultados, modelo=modelo)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
