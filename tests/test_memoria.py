"""Tests de las medidas del servidor local. Sin servidor ni red: se prueban las funciones puras
con textos y tablas de procesos fabricados, y la velocidad del resumen con casos sinteticos.

Uso:  python -m pytest tests/test_memoria.py
"""

from eval import evaluar
from eval.memoria import arbol_rss_kb, libre_pct, pico_cache_gb, texto_desde


def test_arbol_suma_el_padre_y_sus_descendientes_y_no_otros_procesos():
    # (pid, ppid, rss_kb): uv (10) lanza python (11), que lanza un hilo de trabajo (12).
    filas = [(10, 1, 1000), (11, 10, 5000), (12, 11, 2000), (99, 1, 7000)]
    assert arbol_rss_kb(filas, 10) == 8000


def test_arbol_sin_proceso_raiz_da_cero():
    assert arbol_rss_kb([(99, 1, 7000)], 10) == 0


def test_pico_de_cache_toma_el_mayor_valor_del_log():
    texto = (
        "2026-11-20 13:37:19 - INFO - Prompt Cache: 6 sequences, 2.35 GB\n"
        "2026-11-20 13:38:28 - INFO - Prompt Cache: 9 sequences, 3.43 GB\n"
        "2026-11-20 13:38:57 - INFO - Prompt Cache: 10 sequences, 4.22 GB\n"
    )
    assert pico_cache_gb(texto) == 4.22


def test_pico_de_cache_sin_lineas_da_cero():
    assert pico_cache_gb("POST /v1/chat/completions 200") == 0.0


def test_libre_pct_lee_la_salida_de_memory_pressure():
    assert libre_pct("...\nSystem-wide memory free percentage: 85%\n") == 85
    assert libre_pct("sin esa linea") is None


def test_texto_desde_lee_solo_lo_escrito_despues_de_un_punto(tmp_path):
    ruta = tmp_path / "server.log"
    ruta.write_text("antes\n", encoding="utf-8")
    inicio = ruta.stat().st_size
    with open(ruta, "a", encoding="utf-8") as f:
        f.write("durante Prompt Cache: 1 sequences, 0.5 GB\n")
    assert pico_cache_gb(texto_desde(ruta, inicio)) == 0.5
    assert "antes" not in texto_desde(ruta, inicio)


def _caso(ms: int, tokens_salida: int) -> dict:
    return {"id": "x", "categoria": "c", "pregunta": "p", "aprobado": True, "error": None,
            "ids_escritos": [], "ids_inventados": [], "ids_sin_evidencia": [],
            "inventados_en_respuesta": [], "reintentada": False, "verificada": True,
            "tokens_entrada": 100, "tokens_salida": tokens_salida, "ms": ms, "llamadas_llm": 1}


def test_velocidad_es_tokens_totales_partido_por_segundos_totales():
    # 300 tokens en 3 s y 100 tokens en 1 s: 400 / 4 = 100 tokens/s, no la media de (100 + 100) / 2.
    resumen = evaluar.resumir([_caso(3000, 300), _caso(1000, 100)])
    assert resumen["tokens_salida_por_segundo"] == 100.0


def test_velocidad_sin_tiempo_medido_da_cero():
    assert evaluar.resumir([_caso(0, 0)])["tokens_salida_por_segundo"] == 0.0
