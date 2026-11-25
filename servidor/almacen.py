"""Almacen de la aplicacion: usuarios, conversaciones, peticiones de oferta, avisos y notificaciones.

Va en una base SQLite propia, separada del inventario. El inventario es de
solo lectura; esto es lo unico que la aplicacion escribe.
"""

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    usuario TEXT PRIMARY KEY,
    sal     TEXT NOT NULL,
    hash    TEXT NOT NULL,
    rol     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ofertas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario     TEXT NOT NULL,
    producto_id TEXT NOT NULL,
    cantidad_kg REAL,
    comentario  TEXT NOT NULL DEFAULT '',
    creada      TEXT NOT NULL,
    estado      TEXT NOT NULL DEFAULT 'pendiente'
);
-- Cada aviso es tambien un registro de demanda no atendida: no se borra al cumplirse.
CREATE TABLE IF NOT EXISTS avisos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario    TEXT NOT NULL,
    criterios  TEXT NOT NULL,
    creado     TEXT NOT NULL,
    cumplido   TEXT
);
-- Destino: un usuario concreto o un rol entero ("rol:comercial").
CREATE TABLE IF NOT EXISTS notificaciones (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    destino TEXT NOT NULL,
    titulo  TEXT NOT NULL,
    texto   TEXT NOT NULL,
    creada  TEXT NOT NULL
);
-- Historial de chat. Se guarda lo que vio el usuario (pregunta y respuesta ya
-- filtrada por su rol), no los mensajes internos del proveedor: asi el historial
-- sirve aunque se cambie de modelo.
CREATE TABLE IF NOT EXISTS conversaciones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario     TEXT NOT NULL,
    titulo      TEXT NOT NULL,
    creada      TEXT NOT NULL,
    actualizada TEXT NOT NULL,
    evidencia   TEXT NOT NULL DEFAULT '[]'   -- ids que devolvieron las herramientas
);
CREATE TABLE IF NOT EXISTS turnos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversacion_id INTEGER NOT NULL,
    pregunta        TEXT NOT NULL,
    respuesta       TEXT NOT NULL,             -- JSON tal como se envio al navegador
    creado          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS turnos_conversacion ON turnos (conversacion_id);
"""


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(clave: str, sal: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", clave.encode(), bytes.fromhex(sal), 200_000).hex()


class AlmacenApp:
    def __init__(self, ruta: Path | str):
        if str(ruta) != ":memory:":
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(ruta), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        # FastAPI atiende en varios hilos: un cerrojo evita escrituras cruzadas.
        self._cerrojo = threading.Lock()
        with self._cerrojo, self._con:
            self._con.executescript(_ESQUEMA)

    def _ejecutar(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._cerrojo, self._con:
            return self._con.execute(sql, params)

    def _filas(self, sql: str, params=()) -> list[dict]:
        with self._cerrojo:
            return [dict(f) for f in self._con.execute(sql, params)]

    # --- Usuarios ---------------------------------------------------------------

    def crear_usuario(self, usuario: str, clave: str, rol: str) -> None:
        sal = secrets.token_hex(16)
        self._ejecutar("INSERT OR REPLACE INTO usuarios VALUES (?, ?, ?, ?)", (usuario, sal, _hash(clave, sal), rol))

    def autenticar(self, usuario: str, clave: str) -> str | None:
        """Devuelve el rol si usuario y clave son correctos."""
        filas = self._filas("SELECT sal, hash, rol FROM usuarios WHERE usuario = ?", (usuario,))
        if not filas:
            return None
        f = filas[0]
        return f["rol"] if hmac.compare_digest(_hash(clave, f["sal"]), f["hash"]) else None

    # --- Conversaciones ---------------------------------------------------------
    # Todas las consultas filtran por usuario: nadie puede leer ni borrar el
    # historial de otro aunque adivine el id.

    def crear_conversacion(self, usuario: str, titulo: str) -> int:
        ahora = _ahora()
        cur = self._ejecutar("INSERT INTO conversaciones (usuario, titulo, creada, actualizada) VALUES (?, ?, ?, ?)",
                             (usuario, titulo, ahora, ahora))
        return cur.lastrowid

    def guardar_turno(self, conversacion_id: int, pregunta: str, respuesta: dict, evidencia: set[str]) -> None:
        with self._cerrojo, self._con:
            cur = self._con.execute("UPDATE conversaciones SET actualizada = ?, evidencia = ? WHERE id = ?",
                                    (_ahora(), json.dumps(sorted(evidencia)), conversacion_id))
            if cur.rowcount:   # 0 = la borraron mientras se respondia: no se deja un turno huerfano
                self._con.execute("INSERT INTO turnos (conversacion_id, pregunta, respuesta, creado) VALUES (?, ?, ?, ?)",
                                  (conversacion_id, pregunta, json.dumps(respuesta, ensure_ascii=False), _ahora()))

    def conversaciones(self, usuario: str) -> list[dict]:
        # Orden por el ultimo turno y no por la hora: dos turnos en el mismo segundo no empatan.
        return self._filas(
            "SELECT c.id, c.titulo, c.creada, c.actualizada FROM conversaciones c WHERE c.usuario = ? "
            "ORDER BY (SELECT MAX(t.id) FROM turnos t WHERE t.conversacion_id = c.id) DESC, c.id DESC", (usuario,))

    def conversacion(self, conversacion_id: int, usuario: str) -> dict | None:
        filas = self._filas("SELECT * FROM conversaciones WHERE id = ? AND usuario = ?", (conversacion_id, usuario))
        if not filas:
            return None
        turnos = self._filas("SELECT pregunta, respuesta, creado FROM turnos WHERE conversacion_id = ? ORDER BY id",
                             (conversacion_id,))
        return filas[0] | {"evidencia": set(json.loads(filas[0]["evidencia"])),
                           "turnos": [t | {"respuesta": json.loads(t["respuesta"])} for t in turnos]}

    def borrar_conversacion(self, conversacion_id: int, usuario: str) -> bool:
        with self._cerrojo, self._con:
            cur = self._con.execute("DELETE FROM conversaciones WHERE id = ? AND usuario = ?", (conversacion_id, usuario))
            if cur.rowcount:
                self._con.execute("DELETE FROM turnos WHERE conversacion_id = ?", (conversacion_id,))
            return bool(cur.rowcount)

    # --- Ofertas ----------------------------------------------------------------

    def crear_oferta(self, usuario: str, producto_id: str, cantidad_kg: float | None, comentario: str) -> int:
        cur = self._ejecutar(
            "INSERT INTO ofertas (usuario, producto_id, cantidad_kg, comentario, creada) VALUES (?, ?, ?, ?, ?)",
            (usuario, producto_id, cantidad_kg, comentario, _ahora()))
        return cur.lastrowid

    def ofertas(self) -> list[dict]:
        return self._filas("SELECT * FROM ofertas ORDER BY id DESC")

    # --- Avisos de reposicion ---------------------------------------------------

    def crear_aviso(self, usuario: str, criterios: dict) -> int:
        cur = self._ejecutar("INSERT INTO avisos (usuario, criterios, creado) VALUES (?, ?, ?)",
                             (usuario, json.dumps(criterios, ensure_ascii=False), _ahora()))
        return cur.lastrowid

    def avisos(self, usuario: str | None = None, pendientes: bool = False) -> list[dict]:
        sql, params = "SELECT * FROM avisos WHERE 1=1", []
        if usuario is not None:
            sql += " AND usuario = ?"
            params.append(usuario)
        if pendientes:
            sql += " AND cumplido IS NULL"
        filas = self._filas(sql + " ORDER BY id DESC", params)
        return [f | {"criterios": json.loads(f["criterios"])} for f in filas]

    def cumplir_aviso(self, aviso_id: int) -> None:
        self._ejecutar("UPDATE avisos SET cumplido = ? WHERE id = ?", (_ahora(), aviso_id))

    # --- Notificaciones ---------------------------------------------------------

    def notificar(self, destino: str, titulo: str, texto: str) -> int:
        cur = self._ejecutar("INSERT INTO notificaciones (destino, titulo, texto, creada) VALUES (?, ?, ?, ?)",
                             (destino, titulo, texto, _ahora()))
        return cur.lastrowid

    def notificaciones(self, usuario: str, rol: str, desde: int = 0) -> list[dict]:
        return self._filas(
            "SELECT * FROM notificaciones WHERE destino IN (?, ?) AND id > ? ORDER BY id",
            (usuario, f"rol:{rol}", desde))
