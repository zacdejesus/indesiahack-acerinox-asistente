# Cómo funciona el asistente, explicado para poder defenderlo

Este documento es para ti, no para el jurado. El objetivo es que puedas explicar cada decisión del backend sin mirar el código. Está ordenado así: primero la idea central, después un ejemplo seguido paso a paso, después cada patrón por separado y al final las preguntas que te pueden hacer.

## 1. La idea central en una frase

El modelo decide qué buscar y cómo explicarlo; los datos solo salen del código.

Un modelo de lenguaje es muy bueno entendiendo "necesito algo para una barandilla en primera línea de playa" y muy malo recordando cuántos kilos de 316L quedan en Madrid. Si le dejas contestar de memoria, se inventa productos. Por eso el sistema está partido en dos mitades que no se mezclan:

- La mitad que entiende y redacta es el modelo (Claude). No tiene acceso a la base de datos.
- La mitad que sabe es código Python normal: consultas SQL, tablas de reglas y un verificador. No usa IA.

El puente entre las dos se llama tool calling (llamada a herramientas) y es lo primero que hay que entender.

## 2. Qué es el tool calling

Al modelo se le envían tres cosas en cada petición: las instrucciones generales (el prompt de sistema), la conversación hasta ahora y una lista de herramientas. Cada herramienta es solo una descripción: un nombre, un texto que dice para qué sirve y la lista de parámetros que acepta.

El modelo no ejecuta nada. Lo único que puede hacer es contestar una de estas dos cosas:

- Texto normal, que es la respuesta para el usuario.
- Una petición del tipo "quiero que ejecutes `buscar_stock` con grado 316, formato chapa y almacén Madrid".

Cuando pide una herramienta, nuestro código la ejecuta (una función Python normal que consulta SQLite), y le devuelve el resultado al modelo como un mensaje más de la conversación. El modelo lo lee y decide: pedir otra herramienta o contestar ya.

Ese ir y venir es el bucle del agente. Un "agente" no es más que eso: un modelo dentro de un bucle que le deja pedir herramientas hasta que termina.

## 3. Un ejemplo seguido de principio a fin

El comercial escribe: "Necesito chapa 316 2B de 2 mm en Madrid".

Paso 1. `agente.py` añade la pregunta a la conversación y llama al modelo, pasándole el prompt de sistema y la descripción de las cuatro herramientas.

Paso 2. El modelo no contesta con texto. Pide: `buscar_stock(grado="316", formato="chapa", acabado="2B", espesor_min=2, espesor_max=2, almacen="Madrid")`.

Paso 3. El agente ejecuta esa función. Dentro pasan tres cosas:

- `herramientas.py` limpia los argumentos. Si el modelo hubiera escrito "AISI 316" o "madrid", `dominio.py` lo traduce a los nombres del inventario.
- `datos.py` hace la consulta SQL con esos filtros.
- La herramienta puntúa cada producto encontrado y arma el resultado.

En este caso el resultado es: cero productos, con el aviso "Sin stock con estos filtros. Usa `alternativas`".

Paso 4. El agente guarda ese paso en la traza (para enseñarlo en la interfaz) y le devuelve el resultado al modelo.

Paso 5. El modelo lee el aviso y pide: `alternativas(grado="316", formato="chapa", acabado="2B", espesor_mm=2, almacen="Madrid")`.

Paso 6. La herramienta devuelve tres propuestas, en este orden: el mismo 316 en Barcelona (ACX-00497), 316L en Madrid (ACX-00496) y 2205 en Madrid (ACX-00062). Cada una trae una nota escrita por nosotros que dice qué cambia para el cliente. Los tres ids entran en la evidencia de la conversación.

Paso 7. El modelo ya tiene lo que necesita y contesta con texto: no hay 316 en Madrid, propone ACX-00496 (316L, mismo almacén) y explica por qué el 316L vale.

Paso 8. Antes de enseñar nada, `validador.py` busca en ese texto todo lo que tenga forma de id de producto y comprueba que cada uno está en la evidencia. ACX-00496 está, así que la respuesta se da por verificada.

Paso 9. El agente devuelve un objeto `Respuesta` con el texto, la traza de pasos y la tabla de productos. Esa tabla no sale del texto del modelo: se lee de la base de datos usando los ids citados, así que el stock y el precio que ve el usuario en la tabla son siempre los reales.

En total: tres llamadas al modelo y dos herramientas ejecutadas.

## 4. Los ficheros y qué sabe cada uno

La regla de organización es que cada fichero sabe una sola cosa y no conoce los detalles de los demás.

| Fichero | Lo único que sabe | Lo que no sabe |
|---|---|---|
| `config.py` | Leer variables de entorno y rutas | Todo lo demás |
| `datos.py` | SQL y nombres de columnas | Que existe un modelo de IA |
| `dominio.py` | Reglas del acero | De dónde vienen los datos |
| `herramientas.py` | Qué operaciones se le ofrecen al modelo | Qué proveedor de IA se usa |
| `proveedor.py` | Hablar con la API de Claude | Qué es un producto o un almacén |
| `validador.py` | Comprobar ids contra la evidencia | Cómo se generó el texto |
| `agente.py` | El orden en que ocurre todo | SQL y detalles de la API |

Esta separación tiene un nombre: separación de responsabilidades. Se nota que está bien hecha cuando puedes contestar "si cambia X, ¿qué fichero toco?" con un solo nombre. Si cambian las columnas de los datos, `datos.py`. Si cambia el proveedor de IA, `proveedor.py`. Si un técnico dice que una sustitución de grado está mal, `dominio.py`.

## 5. Los patrones, uno por uno

Para cada uno: qué es, por qué está, qué alternativa se descartó y cómo decirlo en una frase.

### 5.1 Repositorio (`datos.py`)

Qué es. Una clase, `Inventario`, que es la única puerta a los datos. El resto del código le pide cosas en lenguaje de negocio ("productos con estos filtros", "demanda media de este grado") y recibe diccionarios. Nadie más escribe SQL.

Por qué. Los datos reales de Acerinox llegan el día del evento y no sabemos qué forma tendrán. Con todo el acceso a datos en un solo fichero, adaptarse es reescribir ese fichero y nada más.

Dos detalles de seguridad que vienen gratis:

- La conexión se abre en modo solo lectura. El asistente no puede borrar ni modificar inventario aunque haya un fallo.
- Las consultas usan parámetros (los signos `?`). El valor que manda el modelo nunca se pega dentro del texto del SQL, así que no hay inyección posible.

Alternativa descartada. Text-to-SQL, es decir, dejar que el modelo escriba la consulta. Es más flexible, pero el modelo puede escribir SQL incorrecto o lento, y es difícil garantizar qué devuelve. En una demo en directo, lo predecible gana.

En una frase: "El modelo nunca escribe SQL; elige filtros, y una capa de datos de solo lectura hace la consulta".

### 5.2 Conocimiento como datos (`dominio.py`)

Qué es. Las reglas del acero están escritas como tablas de Python: qué nombres tiene cada grado (316L, AISI 316L, 1.4404, S31603 son lo mismo), qué grado puede sustituir a cuál y con qué justificación, y cuánto pesa cada factor en la prioridad comercial.

Por qué. Hay dos formas de darle conocimiento a un sistema así: escribirlo en el prompt y esperar que el modelo lo aplique, o escribirlo en código y aplicarlo siempre. La segunda es auditable (un técnico de Acerinox puede leer la tabla y corregirla), se puede probar con tests y da el mismo resultado todas las veces.

La regla más importante de este fichero: las sustituciones solo van hacia arriba. A quien pide 316 se le puede ofrecer 316L o 2205, que cumplen o superan. A quien pide 316L nunca se le ofrece 304. Hay un test que lo comprueba.

En una frase: "Las reglas técnicas no están en el prompt, están en una tabla que un ingeniero de materiales puede revisar".

### 5.3 Registro de herramientas (`herramientas.py`)

Qué es. Una lista, `REGISTRO`, donde cada entrada junta dos cosas: la función Python que hace el trabajo y la descripción que ve el modelo. De esa única lista salen la lista de herramientas que se envía al modelo (`esquemas()`) y el despachador que ejecuta la que el modelo pide (`ejecutar()`).

Por qué. Si la descripción y la función vivieran en sitios distintos, tarde o temprano dejarían de coincidir. Con un registro, añadir una herramienta es añadir una entrada.

En una frase: "Añadir una capacidad nueva al asistente es añadir una función y su descripción en un solo sitio".

### 5.4 Errores como respuesta (`herramientas.py`)

Qué es. Cuando el modelo llama mal a una herramienta (un grado que no existe, un parámetro inventado, un texto donde va un número), la herramienta no lanza una excepción que rompa el programa. Devuelve un mensaje pensado para el modelo: "Grado no reconocido: 317. Grados en inventario: 304, 304L, 316…".

Por qué. El modelo lee ese mensaje en la vuelta siguiente del bucle y se corrige solo, igual que una persona que lee un mensaje de error útil. Una llamada equivocada cuesta una vuelta más, no una demo caída.

En una frase: "Los errores del modelo se le devuelven con instrucciones para corregirse, en lugar de romper el sistema".

### 5.5 Ajuste con puntuación (`herramientas.py`, función `_ajuste`)

Qué es. La búsqueda no es un filtro de sí o no. Cada producto recibe una puntuación de 0 a 100 y una lista de notas que explican cada descuento. Hay dos descuentos:

- Corte: una bobina de 1500 mm sirve para un pedido de 1250 mm, pero hay que cortarla. Pierde entre 5 y 25 puntos según cuánto sobre.
- Stock parcial: si el producto cubre solo el 69 % de los kilos pedidos, pierde puntos en proporción.

A igual puntuación, sale primero el producto que lleva más días parado en el almacén.

Por qué. El enunciado del reto dice "propone exactamente los productos que mejor se ajustan". Un comercial real sabe que un material más ancho se corta; un filtro exacto no lo sabe y diría que no hay stock.

Un detalle de dominio: el corte solo aplica a productos planos (bobina, chapa, fleje). En barras y tubos el diámetro tiene que coincidir, porque una barra de 200 mm no sirve para quien pide una de 20.

En una frase: "No filtramos, puntuamos, y cada punto perdido tiene una explicación que el comercial puede leer".

### 5.6 Prioridad comercial y uso de la demanda (`herramientas.py`, `prioridad_comercial`)

Qué es. Cuando varios productos sirven técnicamente, esta herramienta los ordena por lo que más le conviene vender a Acerinox. Combina cuatro factores con pesos fijos: días de rotación (40 %), dinero inmovilizado en ese stock (25 %), margen (20 %) y cobertura (15 %). La cobertura es cuántos meses de demanda cubre el stock actual de ese grado y formato, y se calcula con el histórico de pedidos.

Por qué. Es el argumento de negocio del proyecto. Un asistente que solo encuentra productos ahorra tiempo; uno que además empuja el stock parado libera dinero. Lo segundo es lo que se puede medir en euros en el pitch.

Un detalle: el margen se usa para calcular la prioridad, pero no se le enseña al modelo. La respuesta puede terminar en un correo a un cliente, y el margen es un dato interno.

En una frase: "Entre opciones válidas, el asistente recomienda primero la que libera stock parado, y dice por qué".

### 5.7 Adaptador de proveedor (`proveedor.py`)

Qué es. El agente no habla el idioma de la API de Claude. Habla uno propio, muy simple, con tres tipos de mensaje: del usuario, del asistente y resultados de herramientas. La clase `ProveedorAnthropic` traduce ese formato al de la API y la respuesta de vuelta.

`ProveedorLLM` es el contrato: dice que un proveedor es cualquier cosa que tenga un método `completar` que recibe instrucciones, mensajes y herramientas y devuelve una `RespuestaLLM`. En Python eso se llama `Protocol`, y no hace falta heredar de nada para cumplirlo.

Por qué. Dos motivos. Si el día del evento dan créditos de Azure OpenAI, se escribe otra clase con el mismo método y el agente no cambia. Y los tests pueden usar un modelo falso, que es el patrón siguiente.

Un detalle técnico que te pueden preguntar: el campo `crudo`. La respuesta original del proveedor se guarda entera y se reenvía sin tocar en la vuelta siguiente. La API de Claude necesita recibir de vuelta sus propios bloques de razonamiento y de llamada a herramienta tal como los generó; si guardas solo el texto, la siguiente petición falla.

En una frase: "El agente no sabe qué modelo hay detrás; cambiar de proveedor es escribir un adaptador de sesenta líneas".

### 5.8 Inyección de dependencias y modelo guionizado (`agente.py`, `tests/`)

Qué es. `Asistente` no crea su proveedor ni su inventario: los recibe al construirse. En producción recibe el proveedor real. En los tests recibe `ModeloGuionizado`, una clase de diez líneas que devuelve respuestas escritas a mano, una por llamada.

Por qué. Permite probar toda la lógica (el bucle, las herramientas, el validador) en centésimas de segundo, sin red, sin clave y sin coste. Y permite probar casos que con un modelo real casi nunca ocurren, como "el modelo se inventa un id dos veces seguidas".

En una frase: "La lógica se prueba sin IA: en los tests el modelo es un guion, así que sabemos exactamente qué hace el sistema en cada situación".

### 5.9 Guardarraíl determinista (`validador.py`)

Qué es. Un comprobador que no usa IA. Durante la conversación, cada id que devuelve una herramienta se apunta en un conjunto llamado evidencia. Cuando el modelo da su respuesta final, se buscan en el texto todos los ids de producto y se clasifican:

- Inventado: no existe en el inventario.
- Sin evidencia: existe, pero ninguna herramienta lo devolvió en esta conversación.

Si aparece cualquiera de los dos, el modelo recibe un aviso y una sola oportunidad de reescribir. Si vuelve a fallar, su texto se descarta entero y se muestra uno construido por código con los productos que sí devolvieron las herramientas.

Por qué. "No inventes productos" escrito en un prompt es una petición. Esto es una garantía: no depende de que el modelo obedezca.

El límite, dicho con honestidad: el validador comprueba ids, no cifras. Si el modelo citara un producto real pero escribiera mal su stock en la frase, no lo detectaría. Lo que protege al usuario en ese caso es que la tabla de productos se lee de la base de datos.

En una frase: "La tasa de productos inventados es cero por construcción, no por confianza en el modelo".

### 5.10 Agente sin estado (`agente.py`)

Qué es. `Asistente` no guarda nada entre preguntas. La conversación (mensajes y evidencia) vive en un objeto `Conversacion` que guarda quien llama: la interfaz, el script de evaluación o un test.

Por qué. Un mismo asistente puede atender muchas conversaciones a la vez sin que se mezclen, y la evaluación puede empezar cada caso con una conversación limpia.

Y una protección: el bucle tiene un tope de 8 vueltas por pregunta. Si el modelo se quedara pidiendo herramientas sin parar, se corta y se devuelve la respuesta segura.

### 5.11 La interfaz (`app.py`)

Qué es. Una sola página de Streamlit con tres zonas: a la izquierda los botones de ejemplo, en el centro el chat y a la derecha el panel "Cómo se ha llegado a la respuesta". Ese panel enseña el estado del verificador, la tabla de productos y cada paso con sus filtros y su tiempo.

Lo que hay que entender de Streamlit: cada vez que el usuario hace algo, el fichero entero se vuelve a ejecutar de arriba abajo. Lo que tiene que sobrevivir entre ejecuciones (la conversación, el historial visible) se guarda en `st.session_state`. El asistente se crea una sola vez con `st.cache_resource`.

Por qué el panel de la derecha. Es lo que convierte la demo en algo creíble: el jurado ve qué se buscó y con qué filtros, no solo un texto bonito. Si el verificador rechaza un primer borrador del modelo, el panel lo dice ("citaba ACX-99999; el modelo lo reescribió"), que es la mejor prueba de que el guardarraíl existe.

La interfaz no tiene lógica de negocio. Solo pinta lo que devuelve `Asistente.responder`. Por eso se puede probar igual que el agente: los tests le inyectan el modelo guionizado y comprueban qué aparece en pantalla.

En una frase: "La interfaz enseña el razonamiento, no solo la respuesta, y no contiene ninguna lógica propia".

### 5.12 La evaluación (`eval/`)

Qué es. `eval/preguntas.yaml` tiene 38 casos repartidos en 11 categorías: peticiones directas, sin stock, aplicación sin grado, designaciones alternativas, unidades no métricas, casos imposibles, preguntas ambiguas, inglés, cantidades que ningún producto cubre solo, prioridad comercial y correos de cliente. `eval/evaluar.py` ejecuta cada caso contra el asistente real y comprueba lo que se espera.

Lo importante es cómo se comprueba: con código, sobre el objeto `Respuesta`, sin otro modelo que haga de juez. Hay nueve comprobaciones, por ejemplo: todos los productos citados son de un grado aceptable, el primero citado es el que se esperaba, se llamó a cierta herramienta, no se citó ningún producto, la respuesta termina en pregunta. Que sea código significa que el resultado es repetible y que nadie puede decir que la nota es una opinión.

Qué mide: porcentaje de aciertos total y por categoría, ids que el verificador tuvo que rechazar, respuestas sustituidas, latencia media y p90, tokens y coste por consulta.

Una garantía que da confianza en los propios casos: hay tests que comprueban que las expectativas son ciertas con los datos actuales. Por ejemplo, que en los casos "sin stock" la búsqueda literal devuelve cero de verdad, y que en los de prioridad el producto esperado es el que devuelve `prioridad_comercial`.

Para qué te sirve, además de para el pitch: para decidir con datos. Ejecutas la evaluación con Opus y con Sonnet y eliges el modelo de la demo mirando aciertos, segundos y céntimos, no por intuición.

Límites que conviene conocer: los casos imposibles solo comprueban que no se recomienda nada, así que un asistente que siempre pidiera aclaración los aprobaría; no se comprueba que el correo lleve borrador de respuesta; y el coste está algo sobrestimado, porque cuenta los tokens leídos de caché a precio completo.

En una frase: "Tenemos 38 casos con comprobaciones automáticas, así que cada cambio de prompt o de modelo se mide en lugar de discutirse".

## 6. Decisiones que tomé por ti y que puedes cambiar

- Modelo por defecto: `claude-opus-5-5`. Razona mejor la parte técnica que Sonnet, pero es más lento y cuesta el doble. Se cambia con la variable `ANTHROPIC_MODEL` en `.env`. Lo sensato es decidirlo con la evaluación delante: si Sonnet acierta lo mismo, se usa Sonnet en la demo por velocidad.
- Profundidad de razonamiento: `LLM_EFFORT=medium`. Bajarlo a `low` acelera las respuestas.
- Reintento ante rechazos: la llamada a la API lleva el parámetro `fallbacks`, que reintenta en otro modelo si un filtro de seguridad rechaza la petición. Es una función en beta; si diera problemas, se quitan dos líneas en `proveedor.py`.
- Pesos de la prioridad comercial: están en `dominio.py`. Son una propuesta razonable, no un dato de Acerinox. Si alguien del jurado pregunta, la respuesta honesta es que son configurables y que los pondría el negocio.

## 7. Lo que no está probado todavía

- La interfaz se ha abierto en el navegador y arranca bien, pero el panel de razonamiento solo se ha visto en los tests, no pintado con una respuesta real.
- No se ha hecho ninguna llamada real al modelo, porque en la máquina no había clave de API. Los tests cubren el bucle, las herramientas y el validador, pero no la calidad de las respuestas ni si el prompt de sistema consigue el comportamiento esperado. Hasta ejecutar la evaluación con una clave, no hay un porcentaje de aciertos.
- La forma de los ids (`ACX-00001`) está en `config.py`. Con los datos reales habrá que ajustar ese patrón.

## 8. Preguntas que te pueden hacer, con su respuesta

¿Cómo sabéis que no se inventa productos? Porque no depende del modelo. Cada id de la respuesta se comprueba por código contra lo que devolvieron las herramientas en esa conversación. Si no coincide, el texto se descarta.

¿Por qué no usáis RAG con embeddings? Porque el inventario es una tabla, no documentos. Para buscar "espesor entre 1 y 2 mm con stock en Madrid" una consulta SQL es exacta y un buscador por similitud no. Las fichas técnicas sí son texto, pero son ocho y se piden por nombre de grado; no hace falta un buscador.

¿Y si el modelo llama mal a una herramienta? La herramienta le contesta con el error y los valores válidos, y el modelo lo corrige en la vuelta siguiente.

¿Qué cambiaríais con los datos reales? `datos.py`, el patrón de ids en `config.py` y, si hay grados nuevos, las tablas de `dominio.py`. El agente, el validador y las herramientas no cambian.

¿Cuánto cuesta cada consulta? Se mide en la evaluación: tokens de entrada y salida por consulta multiplicados por el precio del modelo. No des una cifra hasta haberla medido.

¿Por qué no LangChain o un framework de agentes? Porque el bucle completo son unas sesenta líneas que se pueden leer y explicar. Un framework añadiría dependencias y escondería justo la parte que hay que controlar: qué se ejecuta y qué se valida.

¿Qué pasa si se cae la API en la demo? El error llega como un mensaje legible y la aplicación sigue viva. Aparte, conviene llevar respuestas grabadas de las preguntas de ejemplo.

## 9. Cómo probarlo tú

```
cp .env.example .env          # y pon tu clave en ANTHROPIC_API_KEY
.venv/bin/python -m pytest    # los tests, sin clave
.venv/bin/python -m asistente "Necesito chapa 316 2B de 2 mm en Madrid"
```

El último comando imprime los pasos (qué herramienta se llamó y con qué), la respuesta y una línea final con el estado del verificador, las llamadas al modelo, los tokens y el tiempo.
