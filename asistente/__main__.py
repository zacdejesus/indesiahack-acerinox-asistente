"""Prueba del asistente por terminal.

Uso:
  python -m asistente "Necesito chapa 316 2B de 2 mm en Madrid"
  python -m asistente            (modo conversacion; linea vacia para salir)
"""

import json
import sys

from .agente import Asistente, Conversacion, Respuesta
from .datos import crear_inventario
from .proveedor import ErrorProveedor, crear_proveedor


def mostrar(r: Respuesta) -> None:
    for paso in r.traza:
        marca = "ERROR" if paso.error else "ok"
        print(f"  [{marca}] {paso.herramienta}({json.dumps(paso.argumentos, ensure_ascii=False)}) {paso.ms} ms")
    print(f"\n{r.texto}\n")
    estado = "verificada" if r.verificada else "NO verificada: texto sustituido"
    print(f"  ({estado} | ids citados: {', '.join(r.validacion.citados) or 'ninguno'} | "
          f"{r.llamadas_llm} llamadas al modelo | {r.tokens_entrada}+{r.tokens_salida} tokens | {r.ms} ms)\n")


def main() -> int:
    try:
        asistente = Asistente(crear_proveedor(), crear_inventario())
        conversacion = Conversacion()
        if len(sys.argv) > 1:
            mostrar(asistente.responder(" ".join(sys.argv[1:]), conversacion))
            return 0
        while pregunta := input("> ").strip():
            mostrar(asistente.responder(pregunta, conversacion))
    except ErrorProveedor as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
