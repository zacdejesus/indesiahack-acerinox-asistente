"""Medidas del servidor local durante una evaluacion: memoria del proceso, presion del sistema
y tamaño de la cache de prompts. Solo observa: no arranca ni para el servidor.

Las funciones de lectura de texto y de suma son puras (se prueban en tests/test_memoria.py).
Lo que hace ps, memory_pressure y el log es la parte de macOS.
"""

import os
import re
import subprocess
import threading
from pathlib import Path

# Linea que mlx_lm.server escribe en su log en cada peticion, por ejemplo:
# "Prompt Cache: 10 sequences, 4.32 GB"
PATRON_CACHE = re.compile(r"Prompt Cache: \d+ sequences, ([\d.]+) GB")
PATRON_LIBRE = re.compile(r"System-wide memory free percentage: (\d+)%")


def pid_servidor(ruta_pid: Path) -> int | None:
    """El pid que escribe start.sh. None si no hay fichero o el proceso ya no existe."""
    try:
        pid = int(ruta_pid.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)  # señal 0: solo comprueba que el proceso existe
    except OSError:
        return None
    return pid


def arbol_rss_kb(filas: list[tuple[int, int, int]], raiz: int) -> int:
    """Suma la memoria (RSS, en KB) del proceso raiz y de todos sus descendientes.
    start.sh arranca el servidor a traves de uv, asi que el pid que se guarda es el padre."""
    hijos: dict[int, list[int]] = {}
    rss: dict[int, int] = {}
    for pid, ppid, kb in filas:
        hijos.setdefault(ppid, []).append(pid)
        rss[pid] = kb
    total, pendientes, vistos = 0, [raiz], set()
    while pendientes:
        pid = pendientes.pop()
        if pid in vistos or pid not in rss:
            continue
        vistos.add(pid)
        total += rss[pid]
        pendientes.extend(hijos.get(pid, []))
    return total


def pico_cache_gb(texto: str) -> float:
    """Mayor tamaño de cache de prompts que aparece en el texto del log. 0.0 si no aparece."""
    valores = [float(v) for v in PATRON_CACHE.findall(texto)]
    return max(valores, default=0.0)


def libre_pct(texto: str) -> int | None:
    """Porcentaje de memoria libre del sistema, según la salida de memory_pressure."""
    m = PATRON_LIBRE.search(texto)
    return int(m.group(1)) if m else None


def texto_desde(ruta: Path, desde: int) -> str:
    """Lo que el log ha escrito desde un byte concreto (para leer solo la evaluacion)."""
    try:
        with open(ruta, "rb") as f:
            f.seek(desde)
            return f.read().decode(errors="replace")
    except OSError:
        return ""


def _leer_procesos() -> list[tuple[int, int, int]]:
    salida = subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True).stdout
    filas = []
    for linea in salida.splitlines():
        partes = linea.split()
        if len(partes) == 3 and all(p.isdigit() for p in partes):
            filas.append(tuple(int(p) for p in partes))
    return filas


def _leer_libre() -> int | None:
    salida = subprocess.run(["memory_pressure"], capture_output=True, text=True).stdout
    return libre_pct(salida)


class MonitorServidor:
    """Muestrea cada pocos segundos la memoria del servidor y la libre del sistema, en un hilo."""

    def __init__(self, pid: int, intervalo_s: float = 2.0):
        self._pid = pid
        self._intervalo = intervalo_s
        self._muestras_kb: list[int] = []
        self._libres: list[int] = []
        self._parar = threading.Event()
        self._hilo = threading.Thread(target=self._bucle, daemon=True)

    def iniciar(self):
        self._hilo.start()

    def parar(self):
        self._parar.set()
        self._hilo.join()

    def _bucle(self):
        while not self._parar.is_set():
            kb = arbol_rss_kb(_leer_procesos(), self._pid)
            if kb:
                self._muestras_kb.append(kb)
            libre = _leer_libre()
            if libre is not None:
                self._libres.append(libre)
            self._parar.wait(self._intervalo)

    def resultado(self) -> dict:
        """Cifras para el informe. Los campos son None si no hubo ninguna muestra."""
        mb = [kb / 1024 for kb in self._muestras_kb]
        return {
            "rss_pico_mb": round(max(mb)) if mb else None,
            "rss_media_mb": round(sum(mb) / len(mb)) if mb else None,
            "libre_minimo_pct": min(self._libres) if self._libres else None,
            "muestras": len(mb),
        }
