# TODO — Grupo 2, reparto de trabajo (equipo de 4)

Modelo híbrido **DES (núcleo) + SD (acotado) + Monte Carlo**.
Cada persona tiene **archivos exclusivos** en `src/hospital/` → nunca editan el
mismo archivo → cero conflictos de merge.

Los **contratos** (firmas de funciones, dataclasses, nombres de campos) ya están
escritos en los stubs. Programen contra la firma: **nadie espera a nadie**.
`params.py` ya está implementado y funcionando — es la base de todos.

Cada persona agrega **su propio `assert`** en `tests/test_modelo.py`.
Correr siempre antes de hacer push:  `uv run python tests/test_modelo.py`

| Persona | Módulos propios | Tema |
|---|---|---|
| **P1** | `params.py` ✅, `intercambio.py`, `outputs.py` | Datos, intercambio Grupo 1, tablas para Grupo 7 |
| **P2** | `des.py`, `simulacion.py` | Motor de eventos discretos + acoplamiento |
| **P3** | `sd.py` | Stocks de suministros y fatiga, integradores Euler/RK4 |
| **P4** | `montecarlo.py`, `plots.py`, `notebooks/Grupo2_SistemaHospitalario.ipynb` | 30 réplicas, IC 95 %, gráficas, notebook entregable |

Orden sugerido: P1 y P3 pueden terminar ya (no dependen de nadie). P2 depende de
las firmas de P1/P3 (ya existen). P4 arma el notebook al final pero puede ir
escribiendo `plots.py` contra datos falsos desde el día 1.

---

## P1 — Datos, intercambio, salidas

### `params.py` ✅ HECHO
- [x] Dataclasses `Instalacion`, `ZonaHeridos`, `Suministro`, `ParamsSistema`, `Intervencion`
- [x] Valores del Excel transcritos, tasas normalizadas a **base horaria** (`/24`)
- [x] `matriz_ruteo()` — Z3 cerrada reparte a Z1/Z4; supuestos documentados en docstring
- [x] `coef_consumo()` — reparte consumo/día por gravedad → unidades por paciente-hora
- [x] `volcar_json()` → `data/processed/parametros.json`
- [x] `Intervencion` (config de política: `camas_extra`, `uci_extra`, `medicos_extra`, `sangre_extra`, `desde_bloque`, `derivar_leves_aparte`) — vive aquí, no en `simulacion.py`
- [ ] **Revisar** los supuestos de `matriz_ruteo` y `PESO_CONSUMO_GRAVEDAD` con el equipo y anotarlos en `docs/` para el reporte

### `intercambio.py` ✅ HECHO
- [x] `cargar_demanda_grupo1(path)` → `DataFrame | None` (valida columnas, zonas, rango de bloque, negativos)
- [x] `aplicar_demanda(params, demanda)` → `list[LlegadaExtra]` (zona, `t` en horas, conteos) ordenada por tiempo — `simulacion.correr` (P2) inyecta cada lote en el pool de la zona en `t = (bloque−1)·6`
- [x] `resumen_impacto(params, demanda)` → DataFrame de incremento de pool por zona (para 4 del reporte)
- [x] Test `_p1_intercambio_*`: fixture de 3 filas; CSV vacío / ausente → `None` sin reventar
- [x] Código listo: el día del intercambio solo se pega el CSV real, cero cambios

### `outputs.py` ✅ HECHO
- [x] `ResultadoMC` — **contrato de lo que P4 debe devolver** (arrays `ocup_camas`, `ocup_uci`, `bloqueo_horas`, `muertes_*`, `generados`)
- [x] `tabla_saturacion(res)` → `outputs/tables/saturacion_por_bloque.csv` (% ocupación camas/UCI por instalación × bloque, media + IC95 por percentiles). **Output (a) Grupo 7.**
- [x] `tabla_cuellos_botella(res, top=2)` → ranking de horas-recurso bloqueadas, columna `critico` para los 2 primeros. **Output (b) Grupo 7.**
- [x] `tabla_recursos_minimos(run_fn, params)` → barrido +camas / +camas UCI / +médicos / +sangre hasta tasa evitable < 15 % (`run_fn(interv)` inyectado por P4). **Output (c) Grupo 7.**
- [x] Las 3 escriben CSV y devuelven DataFrame. Test `_p1_outputs_tablas` con `ResultadoMC` falso.

---

## P2 — Motor DES + acoplamiento

### `des.py` ✅ HECHO (`Calendario`, `Evento`, `Paciente`, `Recursos`)
- [x] `llegadas_thinning(rng, params, zona, n_pool)` — Poisson **no homogéneo por thinning** (Lewis–Shedler): candidato `~Exp(LAMBDA_MAX·n_restante)`, aceptar con prob `lambda(t)/LAMBDA_MAX`. Comentar la fórmula de clase.
- [x] `puede_atender / tomar_recursos / liberar_recursos` según las reglas por gravedad:
  grave → UCI + médico (+ quirófano); moderado → cama general + médico; leve → solo médico
- [x] Severidad de cada herido: multinomial con `ZonaHeridos.proporciones()`
- [x] Colas con prioridad `PRIORIDAD` (grave > moderado > leve), FIFO dentro del nivel
- [x] Tiempo de servicio: `Exp(1/T_ATENCION_H[g])` × factor_fatiga (de `sd`) × factor_escasez
- [x] Modo emergencia: pac/médico 4→8 al cruzar `params.umbral_emergencia`
- [x] `MUERTE_EN_COLA` agendada al encolar (`Exp(1/T_TOLERANCIA_COLA_H[g])`); cancelar si `INICIO_ATENCION` llega antes → **muerte evitable**
- [x] Mortalidad clínica al `FIN_ATENCION` con `P_MORTALIDAD_CLINICA[g]` → **muerte inevitable**
- [x] `Recursos.bloqueo_horas[(instalacion, recurso)]` — acumular horas que hubo cola con ese recurso a 0. **Alimenta directo la Pregunta 1.**

### `simulacion.py` ✅ HECHO (`Intervencion` está en `params.py`)
- [x] `ResultadoCorrida` (una corrida) — de ahí P4 agrega el `ResultadoMC` de `outputs.py`
- [x] `correr(params, seed, intervencion=None, llegadas_extra=None) -> ResultadoCorrida`: bucle `while len(cal)`, despacho por `evento.tipo`. `llegadas_extra` = salida de `intercambio.aplicar_demanda`
- [x] Aplicar `Intervencion`: `camas_extra` / `uci_extra` suman capacidad desde `desde_bloque`; `medicos_extra` al pool; `sangre_extra` al stock inicial; `derivar_leves_aparte` saca a los leves de la cola de camas
- [x] `FIN_BLOQUE` cada 6 h → snapshot: ocup_camas, ocup_uci
- [x] `TICK_SD` cada `DT_SD=0.1` h → llamar `sd.paso` con las cargas actuales — **degradado a neutro** mientras `sd.py` (P3) no exista: ver contrato documentado al inicio de `simulacion.py`
- [x] **Acoplamiento DES→SD:** pacientes en atención = outflow de suministros + carga de fatiga
- [x] **Acoplamiento SD→DES:** suministro en 0 → bloquear el recurso ligado; energía baja → factor_fatiga > 1
- [x] Invariante: `atendidos + muertes_evitables + muertes_clinicas + en_sistema == generados`

---

## P3 — Sustrato SD

### `sd.py` (`paso()` con euler/rk4 y `comparar_integradores()` ya hechos)
- [x] `EstadoSD` — todos los stocks definidos en `params.suministros` + `energia[instalacion]` ∈ [0,1]
- [x] `derivadas(estado, cargas, params)`:
  `dS_k/dt = -Σ_s params.coef_consumo[k][s] · N_s(t) · (S_k/(S_k+EPS_STOCK))` — se frena solo en 0
- [x] `dE_f/dt = -params.fatiga_alfa·(carga_f/cap_f) + params.fatiga_beta·(1−E_f)`
- [x] Adaptador vector↔dict para que `paso()` (que usa `np.ndarray`) opere sobre `EstadoSD`
- [x] `factor_fatiga(energia_f)` que `des.py` consume para alargar el servicio (p. ej. `2 − energia_f`)
- [x] Comentar en el código la relación con las diapositivas: EDO de 1er orden, Euler vs RK4, error de truncamiento
- [x] Test: `comparar_integradores()` — error de RK4 < error de Euler contra `e^{-tasa·t}`

---

## P4 — Monte Carlo, gráficas, notebook

### `montecarlo.py`
- [ ] `correr_replicas(params, n=30, intervencion=None) -> outputs.ResultadoMC` — seeds `np.random.default_rng(1000+r)`, llama `simulacion.correr` y apila los resultados en los arrays `[r, i, b]` que define `outputs.ResultadoMC` (P1 ya consume ese contrato)
- [ ] Perturbaciones por réplica: heridos iniciales (±%), multiplicador de `lambda`, multiplicador de tiempos de servicio, presentismo del personal (~85 %), tasas de mortalidad
- [ ] Agregación: media + **IC 95 % por percentiles 2.5 / 97.5** (los tiempos de colapso son sesgados, no usar ±1.96σ)
- [ ] `bloque_de_colapso(res_mc)` por instalación = primer bloque con ocupación ≈100 % y cola creciente sostenida → **respuesta Pregunta 1**
- [ ] `muertes_evitables_vs_base(res_base, res_g1)` en las primeras 48 h (8 bloques) → **respuesta Pregunta 2** *(bloqueado hasta el intercambio)*
- [ ] `recurso_cuello_botella(res_mc)` = argmax de `bloqueo_horas` agregado

### `plots.py`
- [ ] `serie_con_banda(ax, t, media, lo, hi, label)` — helper base de todas las gráficas
- [ ] Ocupación de camas/UCI por instalación con banda IC95 (la gráfica clave del video)
- [ ] Barras: ranking de cuellos de botella
- [ ] Euler vs RK4 (usa `sd.comparar_integradores`)
- [ ] Base vs escenario Grupo 1 *(cuando haya datos)*
- [ ] Todas guardan PNG en `outputs/figures/`

### `notebooks/Grupo2_SistemaHospitalario.ipynb`  ← ENTREGABLE CANVAS
- [ ] ~15 celdas, **solo importa de `src.hospital` y grafica** — nada de lógica de modelo en el notebook
- [ ] Narrativa = estructura del reporte: paradigma → ODD → resultados (Pregunta 1) → intercambio (Pregunta 2) → limitaciones
- [ ] Celdas comentadas explicando la relación código ↔ fórmulas (EDO, RK4, thinning, matriz de flujo)
- [ ] Última celda: genera los 3 CSV de `outputs.py` para el Grupo 7

---

## Cuando llegue el intercambio presencial
1. Pegar los datos del Grupo 1 en `data/intercambio/grupo1_demanda.csv`
2. Re-ejecutar el notebook completo
3. **Cero cambios de código.** Solo se recalculan Pregunta 2 y las gráficas base-vs-G1
