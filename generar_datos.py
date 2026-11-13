"""Genera datos sinteticos de inventario y demanda de acero inoxidable.

Reproducible: misma semilla -> mismos ficheros.
Uso:  python generar_datos.py

Salida:
  data/inventario.csv   ~500 productos
  data/demanda.csv      historico mensual de pedidos por grado y formato
  data/inventario.db    SQLite con las tablas `inventario` y `demanda`

Las fichas tecnicas (data/fichas_tecnicas/*.md) son texto estatico, no se generan aqui.
Solo usa la libreria estandar.
"""

import csv
import math
import random
import sqlite3
from pathlib import Path

SEMILLA = 42
N_PRODUCTOS = 500
DATA = Path(__file__).parent / "data"

# --- Catalogo de referencia -------------------------------------------------

# grado: (familia, densidad kg/dm3, precio base EUR/kg, peso relativo en el inventario)
GRADOS = {
    "304":  ("austenítico", 7.93, 3.05, 30),
    "304L": ("austenítico", 7.93, 3.15, 15),
    "316":  ("austenítico", 7.98, 4.20, 8),
    "316L": ("austenítico", 7.98, 4.30, 17),
    "321":  ("austenítico", 7.90, 3.70, 5),
    "310S": ("austenítico", 7.90, 6.40, 4),
    "430":  ("ferrítico",   7.70, 2.10, 15),
    "2205": ("dúplex",      7.80, 5.10, 6),
}

FORMATOS = {"bobina": 25, "chapa": 35, "fleje": 15, "barra": 12, "tubo": 13}

# Acabados posibles por formato y por grado (la interseccion es lo valido).
ACABADOS_FORMATO = {
    "bobina": ["2B", "BA", "N4", "1D"],
    "chapa":  ["2B", "BA", "N4", "1D"],
    "fleje":  ["2B", "BA"],
    "barra":  ["1D"],          # laminado en caliente / descascarillado
    "tubo":   ["2B", "N4"],    # N4 = pulido exterior
}
ACABADOS_GRADO = {
    "304": ["2B", "BA", "N4", "1D"], "304L": ["2B", "BA", "N4", "1D"],
    "316": ["2B", "BA", "N4", "1D"], "316L": ["2B", "BA", "N4", "1D"],
    "321": ["2B", "1D"], "310S": ["2B", "1D"],
    "430": ["2B", "BA", "N4"], "2205": ["2B", "1D"],
}

# Espesores comerciales por acabado (mm)
ESPESORES = {
    "BA": [0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.2, 1.5, 2.0],
    "N4": [0.5, 0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0],
    "2B": [0.4, 0.5, 0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0],
    "1D": [3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0],
}
ANCHOS_PLANO = [1000, 1250, 1500, 2000]
LARGOS_CHAPA = [2000, 2500, 3000, 4000, 6000]
ANCHOS_FLEJE = [20, 30, 40, 50, 60, 80, 100, 125, 150, 200, 250, 300, 400, 500, 600]
DIAMETROS_BARRA = [6, 8, 10, 12, 16, 20, 25, 30, 40, 50, 60, 80, 100, 120, 150, 200]
DIAMETROS_TUBO = [12.7, 21.3, 26.9, 33.7, 42.4, 48.3, 60.3, 76.1, 88.9, 114.3, 168.3]
PAREDES_TUBO = [1.0, 1.5, 2.0, 2.77, 3.05, 3.68]

# Almacen: peso relativo (Algeciras = almacen junto a fabrica, el mas grande)
ALMACENES = {"Algeciras": 30, "Madrid": 20, "Barcelona": 18, "Bilbao": 12, "Valencia": 12, "Sevilla": 8}

# Sobreprecio EUR/kg por valor anadido
EXTRA_FORMATO = {"bobina": 0.0, "chapa": 0.15, "fleje": 0.40, "barra": 0.60, "tubo": 1.20}
EXTRA_ACABADO = {"1D": -0.10, "2B": 0.0, "BA": 0.30, "N4": 0.40}

COLUMNAS = [
    "id", "grado", "familia", "formato", "acabado", "espesor_mm", "ancho_mm", "largo_mm",
    "peso_kg", "stock_kg", "almacen", "precio_eur_kg", "margen_pct", "rotacion_dias", "norma",
]


def elegir(rng, pesos: dict):
    return rng.choices(list(pesos), weights=list(pesos.values()))[0]


def norma(formato: str) -> str:
    """Norma europea / ASTM tipica segun formato."""
    if formato == "barra":
        return "EN 10088-3 / ASTM A276"
    if formato == "tubo":
        return "EN 10217-7 / ASTM A312"
    return "EN 10088-2 / ASTM A240"


def dimensiones(rng, formato, acabado, densidad):
    """Devuelve (espesor, ancho, largo, peso_unidad_kg). largo None = bobina/rollo."""
    if formato in ("bobina", "chapa"):
        espesor = rng.choice(ESPESORES[acabado] if formato == "chapa" else [e for e in ESPESORES[acabado] if e <= 12])
        anchos = [a for a in ANCHOS_PLANO if acabado != "BA" or a <= 1250]
        ancho = rng.choice(anchos)
        if formato == "bobina":
            return espesor, ancho, None, round(rng.uniform(4000, 22000), -1)
        largo = rng.choice(LARGOS_CHAPA)
        peso = espesor * ancho * largo * densidad / 1e6
        return espesor, ancho, largo, round(peso, 1)
    if formato == "fleje":
        espesor = rng.choice([e for e in ESPESORES[acabado] if e <= 3])
        return espesor, rng.choice(ANCHOS_FLEJE), None, round(rng.uniform(200, 3000), -1)
    if formato == "barra":
        d = rng.choice(DIAMETROS_BARRA)
        largo = rng.choice([3000, 6000])
        peso = math.pi * (d / 2) ** 2 * largo * densidad / 1e6
        return d, d, largo, round(peso, 1)
    # tubo: espesor = pared, ancho = diametro exterior
    diam = rng.choice(DIAMETROS_TUBO)
    pared = rng.choice([p for p in PAREDES_TUBO if p < diam / 6])
    largo = 6000
    peso = math.pi * (diam - pared) * pared * largo * densidad / 1e6
    return pared, diam, largo, round(peso, 1)


def producto(rng, n, grado, formato, acabado, almacen, espesor=None, ancho=None, largo=None, unidades=None):
    familia, densidad, base, _ = GRADOS[grado]
    e, a, l, peso = dimensiones(rng, formato, acabado, densidad)
    if espesor is not None:  # escenario fijado a mano
        e, a, l = espesor, ancho, largo
        peso = round(e * a * l * densidad / 1e6, 1) if l else peso

    if unidades is None:
        unidades = 0 if rng.random() < 0.08 else rng.randint(1, 40 if peso < 500 else 8)
    stock = round(unidades * peso, 1)

    precio = base + EXTRA_FORMATO[formato] + EXTRA_ACABADO[acabado]
    if e < 1.0 and formato != "tubo":
        precio += 0.25  # espesores finos cuestan mas de laminar
    precio *= rng.uniform(0.95, 1.05)

    margen = rng.uniform(8, 18) + (4 if acabado == "N4" or formato == "tubo" else 0)
    rotacion = int(min(540, max(7, rng.lognormvariate(math.log(60), 0.8))))

    return {
        "id": f"ACX-{n:05d}", "grado": grado, "familia": familia, "formato": formato,
        "acabado": acabado, "espesor_mm": e, "ancho_mm": a, "largo_mm": l if l else "",
        "peso_kg": peso, "stock_kg": stock, "almacen": almacen,
        "precio_eur_kg": round(precio, 2), "margen_pct": round(margen, 1),
        "rotacion_dias": rotacion, "norma": norma(formato),
    }


# Escenarios fijos para la demo (siempre presentes, al final del CSV).
# Sirven para ensenar "no hay stock -> alternativa" de forma predecible.
ESCENARIOS_DEMO = [
    # 316 chapa 2B 2 mm 1500x3000 agotada en Madrid...
    dict(grado="316",  formato="chapa", acabado="2B", almacen="Madrid",    espesor=2.0, ancho=1500, largo=3000, unidades=0),
    # ...pero hay 316L equivalente en Madrid (alternativa por grado)
    dict(grado="316L", formato="chapa", acabado="2B", almacen="Madrid",    espesor=2.0, ancho=1500, largo=3000, unidades=60),
    # ...y 316 identica en Barcelona (alternativa por almacen)
    dict(grado="316",  formato="chapa", acabado="2B", almacen="Barcelona", espesor=2.0, ancho=1500, largo=3000, unidades=25),
    # 2205 chapa gruesa para ambiente marino, solo en Algeciras
    dict(grado="2205", formato="chapa", acabado="1D", almacen="Algeciras", espesor=10.0, ancho=2000, largo=6000, unidades=12),
    # 430 BA fino para electrodomestico, mucho stock y rotacion lenta (prioridad comercial)
    dict(grado="430",  formato="chapa", acabado="BA", almacen="Valencia",  espesor=0.6, ancho=1250, largo=2500, unidades=300),
    # 310S para horno, alta temperatura
    dict(grado="310S", formato="chapa", acabado="2B", almacen="Bilbao",    espesor=3.0, ancho=1500, largo=3000, unidades=20),
]


def generar_inventario(rng):
    filas = []
    n_aleatorios = N_PRODUCTOS - len(ESCENARIOS_DEMO)
    while len(filas) < n_aleatorios:
        grado = elegir(rng, {g: v[3] for g, v in GRADOS.items()})
        formato = elegir(rng, FORMATOS)
        validos = [a for a in ACABADOS_FORMATO[formato] if a in ACABADOS_GRADO[grado]]
        if not validos:
            continue
        acabado = rng.choice(validos)
        filas.append(producto(rng, len(filas) + 1, grado, formato, acabado, elegir(rng, ALMACENES)))

    for esc in ESCENARIOS_DEMO:
        fila = producto(rng, len(filas) + 1, **esc)
        if esc["grado"] == "430":
            fila["rotacion_dias"] = 280
        filas.append(fila)
    return filas


# --- Demanda -----------------------------------------------------------------

TAMANO_PEDIDO_KG = {"bobina": 12000, "chapa": 2500, "fleje": 800, "barra": 600, "tubo": 900}
# Estacionalidad: agosto flojo, diciembre algo flojo
ESTACIONALIDAD = {1: 0.95, 2: 1.0, 3: 1.08, 4: 1.02, 5: 1.05, 6: 1.03,
                  7: 0.92, 8: 0.62, 9: 1.05, 10: 1.10, 11: 1.04, 12: 0.85}


def meses(desde=(2024, 11), n=24):
    anio, mes = desde
    for _ in range(n):
        yield anio, mes
        mes += 1
        if mes == 13:
            anio, mes = anio + 1, 1


def generar_demanda(rng):
    filas = []
    for i, (anio, mes) in enumerate(meses()):
        tendencia = 1 + 0.004 * i  # ligero crecimiento
        for grado, (_, _, _, peso_grado) in GRADOS.items():
            for formato, peso_formato in FORMATOS.items():
                validos = [a for a in ACABADOS_FORMATO[formato] if a in ACABADOS_GRADO[grado]]
                if not validos:
                    continue
                media = 0.9 * peso_grado * peso_formato / 25 * ESTACIONALIDAD[mes] * tendencia
                pedidos = max(0, round(rng.gauss(media, media * 0.18)))
                kg = round(pedidos * TAMANO_PEDIDO_KG[formato] * rng.uniform(0.8, 1.2), -1)
                filas.append({"mes": f"{anio}-{mes:02d}", "grado": grado, "formato": formato,
                              "num_pedidos": pedidos, "kg_pedidos": kg})
    return filas


# --- Escritura ---------------------------------------------------------------

def escribir_csv(ruta, filas, columnas):
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columnas)
        w.writeheader()
        w.writerows(filas)


def escribir_sqlite(ruta, inventario, demanda):
    ruta.unlink(missing_ok=True)
    con = sqlite3.connect(ruta)
    con.execute("""CREATE TABLE inventario (
        id TEXT PRIMARY KEY, grado TEXT, familia TEXT, formato TEXT, acabado TEXT,
        espesor_mm REAL, ancho_mm REAL, largo_mm REAL, peso_kg REAL, stock_kg REAL,
        almacen TEXT, precio_eur_kg REAL, margen_pct REAL, rotacion_dias INTEGER, norma TEXT)""")
    con.execute("""CREATE TABLE demanda (
        mes TEXT, grado TEXT, formato TEXT, num_pedidos INTEGER, kg_pedidos REAL)""")
    con.executemany(f"INSERT INTO inventario VALUES ({','.join('?' * len(COLUMNAS))})",
                    [[f[c] if f[c] != "" else None for c in COLUMNAS] for f in inventario])
    con.executemany("INSERT INTO demanda VALUES (:mes, :grado, :formato, :num_pedidos, :kg_pedidos)", demanda)
    con.commit()
    con.close()


def main():
    rng = random.Random(SEMILLA)
    DATA.mkdir(exist_ok=True)
    inventario = generar_inventario(rng)
    demanda = generar_demanda(rng)
    escribir_csv(DATA / "inventario.csv", inventario, COLUMNAS)
    escribir_csv(DATA / "demanda.csv", demanda, ["mes", "grado", "formato", "num_pedidos", "kg_pedidos"])
    escribir_sqlite(DATA / "inventario.db", inventario, demanda)

    sin_stock = sum(1 for f in inventario if f["stock_kg"] == 0)
    total_t = sum(f["stock_kg"] for f in inventario) / 1000
    print(f"inventario.csv: {len(inventario)} productos, {sin_stock} sin stock, {total_t:,.0f} t en total")
    print(f"demanda.csv:    {len(demanda)} filas ({demanda[0]['mes']} a {demanda[-1]['mes']})")
    print(f"inventario.db:  tablas inventario y demanda")


if __name__ == "__main__":
    main()
