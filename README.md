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

Cada tarea posee archivos **exclusivos** → no hay conflictos de merge. Los contratos
de cada módulo (firmas de funciones y dataclasses) están fijados en los stubs de
`src/hospital/` desde el primer commit: **nadie espera a nadie**, se programa contra
la firma. Cada quien agrega **un `assert`** a `tests/test_modelo.py`.

### T1 — Datos y salidas · `params.py`, `intercambio.py`, `outputs.py`
- [ ] `params.py`: leer `data/raw/*.xlsx` → dataclasses (`Instalacion`, `ZonaHeridos`, `Suministro`, `ParamsSistema`). Volcar `data/processed/parametros.json`.
- [ ] `params.py`: **normalizar todas las tasas a base horaria** (`tasa_hora = tasa_dia / 24`) y dejarlo documentado en el código.
- [ ] `params.py`: construir la **matriz de ruteo** `R[zona][instalacion]` (fila-estocástica). Z3 → reparte a Z1/Z4/Z2. Documentar el supuesto.
- [ ] `params.py`: repartir el consumo/día agregado de cada suministro por gravedad (pesos leve/mod/grave) → coeficientes `c[suministro][gravedad]` en paciente-hora. Documentar supuesto.
- [ ] `intercambio.py`: `cargar_demanda_grupo1(path) -> DataFrame|None` (zona × bloque → heridos adicionales). Con CSV vacío devuelve `None` (escenario B se salta limpio).
- [ ] `intercambio.py`: `aplicar_demanda(params, demanda)` → nuevos pools de heridos por zona/bloque.
- [ ] `outputs.py`: `tabla_saturacion(res)` → `outputs/tables/saturacion_por_bloque.csv` (% ocupación por instalación × bloque, media + IC95).
- [ ] `outputs.py`: `tabla_cuellos_botella(res)` → ranking de horas-recurso bloqueadas.
- [ ] `outputs.py`: `tabla_recursos_minimos(res)` → barrido +camas/+médicos/+sangre hasta mortalidad evitable < 15 %.

### T2 — Motor DES · `des.py`, `simulacion.py`
- [ ] `des.py`: calendario de eventos con `heapq` de `(t, seq, tipo, datos)`. Tipos: `LLEGADA`, `INICIO_ATENCION`, `FIN_ATENCION`, `MUERTE_EN_COLA`, `TICK_SD`, `FIN_BLOQUE`.
- [ ] `des.py`: llegadas por **thinning de Poisson no homogéneo** (Lewis–Shedler), λ(t) por tramo horario sobre el pool restante de cada zona.
- [ ] `des.py`: severidad por multinomial con las proporciones reales de cada zona.
- [ ] `des.py`: **recursos por gravedad** — grave → UCI+médico(+quirófano); moderado → cama general+médico; leve → solo médico (no ocupa cama).
- [ ] `des.py`: colas con prioridad grave > moderado > leve, FIFO dentro de cada nivel.
- [ ] `des.py`: tiempo de servicio `Exp(1/T_s)` × factor_fatiga × factor_escasez; modo emergencia (4→8 pac/médico) al cruzar umbral de ocupación.
- [ ] `des.py`: `MUERTE_EN_COLA` agendada al encolar (`Exp(1/T_tolerancia_s)`), cancelada si entra a atención antes → **mortalidad evitable**. Mortalidad clínica `p_s` al `FIN_ATENCION` → **inevitable**.
- [ ] `des.py`: **contadores de horas-recurso bloqueadas** por tipo (cama UCI / cama general / médico / suministro k) — instrumentar desde ya, alimenta la Pregunta 1.
- [ ] `simulacion.py`: bucle principal, snapshot en `FIN_BLOQUE`, **acoplamiento bidireccional** DES↔SD (paciente en atención → outflow de suministros + carga de fatiga; stock en 0 → recurso bloqueado; energía baja → servicio más lento).
- [ ] `simulacion.py`: `correr(params, seed, intervencion=None) -> ResultadoCorrida`.

### T3 — Sustrato SD · `sd.py`
- [ ] `sd.py`: `EstadoSD` con 7 stocks de suministros + `energia[instalacion]`.
- [ ] `sd.py`: `derivadas(estado, cargas, params)` → `dS_k/dt = -Σ c_{k,s}·N_s·(S_k/(S_k+ε))` (se detiene solo en 0, sin clamps).
- [ ] `sd.py`: `dE_f/dt = -α·(carga_f/cap_f) + β·(1−E_f)` (fatiga con recuperación).
- [ ] `sd.py`: `paso(f, y, t, dt, metodo)` con `metodo ∈ {"euler","rk4"}`. Comentar la relación con las fórmulas de EDO/RK4 de clase.
- [ ] `sd.py`: helper `comparar_integradores()` para la gráfica Euler vs RK4 del reporte.

### T4 — Monte Carlo, gráficas y notebook · `montecarlo.py`, `plots.py`, `.ipynb`
- [ ] `montecarlo.py`: `correr_replicas(params, n=30, intervencion=None)` con seeds `default_rng(1000+r)`. Muestrear heridos iniciales, multiplicadores de λ y de servicio, presentismo del personal (~85 %), tasas de mortalidad.
- [ ] `montecarlo.py`: agregación **IC 95 % por percentiles 2.5 / 97.5** (los tiempos de colapso son sesgados).
- [ ] `montecarlo.py`: `bloque_de_colapso(res)` por instalación (primer bloque con ocupación 100 % y cola creciente sostenida).
- [ ] `montecarlo.py`: `muertes_evitables_vs_base(res_base, res_g1)` — primeras 48 h (8 bloques). *(bloqueado hasta tener el CSV real del Grupo 1)*
- [ ] `plots.py`: series temporales con banda de IC95, gráfica de saturación por instalación, ranking de cuellos de botella, comparación base vs G1.
- [ ] `.ipynb`: ~15 celdas que **solo importan de `src/hospital` y grafican** — narrativa del reporte (paradigma → ODD → resultados → intercambio → limitaciones).

### Compartido
- [ ] Intervenciones (`simulacion.py`, dataclass `Intervencion`): carpas de expansión (+camas desde bloque k), reasignación de médicos entre instalaciones, derivación de leves a triaje aparte.
- [ ] Cuando llegue el intercambio: reemplazar `data/intercambio/grupo1_demanda.csv` y re-ejecutar el notebook. Cero cambios de código.

## Preguntas del grupo

1. **Análisis propio:** ¿qué instalación colapsa primero, en qué bloque, y cuál es el recurso cuello de botella? → `montecarlo.bloque_de_colapso` + `outputs.tabla_cuellos_botella`.
2. **Integración:** muertes evitables adicionales en 48 h (base vs demanda real del Grupo 1) e intervención que más las reduce. → **bloqueado hasta el intercambio presencial.**
