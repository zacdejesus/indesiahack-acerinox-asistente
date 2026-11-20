# Guion de la demo (3 minutos)

Regla general: enseñar antes de explicar. La demo empieza en el segundo 15, no en el minuto 2.

Todas las cifras de este guion salen de los datos sintéticos del prototipo. El día del evento hay que recalcularlas con los datos reales y con la evaluación ejecutada; las que aún no existen están marcadas con [MEDIR].

## 0:00 a 0:15. El problema, en dos frases

"Un comercial recibe un correo como este y tarda un rato en traducirlo a productos concretos de un inventario de miles de referencias. Mientras tanto, hay material que lleva meses parado en el almacén porque nadie se acuerda de ofrecerlo."

En pantalla: la aplicación ya abierta, con el correo de ejemplo a la vista.

## 0:15 a 1:15. Demo 1: el correo de un cliente

Pulsar el ejemplo del correo en inglés con varias líneas (una con designación EN 1.4404, otra con medidas en pulgadas).

Decir mientras responde:

- "El cliente no habla como nuestro inventario: dice 1.4404, pulgadas, y pide tres cosas a la vez."
- "El asistente separa las líneas, convierte unidades y busca cada una."

Cuando aparezca la respuesta, señalar el panel de razonamiento:

- "Aquí se ve cada búsqueda que ha hecho y con qué filtros. No es una caja negra."
- "Y al final deja el borrador de respuesta al cliente, en su idioma."

## 1:15 a 2:00. Demo 2: no hay stock

Pulsar el ejemplo de la chapa 316 2B de 2 mm en Madrid.

- "No hay. En lugar de decir que no, propone 316L en el mismo almacén y explica por qué es válido: misma resistencia a la corrosión y mejor soldabilidad."
- "Esa regla de sustitución no se la inventa el modelo: está en una tabla que un técnico de Acerinox puede revisar, y solo permite sustituir por un grado igual o superior."

Señalar el indicador del verificador:

- "Cada producto que aparece en la respuesta se comprueba por código contra el inventario. Si el modelo citara algo que no ha salido de una búsqueda, la respuesta se descarta. La tasa de productos inventados es cero por construcción."

## 2:00 a 2:30. Demo 3: el argumento de negocio

Pulsar el ejemplo del 430 BA para electrodomésticos.

- "Hay varias chapas que sirven. Recomienda primero esta, que lleva 280 días en el almacén."
- "Entre opciones técnicamente válidas, el asistente empuja el stock parado."

## 2:30 a 3:00. Impacto y cierre

- "En este inventario de prueba hay 30 referencias con más de 180 días de rotación: 408 toneladas y 1,6 millones de euros inmovilizados. Cada consulta es una oportunidad de darles salida."
- "Lo hemos medido: [MEDIR] % de aciertos en [MEDIR] casos de prueba, cero productos inventados, [MEDIR] segundos de media y [MEDIR] céntimos por consulta."
- "Y está hecho para los datos reales: el acceso a datos es un solo fichero."

Frase final: "Un asesor que responde en segundos, que no se inventa nada y que vende primero lo que más conviene vender."

## Si algo falla en directo

- Sin red o sin API: la aplicación muestra el error y sigue viva. Tener capturas o un vídeo corto de las tres demos como respaldo.
- Respuesta lenta: seguir hablando sobre el panel de razonamiento de la demo anterior.
- Respuesta distinta a la ensayada: no importa; señalar el panel de pasos y el verificador, que siempre están.

## Preguntas probables del jurado

Están respondidas en la sección 8 de `docs/arquitectura.md`.

## Antes de presentar

- Ejecutar la evaluación y sustituir cada [MEDIR] por su número real.
- Ensayar las tres demos al menos cinco veces con el modelo y la configuración definitivos.
- Recalcular las cifras de stock parado con los datos reales.
