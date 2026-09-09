# Saturación hospitalaria y recursos críticos

**De:** Grupo 2 (Sistema Hospitalario y Atención Médica)  
**Para:** Grupo 7  
**Escenario:** Ciudad UVG, sismo M6.8, horizonte de 72 h en 12 bloques de 6 h  
**Base estadística:** 30 réplicas de Monte Carlo. Todo valor es la media entre réplicas con su
intervalo de confianza del 95 % (t de Student, n−1 grados de libertad).

## Resumen

- Llegan a pedir atención **11 313 heridos** (IC 11 071–11 555), el 96 % de los 11 773 estimados en T0.

- Reciben atención **8 434** (IC 8 266–8 602). Mueren esperando **2 787** (IC 2 601–2 973): una mortalidad evitable del **24.5 %** (IC 23.2–25.8).

- **Las camas no se saturan.** Las camas generales no pasan del 60 % y las UCI del 25 % en ningún bloque ni instalación.

- **Lo que se satura es el personal médico y los quirófanos**, ambos al 100 % desde el bloque 2 (hora 6) y hasta el bloque 5 o 6.

- De los cinco recursos evaluados, **solo aumentar el personal médico reduce la mortalidad**. Ampliar camas, UCI o sangre no cambia ningún resultado.

## 1. Saturación hospitalaria

Ocupación media en porcentaje, por instalación y recurso, en cada bloque de 6 h. El Centro Salud Z3 está cerrado y no recibe pacientes, por lo que no aparece.

| Instalación            | Recurso             |    1 |       2 |       3 |       4 |       5 |       6 |       7 |    8 |    9 |   10 |   11 |   12 |
| ---------------------- | ------------------- | ---: | ------: | ------: | ------: | ------: | ------: | ------: | ---: | ---: | ---: | ---: | ---: |
| Hospital General UVG   | Camas generales     |   36 |      44 |      44 |      37 |      22 |       9 |       5 |    4 |    4 |    4 |    3 |    3 |
| Hospital General UVG   | Camas UCI           |   23 |      25 |      25 |      25 |      25 |      25 |      24 |   23 |   23 |   23 |   22 |   20 |
| Hospital General UVG   | **Quirófanos**      |   92 | **100** | **100** | **100** | **100** |      99 |      97 |   93 |   92 |   91 |   89 |   81 |
| Hospital General UVG   | **Personal médico** |   92 | **100** | **100** | **100** | **100** | **100** |      89 |   64 |   41 |   19 |   14 |   11 |
| Clínica Z2             | Camas generales     |   37 |      47 |      47 |      42 |      25 |      10 |       5 |    3 |    2 |    2 |    2 |    1 |
| Clínica Z2             | Camas UCI           |   23 |      25 |      25 |      25 |      25 |      24 |      23 |   25 |   22 |   23 |   21 |   21 |
| Clínica Z2             | **Quirófanos**      |   91 | **100** | **100** | **100** | **100** |      97 |      92 |   98 |   88 |   92 |   85 |   84 |
| Clínica Z2             | **Personal médico** |   91 | **100** | **100** | **100** | **100** | **100** |      90 |   64 |   38 |   12 |    8 |    6 |
| Hospital Regional Este | Camas generales     |   32 |      45 |      44 |      35 |      18 |       6 |       2 |    1 |    1 |    0 |    0 |    0 |
| Hospital Regional Este | Camas UCI           |   19 |      22 |      22 |      22 |      22 |      21 |      20 |   19 |   19 |   18 |   16 |   14 |
| Hospital Regional Este | **Quirófanos**      |   86 | **100** | **100** | **100** | **100** |      95 |      91 |   88 |   86 |   79 |   73 |   61 |
| Hospital Regional Este | **Personal médico** |   81 | **100** | **100** | **100** | **100** |      99 |      86 |   57 |   31 |    8 |    4 |    3 |
| Puesto Salud Z5        | Camas generales     |   53 |      60 |      60 |      59 |      52 |      40 |      35 |   26 |   18 |   14 |   15 |   18 |
| Puesto Salud Z5        | Camas UCI           |    0 |       0 |       0 |       0 |       0 |       0 |       0 |    0 |    0 |    0 |    0 |    0 |
| Puesto Salud Z5        | Quirófanos          |    0 |       0 |       0 |       0 |       0 |       0 |       0 |    0 |    0 |    0 |    0 |    0 |
| Puesto Salud Z5        | **Personal médico** |   92 | **100** | **100** | **100** | **100** | **100** | **100** |   99 |   94 |   81 |   73 |   61 |

**Precisión de la tabla.** En los bloques 1 a 6, donde ocurre la saturación, el intervalo de confianza del 95% de cada celda no excede ±2.2 puntos porcentuales para el personal médico, ±2.1 para UCI, ±8.6 para quirófanos y ±8.8 para camas generales. En los bloques 7 a 12, cuando el sistema drena, los intervalos se ensanchan hasta ±30 puntos. El intervalo celda por celda de camas y UCI está en `saturacion_por_bloque.csv`.

## 2. Recursos críticos

Los dos cuellos de botella, ordenados por el efecto de relajarlos sobre la mortalidad evitable y no por sus horas de bloqueo.

| #   | Recurso             | Instalación                       | Falla desde       | Evidencia                                                                                                                                                                                            |
| --- | ------------------- | --------------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **Personal médico** | Las cuatro instalaciones          | Bloque 2 (hora 6) | Ocupación 100 % (IC 100–100) en los bloques 2 a 5. Cola de 2 954 pacientes (IC 2 847–3 060) en el Hospital General UVG en el bloque 3. Hasta 59.1 h bloqueadas (IC 56.4–61.8) en el Puesto Salud Z5. |
| 2   | **Quirófanos**      | Los tres hospitales con quirófano | Bloque 2 (hora 6) | Ocupación 100 % (IC 100–100) en los bloques 2 a 5 y por encima del 80 % hasta el final. 50.1 h bloqueadas (IC 46.8–53.5) en la Clínica Z2.                                                           |

El criterio de colapso (ocupación cercana al 100% con cola creciente sostenida durante al menos dos bloques) lo cumplen el **Hospital General UVG** y el **Hospital Regional Este**, ambos por **personal médico** y ambos en el **bloque 2**.

### El segundo cuello de botella no es independiente

Se relajó cada recurso un 20% por separado, con las mismas semillas en todos los escenarios para poder comparar réplica contra réplica.

| Escenario             | Incremento aplicado | Muertes evitables (IC 95 %) | Diferencia frente al base (IC 95 %) | ¿Reduce la mortalidad?     |
| --------------------- | ------------------- | --------------------------: | ----------------------------------: | -------------------------- |
| Base                  | —                   |         2 787 (2 601–2 973) |                                   — | —                          |
| +20 % personal médico | +15 médicos         |         2 407 (2 245–2 570) |              **−379** (−415 a −344) | **Sí, −13.6 %**            |
| +20 % quirófanos      | +1 por hospital     |         2 811 (2 635–2 987) |                      +24 (−9 a +58) | No, indistinguible de cero |
| +20 % camas generales | +126 camas          |         2 787 (2 601–2 973) |                                   0 | No                         |
| +20 % camas UCI       | +10 camas           |         2 787 (2 601–2 973) |                                   0 | No                         |
| +20 % sangre          | +96 unidades        |         2 787 (2 601–2 973) |                                   0 | No                         |

Los quirófanos están saturados, pero **ampliarlos por sí solos no reduce la mortalidad**: el intervalo de la diferencia incluye el cero y su valor central va en la dirección contraria. La razón es que la restricción activa sigue siendo el médico, y una cirugía ocupa a un médico ocho horas. Sin más personal, abrir quirófanos redistribuye tiempo médico hacia procedimientos largos en lugar de aumentar el total de pacientes atendidos.

Camas generales, UCI y sangre dan una diferencia de **exactamente cero**: con semillas comunes, aumentarlos no cambia una sola réplica. Nunca fueron restricción.

### Quién muere esperando

| Gravedad | Llegan | Atendidos | Mueren esperando | % que muere esperando |
| -------- | -----: | --------: | ---------------: | --------------------: |
| Grave    |    897 |        54 |              824 |            **91.8 %** |
| Moderado |  2 473 |     2 021 |              396 |                16.0 % |
| Leve     |  7 943 |     6 359 |            1 567 |                19.7 % |

La diferencia entre las columnas corresponde a las muertes clínicas inevitables y a los pacientes que siguen en el sistema al cumplirse las 72 h.

Los heridos graves son el grupo que el sistema no alcanza a atender: requieren médico, quirófano y UCI a la vez, y su ventana de supervivencia es la más corta.

El ranking completo de recursos críticps está en `cuellos_botella.csv`.

## 3. Extra: Recursos adicionales mínimos para mortalidad evitable < 15 %

**Definición usada:** mortalidad evitable = fallecidos en espera / heridos que llegan a pedir atención dentro de las 72 h. El denominador es 11 313 en promedio, no los 11 773 estimados en T0: el 4% restante no alcanza a llegar dentro del horizonte.

| Recurso             | Incremento mínimo que baja del 15 % | Mortalidad evitable resultante |
| ------------------- | ----------------------------------- | -----------------------------: |
| **Personal médico** | **+60 médicos**                     |                     **13.3 %** |
| Camas generales     | No lo alcanza                       |                         23.1 % |
| Camas UCI           | No lo alcanza                       |                         23.1 % |
| Quirófanos          | No lo alcanza                       |                         24.0 % |
| Sangre              | No lo alcanza                       |                         23.1 % |

> **Limitación de este apartado:** este barrido se corrió con **10 réplicas y sin intervalo de confianza**, a diferencia del resto del documento. Las cifras son indicativas y no concluyentes (sirven para señalar que el personal médico es la única palanca que alcanza el umbral por sí sola, no para fijar la cantidad exacta). Los apartados 1 y 2 sí llevan 30 réplicas e intervalos.

## Notas de interpretación

- **El 99% de las muertes evitables ocurre en las primeras 48 h**: 2 759 (IC 2 578–2 939) de las 2 787 totales. La ventana de intervención útil es corta.

- Las muertes clínicas inevitables son solo **60** (IC 54–66). Prácticamente toda la mortalidad del escenario es por espera, no por gravedad de la lesión.

- Dos supuestos propios pesan sobre estas cifras y conviene tenerlos presentes: las **ventanas de supervivencia en cola** (media de 4 h para graves, 24 h para moderados y 120 h para leves) y el **consumo fijo de insumos por procedimiento**. El archivo fuente no define ninguno de los dos. Cambiarlos mueve la mortalidad evitable, aunque no altera qué recurso es el cuello de botella: el personal médico se satura antes de que cualquier insumo se agote.

- Los insumos médicos **no** son restricción en este escenario. El material quirúrgico es el que más se acerca a agotarse, con 2.0 h bloqueadas (IC −0.3 a 4.3) frente a las 59.1 h del personal médico.
