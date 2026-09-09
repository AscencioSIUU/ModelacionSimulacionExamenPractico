# **Examen Práctico: Sistema Hospitalario y Atención Médica**

Un sismo de magnitud 6.8 golpea **Ciudad UVG** el 14 de noviembre a las 4:00 AM. La ciudad tiene cinco zonas y cada grupo del curso modela uno de sus subsistemas durante las **72 horas** siguientes, reportadas en **12 bloques de 6 horas**.

El subsistema de nuestro grupo es el **sistema hospitalario y la atención médica**: cinco instalaciones con capacidad limitada de camas, UCI, quirófanos y personal, frente a 11,773 heridos que requieren atención.

El examen incluye un intercambio de información en dos direcciones:

- **Entra:** el Grupo 1 nos entrega su proyección de desplazados que requieren atención médica por zona y bloque de tiempo. Nosotros la incorporamos al modelo.
- **Sale:** nuestro grupo entrega al Grupo 7 un reporte de saturación hospitalaria y recursos críticos.

Las preguntas que nuestro equipo responde (antes y después del intercambio, respectivamente) son:

1. ¿Qué instalación colapsa primero, en qué bloque y por qué recurso?
2. ¿Cuántas muertes evitables adicionales produce la demanda del Grupo 1 en las primeras 48 horas, y qué intervención las reduce más?

## Descripción del Modelo y Simulación

El modelo es **híbrido**. Un motor de **eventos discretos** mueve a los pacientes uno por uno: llegan según un proceso de Poisson no homogéneo, se derivan a la instalación operativa más cercana con capacidad libre, compiten por médico, cama, UCI y quirófano en colas de prioridad estricta, y mueren si se les agota la ventana de supervivencia antes de ser atendidos. Sobre él corre un **sustrato de dinámica de sistemas** acotado, que drena los insumos médicos y la energía del personal integrando por Euler explícito con Δt = 0.25 h. Encima de todo, **Monte Carlo** con 30 réplicas de semilla independiente: nunca se reporta un valor puntual, siempre la media con su intervalo de confianza del 95% por t de Student.

Las siete reglas del modelo, R1 a R7, están documentadas celda por celda en los cuadernos. Todo lo que el archivo fuente no da vive en un único bloque editable marcado **S-1 a S-7** en `src/hospital/params.py`; ningún número del modelo está escrito fuera de ese módulo.

## Resultados

- En el escenario base llegan a pedir atención 11,313 heridos y **2,787 mueren esperando** (IC 95%: 2601–2973): una mortalidad evitable del **24.5%** (IC 23.2–25.8).

- **Las camas nunca se saturan.** Las generales no pasan del 60% ni las UCI del 25% en ningún bloque. Lo que se satura es el **personal médico y los quirófanos**, ambos al 100% desde el bloque 2. Las camas quedan libres porque no hay quién atienda a los pacientes que las ocuparían.
 
- La primera instalación en colapsar es el **Hospital General UVG por falta de médicos, en el bloque 2** (horas 6 a 12), con casi 2,800 pacientes en cola.

- De los cinco recursos evaluados, **solo aumentar el personal médico reduce la mortalidad**. Más camas, UCI o sangre no cambia ningún resultado, y ampliar quirófanos tampoco (sin más personal, una cirugía ocupa a un médico durante ocho horas).

- Al incorporar la demanda del Grupo 1 (**+9,100 heridos, un +77%**) se producen **4,288 muertes evitables adicionales en las primeras 48 horas** (IC 95%: 4142–4435), un aumento del 155%. El cuello de botella sigue siendo el personal médico, pero su rendimiento cae; los mismos refuerzos deben repartirse entre casi el doble de pacientes.

## Estructura del Repositorio

```
ModelacionSimulacionExamenPractico/
├── data/
│   ├── raw/
│   │   └── Grupo2_SistemaHospitalario.xlsx          # Datos de partida del examen
│   ├── intercambio/
│   │   ├── output_grupo2_demanda_medica.csv         # Entregado por el Grupo 1
│   │   └── grupo1_demanda.csv                       # Datos del Grupo 1 convertidos a nuestro esquema
│   └── processed/
│       └── parametros.json                          # Archivo auto-generado
├── src/
│   └── hospital/                                    # Código del modelo del sistema hospitalario
├── notebooks/
│   ├── SistemaHospitalario(Antes).ipynb             # Análisis propio y entrega al Grupo 7
│   └── SistemaHospitalario(Despues).ipynb           # Análisis posterior al intercambio con el Grupo 1
├── tests/
│   └── test_modelo.py                               # 47 pruebas del modelo
├── docs/
│   └── REPORTE-GRUPO7.md                            # Entregable al Grupo 7
└── outputs/
    ├── tables/                                      # Tres CSV generados para el Grupo 7
    ├── figures/                                     # Gráficas del reporte, auto-generadas
    └── cache/                                       # Réplicas de Monte Carlo, auto-generadas
```

## Cómo ejecutar

```bash
uv sync                                     # instala dependencias
uv run python tests/test_modelo.py          # 47 asserts, sin pytest
uv run jupyter lab                          # abre los dos cuadernos
uv run python -m hospital.params            # regenera data/processed/parametros.json
uv run python -m hospital.intercambio       # resumen del impacto de la demanda del Grupo 1
```

Conviene correr primero `SistemaHospitalario(Antes).ipynb`, porque `SistemaHospitalario(Despues).ipynb` reutiliza su escenario base desde el cache. La primera ejecución tarda unos 6 y 4 minutos respectivamente; las siguientes, segundos.

Borrar `outputs/cache/` obliga a recalcular todo sin cambiar ningún resultado. Las semillas son deterministas, así que las cifras salen idénticas con o sin cache.

## Integrantes

**Grupo 2**

- Cristian Túnchez (231359)
- Nadissa Vela (23764)
- Javier Linares (231135)
- Ernesto Ascencio (23009)
