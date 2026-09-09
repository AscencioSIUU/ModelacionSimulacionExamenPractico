# Grupo 2 — Sistema Hospitalario y Atención Médica

Modelo de simulación del colapso hospitalario de **Ciudad UVG** tras el sismo
M6.8 del 14 de noviembre (4:00 AM). Horizonte: 72 h en **12 bloques de 6 h**.
Examen Práctico CC2017 — Modelación y Simulación.

## Paradigma

Híbrido: **DES** (núcleo — pacientes discretos compitiendo por camas/personal,
teoría de colas con recursos finitos) + **SD acotado** (stocks que se drenan:
suministros y fatiga del personal) + **Monte Carlo** (≥30 réplicas, IC 95 %).

## Setup

```bash
uv sync
uv run python tests/test_modelo.py     # suite de asserts
uv run jupyter lab                      # abre notebooks/Grupo2_SistemaHospitalario.ipynb
```

## Estructura

```
data/raw/          Grupo2_SistemaHospitalario.xlsx  (datos de partida)
data/processed/    parametros.json  (generado por params.py)
data/intercambio/  grupo1_demanda.csv  (VACÍO hasta el intercambio presencial)
src/hospital/      código del modelo (un módulo por tarea, ver abajo)
notebooks/         Grupo2_SistemaHospitalario.ipynb  ← ENTREGABLE Canvas
tests/             test_modelo.py  (asserts planos, sin pytest)
outputs/tables/    CSVs para el Grupo 7
outputs/figures/   PNGs para el reporte
docs/              ExamenParcial.md, GRUPO2.md, reporte (fuera de git)
```

## Datos de partida (extraídos del Excel)

| Instalación | Zona | Camas | UCI | Quirófanos | Estado |
|---|---|---|---|---|---|
| Hospital General UVG | Z1 | 320 | 24 | 6 | Operativo |
| Clínica Z2 | Z2 | 80 | 4 | 1 | Operativo |
| Centro Salud Z3 | Z3 | 45 | 0 | 0 | **CERRADO** |
| Hospital Regional Este | Z4 | 210 | 18 | 4 | Operativo |
| Puesto Salud Z5 | Z5 | 20 | 0 | 0 | Capacidad mínima |

Heridos T0 (total requieren atención): Z1 4050 · Z2 948 · Z3 1640 · Z4 565 · Z5 4570.
Tiempos de atención: leve 0.5 h · moderado 3 h · grave 8 h.
Tasa de llegada al hospital: 18 %/h (0–6 h) · 7 %/h (6–24 h) · 2 %/h (>24 h).
Personal: 87 médicos, 142 enfermeras. Capacidad: 4 pac/médico normal, 8 en emergencia.

Dos desbalances estructurales son el eje del análisis: **Z3 cerrado** (1640 heridos
sin instalación) y **Z5** (4570 heridos vs 20 camas).

---

## Reparto de tareas (equipo de 4)

**El checklist de código por persona (P1–P4) está en [`TODO.md`](TODO.md).**
Cada persona tiene archivos exclusivos en `src/hospital/` → cero conflictos de merge.
Resumen:

| Persona | Módulos | Tema |
|---|---|---|
| P1 | `params.py` ✅, `intercambio.py`, `outputs.py` | Datos, intercambio Grupo 1, tablas para Grupo 7 |
| P2 | `des.py`, `simulacion.py` | Motor de eventos discretos + acoplamiento |
| P3 | `sd.py` | Stocks de suministros y fatiga, integradores Euler/RK4 |
| P4 | `montecarlo.py`, `plots.py`, el `.ipynb` | 30 réplicas, IC 95 %, gráficas, notebook |

## Preguntas del grupo

1. **Análisis propio:** ¿qué instalación colapsa primero, en qué bloque, y cuál es el recurso cuello de botella? → `montecarlo.bloque_de_colapso` + `outputs.tabla_cuellos_botella`.
2. **Integración:** muertes evitables adicionales en 48 h (base vs demanda real del Grupo 1) e intervención que más las reduce. → **bloqueado hasta el intercambio presencial.**
