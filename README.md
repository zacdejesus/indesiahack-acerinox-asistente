# Asistente inteligente de inventario comercial

Prototipo para IndesIAhack 2026, reto de Acerinox con Tinámica. Un comercial escribe una necesidad en lenguaje natural, o pega el correo de un cliente, y el asistente propone productos reales del inventario, explica el porqué técnico y ofrece una alternativa cuando no hay stock.

Los datos de este repositorio son sintéticos. El código está organizado para cambiar a los datos reales tocando un solo fichero (`asistente/datos.py`).

## Arrancar

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env    # pon tu clave en ANTHROPIC_API_KEY
.venv/bin/streamlit run app.py
```

## Otros comandos

```
.venv/bin/python -m pytest                 # tests, no necesitan clave de API
.venv/bin/python -m asistente "pregunta"   # el asistente por terminal, con la traza de pasos
.venv/bin/python -m eval.evaluar           # evaluación: aciertos, latencia y coste por consulta
.venv/bin/python generar_datos.py          # regenera los datos sintéticos (semilla fija)
```

## Qué lo distingue

- No inventa productos. Cada id de la respuesta se comprueba por código contra lo que devolvieron las herramientas; si no coincide, el texto del modelo se descarta.
- Puntúa en vez de filtrar. Una bobina más ancha sirve con un corte y un stock parcial cubre parte del pedido; cada punto perdido lleva su explicación.
- Empuja el stock parado. Entre opciones técnicamente válidas, recomienda primero la que lleva más tiempo en el almacén, usando la rotación y el histórico de demanda.
- Entiende cómo piden los clientes. Acepta el grado como 316L, AISI 316L, 1.4404 o S31603, y correos con varias líneas de material.
- Sustituciones de grado auditables. Están en una tabla (`asistente/dominio.py`) y solo van hacia un grado igual o superior.

## Casuística básica

| Caso | Ejemplo de pregunta | Qué hace el asistente |
|---|---|---|
| Hay stock | Chapa 304 2B de 1,5 mm, 1.000 kg | Propone el producto que mejor encaja, con almacén y stock |
| Sin stock | Chapa 316 2B de 2 mm, 1.500 kg, desde Madrid | No lo inventa: busca alternativas (otro almacén, grado equivalente, otro espesor) |
| Norma o nombre técnico | ¿Tenemos EN 1.4404 en chapa 2B de 2 mm? | Traduce la norma al grado interno y busca con él |
| Pregunta ambigua | Necesito acero inoxidable para un cliente | No recomienda nada: pide grado, formato, medidas y cantidad |
| Lo que no existe | Chapa de aluminio 5754 de 3 mm | No propone ningún producto: el inventario no tiene nada parecido |

Los casos completos, con su criterio de acierto, están en `eval/preguntas.yaml`.

## Cómo se puntúa un producto

Cada producto candidato empieza con 100 puntos. `_ajuste` en `asistente/herramientas.py` le resta puntos por cada descuento y deja escrito el motivo en las notas del resultado.

- Corte de ancho (solo en formatos planos: bobina, chapa, fleje). Si el ancho del producto supera el pedido, se restan `5 + 20 × exceso` puntos, con un máximo de 25. El exceso es `(ancho_producto − ancho_pedido) / ancho_pedido`. Ejemplo: bobina de 1500 mm para un pedido de 1250 mm; exceso 0,2; se restan 9 puntos (queda 91).
- Stock parcial. Si el stock no cubre la cantidad pedida, se restan `50 × (1 − cobertura)` puntos, donde la cobertura es stock dividido entre cantidad pedida. Ejemplo: 600 kg para 1000 kg; cobertura 60 %; se restan 20 puntos.

Los valores (5, 20, 25 y 50) son una heurística para ordenar opciones, no un dato validado del sector. Conviene calibrarlos con los mentores de Acerinox antes de dar cifras al cliente.

## Estructura

```
app.py                  interfaz Streamlit
asistente/
  agente.py             bucle: pregunta, modelo, herramientas, validador
  herramientas.py       buscar_stock, ficha_tecnica, alternativas, prioridad_comercial
  dominio.py            reglas del acero: alias de grados, sustituciones, pesos comerciales
  datos.py              acceso a datos (SQLite en solo lectura)
  proveedor.py          adaptador del modelo de lenguaje (Claude por defecto)
  validador.py          comprobación de los ids citados
eval/                   casos de prueba y script de evaluación
data/                   inventario, demanda y fichas técnicas (sintéticos)
docs/arquitectura.md    explicación detallada del diseño
docs/pitch.md           guion de la demo de 3 minutos
```

## Configuración

Variables en `.env`:

| Variable | Para qué | Por defecto |
|---|---|---|
| `ANTHROPIC_API_KEY` | Clave de la API de Claude | (obligatoria) |
| `ANTHROPIC_MODEL` | Modelo | `claude-opus-5-5` |
| `LLM_EFFORT` | Profundidad de razonamiento: low, medium, high | `medium` |
| `LLM_PROVIDER` | Proveedor del modelo | `anthropic` |
| `DATA_DIR` | Carpeta de datos | `data/` |
| `PATRON_ID` | Forma de un id de producto (expresión regular) | `\bACX-\d{4,5}\b` |

## Pasar a los datos reales

1. Cargar los datos en SQLite y adaptar las consultas de `asistente/datos.py` a sus columnas.
2. Ajustar `PATRON_ID` a la forma real de los ids.
3. Revisar las tablas de `asistente/dominio.py` si aparecen grados, formatos o acabados nuevos.
4. Ejecutar los tests y la evaluación.
