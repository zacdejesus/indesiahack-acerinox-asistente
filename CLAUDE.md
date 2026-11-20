# Asistente de inventario comercial (IndesIAhack 2026, reto Acerinox)

Prototipo con datos sintéticos. La explicación del diseño está en `docs/arquitectura.md`.

## Comandos

```
.venv/bin/python -m pytest                    # tests, sin clave ni red
.venv/bin/python -m asistente "pregunta"      # asistente por terminal
.venv/bin/streamlit run app.py                # interfaz actual
.venv/bin/python -m eval.evaluar              # evaluación (38 casos)
LLM_PROVIDER=local <comando>                  # usar el modelo local (servidor MLX en 127.0.0.1:8080)
```

## Principio de diseño: todo lo externo va detrás de un adaptador

El núcleo (`asistente/agente.py`, `herramientas.py`, `dominio.py`, `validador.py`) no debe saber de dónde vienen los datos, qué modelo responde ni qué interfaz lo usa. Cada cosa externa se conecta con un adaptador que cumple un contrato, y se elige por configuración en un solo sitio.

- Modelo de lenguaje: hecho. Contrato `ProveedorLLM` en `asistente/proveedor.py`, con adaptadores para Claude y para APIs compatibles con OpenAI. Se elige con `LLM_PROVIDER`.
- Datos: pendiente. Hoy `asistente/datos.py` es una clase concreta sobre SQLite. El objetivo es convertirla en un contrato (igual que `ProveedorLLM`) con un adaptador por origen: SQLite, otra base de datos, una API o lo que entregue Acerinox. Las herramientas solo deben usar los métodos del contrato.
- Interfaz: pendiente. Hoy es Streamlit (`app.py`). El objetivo es poder usarlo como aplicación web instalable (PWA), de las que se añaden a la pantalla de inicio. Eso exige exponer el asistente por una API HTTP; la interfaz no debe contener lógica de negocio.

## Dirección del producto (acordada el 2026-11-16, sin implementar)

Para la demo del hackathon se quiere una PWA además de Streamlit, con dos roles:

- Comercial: ve stock, almacén, rotación, prioridad comercial y el panel de razonamiento.
- Cliente: ve disponibilidad y alternativas, nunca datos internos (margen, rotación, motivos de prioridad). Lo que un rol no puede ver se filtra en el servidor (columnas, herramientas y prompt por rol), no en la pantalla. El rol sale del inicio de sesión, nunca de un parámetro del navegador.

Notificaciones push (Web Push con claves VAPID), dos casos:

- Petición de oferta: el cliente pulsa "pedir oferta" y al comercial le llega un push.
- Aviso de reposición: si un usuario pide un material sin stock, puede dejar un aviso; cuando vuelve a haber stock le llega un push. La comprobación la hace código (la misma función `buscar_stock` con los criterios guardados), sin modelo. Cada aviso es además un registro de demanda no atendida.

Avisos, peticiones de oferta, usuarios y suscripciones push son datos propios de la aplicación: van en un almacén aparte. El inventario sigue siendo de solo lectura.

Al añadir código, no meter en el núcleo SQL, llamadas HTTP, nombres de proveedor ni nada de Streamlit.

## Condiciones del hackathon (kit del participante, docs/organizacion/)

- Plataforma de la organización: Microsoft Azure, con Databricks (notebooks y datos del reto en volúmenes de Unity Catalog) y GitLab para entregar el código. Acceso con el correo de inscripción y Microsoft Authenticator.
- Las claves de los servicios de IA se publican en Teams el día 25; no está confirmado qué modelos habrá (probablemente Azure OpenAI). El adaptador de modelo debe poder apuntar a ellos sin tocar el núcleo.
- Los datos reales llegarán como ficheros o tablas en Databricks, no como SQLite: otra razón para el adaptador de datos.
- Entrega: código subido al repositorio el viernes 27 a las 10:00; exposición ante el jurado a las 11:00.
- Criterios comunes: creatividad e innovación, nivel de avance, impacto económico y social, uso responsable de la IA (transparencia, equidad, privacidad) y pitch.

## Otras reglas del proyecto

- El modelo nunca escribe SQL ni aporta datos: elige filtros y redacta. Todo producto citado pasa por el validador.
- Las reglas del acero van en `asistente/dominio.py` como tablas, no en el prompt.
- Código e identificadores en español; comentarios breves que explican el porqué.
- Cada cambio de comportamiento lleva su test con el modelo guionizado (`tests/test_asistente.py`).

## Forma de trabajar

- Explicar la arquitectura y los patrones mientras se construyen, en lenguaje llano.
- No hacer commit hasta que el autor confirme que lo ha entendido.
