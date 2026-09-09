"""
Suite de asserts planos (sin pytest).

Cada función `_p<N>_...` verifica una regla del modelo y se descubre sola desde
`main()`. Los tests que necesitan corridas completas usan el escenario reducido
`_mini()` para que la suite termine en segundos.

Correr:  uv run python tests/test_modelo.py
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import matplotlib
matplotlib.use("Agg")           # sin ventana: los tests solo escriben PNG

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
FIXTURES = Path(__file__).resolve().parent / "fixtures"

from hospital import (des, intercambio, montecarlo, outputs, params, plots, sd,  # noqa: E402
                      simulacion)

UVG = "Hospital General UVG"
Z5 = "Puesto Salud Z5"


def _mini(fraccion: float = 0.02) -> params.ParamsSistema:
    """Escenario reducido: mismas reglas, ~2 % de los heridos."""
    p = params.cargar()
    return replace(p, heridos_t0=[
        replace(z, leve=round(z.leve * fraccion), moderado=round(z.moderado * fraccion),
                grave=max(1, round(z.grave * fraccion)))
        for z in p.heridos_t0])


# --------------------------------------------------------------------------- P1
def _p1_params():
    p = params.cargar()
    assert len(p.instalaciones) == 5
    assert sum(i.operativa for i in p.instalaciones) == 4        # Z3 cerrada
    assert sum(z.total_atencion for z in p.heridos_t0) == 11773
    # tasas normalizadas a base horaria: consumo/hora = consumo/día / 24
    s = next(x for x in p.suministros if x.nombre == "sangre")
    assert abs(s.consumo_hora_base - 120 / 24) < 1e-9
    assert p.dt_sd_h == 0.25 and p.metodo_integracion == "euler"


def _p1_totales_por_zona():
    """Los totales por zona y triage coinciden con el Excel fuente."""
    fuente = {"Z1": (2840, 890, 320, 145, 4050), "Z2": (680, 210, 58, 22, 948),
              "Z3": (1120, 380, 140, 67, 1640), "Z4": (410, 120, 35, 12, 565),
              "Z5": (3200, 980, 390, 178, 4570)}
    for z in params.cargar().heridos_t0:
        leve, mod, grave, fall, total = fuente[z.zona]
        assert (z.leve, z.moderado, z.grave, z.fallecidos) == (leve, mod, grave, fall), z.zona
        assert z.total_atencion == total, z.zona
    inst = {i.zona: (i.camas, i.uci, i.quirofanos, i.operativa) for i in params.cargar().instalaciones}
    assert inst["Z1"] == (320, 24, 6, True)
    assert inst["Z3"][3] is False                                 # Centro Salud Z3 cerrado
    assert inst["Z5"] == (20, 0, 0, True)


def _p1_r2_tau_y_distancias():
    d = params.DIST_ZONAS_KM
    for a in params.ZONAS:
        assert d[a][a] == 0.0
        for b in params.ZONAS:
            assert d[a][b] == d[b][a], (a, b)                     # simétrica
            if a != b:
                assert d[a][b] > 0
                assert params.tau_traslado(a, b) == params.tau_traslado(b, a) > params.TAU_FIJO_H
    assert params.tau_traslado("Z1", "Z1") == params.TAU_FIJO_H
    assert abs(params.tau_traslado("Z5", "Z1") - (0.25 + 9 / 25)) < 1e-12


def _p1_r2_orden_derivacion_excluye_capacidad_cero():
    tabla = params.cargar().orden_derivacion()
    for zona in params.ZONAS:
        for gravedad in params.GRAVEDADES:
            candidatas = tabla[(zona, gravedad)]
            assert candidatas, (zona, gravedad)
            assert "Centro Salud Z3" not in candidatas            # cerrada
            taus = [params.tau_traslado(zona, params.cargar().zona_de(n)) for n in candidatas]
            assert taus == sorted(taus), (zona, gravedad)         # ordenadas por cercanía
        assert Z5 not in tabla[(zona, "grave")]                   # 0 UCI y 0 quirófanos
        assert Z5 in tabla[(zona, "moderado")]


def _p1_s1_reparto_personal():
    p = params.cargar()
    reparto = params.reparto_personal(p.instalaciones, p.medicos_efectivos())
    assert sum(reparto.values()) == p.medicos_efectivos() == round(87 * 0.85)
    assert min(reparto.values()) >= params.MEDICOS_MINIMOS_POR_INSTALACION
    assert "Centro Salud Z3" not in reparto
    assert reparto[UVG] > reparto[Z5]                             # proporcional a camas


def _p1_make_rng_determinismo():
    assert params.make_rng(7).random() == params.make_rng(7).random()
    assert params.make_rng(7).random() != params.make_rng(8).random()


def _p1_intercambio_csv_vacio():
    vacio = FIXTURES / "_vacio.csv"
    vacio.write_text("")
    try:
        assert intercambio.cargar_demanda_grupo1(vacio) is None
        assert intercambio.cargar_demanda_grupo1(FIXTURES / "no_existe.csv") is None
    finally:
        vacio.unlink()


def _p1_intercambio_fixture():
    df = intercambio.cargar_demanda_grupo1(FIXTURES / "grupo1_demo.csv")
    assert df is not None and len(df) == 3

    p = params.cargar()
    lotes = intercambio.aplicar_demanda(p, df)
    assert len(lotes) == 3
    assert [l.t for l in lotes] == [6.0, 12.0, 18.0]             # bloque b -> hora (b-1)*6
    assert lotes[0].zona == "Z1" and lotes[0].total == 265

    imp = intercambio.resumen_impacto(p, df)
    z5 = imp.loc[imp["zona"] == "Z5"].iloc[0]
    assert z5["grupo1_total"] == 320
    assert z5["nuevo_total"] == 4570 + 320


# --------------------------------------------------------------------------- P2
def _p2_des_thinning():
    rng = params.make_rng(42)
    p = params.cargar()
    z = next(z for z in p.heridos_t0 if z.zona == "Z1")
    tiempos = des.llegadas_thinning(rng, p, z.zona, z.total_atencion)
    assert 0 < len(tiempos) <= z.total_atencion
    assert all(0 <= t < params.HORIZONTE_H for t in tiempos)
    assert tiempos == sorted(tiempos)                            # Poisson -> orden creciente


def _p2_r3_grave_requiere_los_cuatro():
    """R3: un grave necesita médico, quirófano, UCI y consumible a la vez."""
    p = params.cargar()
    r = des.crear_recursos(p)
    inst = r.instalaciones[UVG]
    inst.medicos_capacidad = 1

    for recurso, campo in (("medico", "medicos_ocupados"), ("quirofano", "quirofanos_ocupados"),
                           ("cama_uci", "uci_ocupadas")):
        antes = getattr(inst, campo)
        setattr(inst, campo, des.capacidad(inst, recurso))        # saturar solo ese recurso
        assert not des.hay_servible_libre(r, UVG, "grave", p), recurso
        setattr(inst, campo, antes)
    assert des.hay_servible_libre(r, UVG, "grave", p)

    r.nivel_insumos["sangre"] = 0.0                               # R5: falta el consumible
    assert not des.puede_atender(r, UVG, "grave", p)
    r.nivel_insumos["sangre"] = 480.0

    des.tomar_recursos(r, UVG, "grave", p)
    assert (inst.medicos_ocupados, inst.quirofanos_ocupados, inst.uci_ocupadas) == (1, 1, 1)
    des.liberar_recursos(r, UVG, "grave", p)
    assert (inst.medicos_ocupados, inst.quirofanos_ocupados, inst.uci_ocupadas) == (0, 0, 0)

    # moderado ocupa cama general, leve solo médico
    des.tomar_recursos(r, UVG, "moderado", p)
    assert inst.camas_ocupadas == 1 and inst.uci_ocupadas == 0
    des.liberar_recursos(r, UVG, "moderado", p)
    des.tomar_recursos(r, UVG, "leve", p)
    assert inst.camas_ocupadas == 0 and inst.medicos_ocupados == 1
    des.liberar_recursos(r, UVG, "leve", p)


def _p2_r4_prioridad_estricta():
    """Con un solo hueco, entra el grave aunque el leve lleve más tiempo en cola."""
    p = params.cargar()
    r = des.crear_recursos(p)
    inst = r.instalaciones[UVG]
    inst.medicos_capacidad = 1
    for i, gravedad in enumerate(("leve", "moderado", "grave")):
        paciente = des.Paciente(id=i, zona="Z1", gravedad=gravedad, instalacion=UVG, t_llegada=i)
        paciente.estado = "en_cola"
        des.encolar(r, paciente)
    resultado = des.admitir_pendientes(r, UVG, p)
    assert [x.gravedad for x in resultado.admitidos] == ["grave"]


def _p2_r2_elige_la_mas_cercana_libre():
    p = params.cargar()
    r = des.crear_recursos(p)
    assert des.elegir_instalacion(r, "Z2", "moderado", p) == "Clínica Z2"      # intrazona
    assert des.elegir_instalacion(r, "Z5", "grave", p) == UVG                  # Z5 no opera graves

    clinica = r.instalaciones["Clínica Z2"]
    clinica.camas_ocupadas = clinica.camas_capacidad
    assert des.elegir_instalacion(r, "Z2", "moderado", p) == UVG               # la siguiente

    for inst in r.instalaciones.values():                                       # todo saturado
        inst.camas_ocupadas = inst.camas_capacidad
    destino = des.elegir_instalacion(r, "Z2", "moderado", p)
    assert destino in r.instalaciones and destino != "Centro Salud Z3"          # fallback válido


def _p2_simulacion_invariante():
    p = _mini()
    res = simulacion.correr(p, seed=1000, replica=3)
    assert res.generados <= sum(z.total_atencion for z in p.heridos_t0)
    assert (res.atendidos + res.muertes_evitables + res.muertes_clinicas + res.en_sistema
            == res.generados)
    assert len(res.patient_log) == res.generados
    assert res.patient_log["pid"].is_unique
    assert (res.patient_log["replica"] == 3).all()
    assert set(res.patient_log["estado"]) <= set(params.ESTADOS_PACIENTE)
    assert (res.patient_log["espera"] >= 0).all()
    for shape in (res.ocup_camas, res.ocup_uci, res.ocup_quirofanos):
        assert shape.shape == (len(res.instalaciones), params.N_BLOQUES)
        assert (shape >= 0).all()
    assert res.bloqueo_horas.shape == (len(res.instalaciones), len(params.RECURSOS_BLOQUEO))
    assert (res.bloqueo_horas >= 0).all()


def _p2_esquemas_de_logs():
    res = simulacion.correr(_mini(), seed=1000)
    assert tuple(res.patient_log.columns) == params.COLS_PATIENT_LOG
    assert tuple(res.state_log.columns) == params.COLS_STATE_LOG
    assert tuple(res.stock_log.columns) == params.COLS_STOCK_LOG
    assert set(res.state_log["recurso"]) == set(params.RECURSOS_SERVIBLES)
    assert (res.state_log["ocupados"] <= res.state_log["capacidad"]).all()
    assert (res.state_log["cola"] >= 0).all()
    assert set(res.stock_log["suministro"]) == {s.nombre for s in params.cargar().suministros}


def _p2_dt_y_muestreo():
    """El paso de integración es 0.25 h y el muestreo ocurre en cada paso."""
    res = simulacion.correr(_mini(), seed=1000)
    ts = np.sort(res.stock_log["t"].unique())
    assert len(ts) == params.HORIZONTE_H / params.DT_SD_H + 1     # incluye la muestra en t=0
    assert np.allclose(np.diff(ts), params.DT_SD_H)
    assert set(res.state_log["t"].unique()) == set(ts)
    assert "DT_SD =" not in Path(simulacion.__file__).read_text()  # la constante vive en params


def _p2_exclusiones_en_corrida():
    log = simulacion.correr(_mini(), seed=1000).patient_log
    assert (log["instalacion"] == "Centro Salud Z3").sum() == 0
    assert log[(log["triage"] == "grave") & (log["instalacion"] == Z5)].empty


def _p2_tau_en_la_espera():
    """El traslado consume tiempo: nadie es atendido antes de tau desde su llegada."""
    p = _mini()
    log = simulacion.correr(p, seed=1000).patient_log.dropna(subset=["t_inicio"])
    for _, fila in log.iterrows():
        tau = p.tau(fila["zona"], p.zona_de(fila["instalacion"]))
        assert fila["t_inicio"] - fila["t_llegada"] >= tau - 1e-9


def _p2_simulacion_llegadas_extra():
    p = _mini()
    df = intercambio.cargar_demanda_grupo1(FIXTURES / "grupo1_demo.csv")
    lotes = intercambio.aplicar_demanda(p, df)
    res = simulacion.correr(p, seed=1000, llegadas_extra=lotes)
    base = simulacion.correr(p, seed=1000)
    assert res.generados == base.generados + sum(l.total for l in lotes)


def _p2_intervencion_reduce_muertes():
    """Más médicos (el recurso que vincula) reducen la mortalidad evitable."""
    p = _mini()
    interv = params.Intervencion(medicos_extra=40, desde_bloque=1)
    base = np.mean([simulacion.correr(p, seed=s).muertes_evitables for s in (1000, 1001, 1002)])
    con = np.mean([simulacion.correr(p, seed=s, intervencion=interv).muertes_evitables
                   for s in (1000, 1001, 1002)])
    assert con < base, (base, con)


# --------------------------------------------------------------------------- P3
def _p3_sd_integradores():
    comparacion = sd.comparar_integradores()
    assert comparacion["error_rk4"] < comparacion["error_euler"]


def _p3_sd_impulso_y_no_negatividad():
    p = params.cargar()
    estado = sd.estado_inicial(p)
    sin_carga = {i.nombre: {g: 0 for g in params.GRAVEDADES} for i in p.instalaciones}
    nuevo = sd.paso(estado, {"sangre": 30.0}, sin_carga, p, p.dt_sd_h)
    assert nuevo.stocks["sangre"] == 450.0                        # impulso exacto, no proporcional
    assert nuevo.stocks["combustible_generadores"] < 1800.0       # flujo continuo
    assert all(v >= 0 for v in nuevo.stocks.values())
    assert all(0 <= v <= 1 for v in nuevo.energia.values())


def _p3_r5_consumo_es_fijo():
    p = params.cargar()
    r = des.crear_recursos(p)
    antes = dict(r.nivel_insumos)
    paciente = des.Paciente(id=0, zona="Z1", gravedad="grave", instalacion=UVG)
    assert des.intentar_iniciar(r, UVG, paciente, p) is des.Intento.INICIADO
    for k, q in p.consumo_por_procedimiento["grave"].items():
        assert antes[k] - r.nivel_insumos[k] == q                 # cantidad fija exacta
        assert r.consumo_pendiente[k] == q                        # y queda en el buffer del SD


def _p3_r5_sin_insumo_no_inicia_y_libera():
    p = params.cargar()
    r = des.crear_recursos(p)
    inst = r.instalaciones[UVG]
    r.nivel_insumos["material_quirurgico"] = 0.0
    paciente = des.Paciente(id=0, zona="Z1", gravedad="grave", instalacion=UVG)
    paciente.estado = "en_cola"
    assert des.intentar_iniciar(r, UVG, paciente, p) is des.Intento.SIN_INSUMO
    assert (inst.medicos_ocupados, inst.quirofanos_ocupados, inst.uci_ocupadas) == (0, 0, 0)
    assert paciente.estado == "en_cola" and paciente.t_inicio_atencion is None
    assert des.faltantes_insumo(r, "grave", p) == ("material_quirurgico",)


def _p3_r5_reintento_conserva_orden_y_espera():
    p = params.cargar()
    r = des.crear_recursos(p)
    cola = r.instalaciones[UVG].colas["grave"]
    graves = []
    for i in range(3):
        paciente = des.Paciente(id=i, zona="Z1", gravedad="grave", instalacion=UVG, t_llegada=i)
        paciente.estado = "en_cola"
        des.encolar(r, paciente)
        graves.append(paciente)

    r.nivel_insumos["material_quirurgico"] = 0.0                  # guarda 1: ni siquiera saca
    resultado = des.admitir_pendientes(r, UVG, p)
    assert resultado.admitidos == []
    assert list(cola) == graves
    assert "material_quirurgico" in resultado.faltantes["grave"]
    assert all(x.intentos_insumo_fallidos == 0 for x in graves)

    r.nivel_insumos["material_quirurgico"] = 1.0                  # guarda 2: el 2.º vuelve al frente
    resultado = des.admitir_pendientes(r, UVG, p)
    assert resultado.admitidos == [graves[0]]
    assert list(cola) == graves[1:]                               # orden FIFO intacto
    assert graves[1].intentos_insumo_fallidos == 1
    assert graves[1].t_llegada == 1 and graves[1].estado == "en_cola"   # espera acumulada intacta


def _p3_r5_no_hay_bucle_infinito():
    p = params.cargar()
    r = des.crear_recursos(p)
    r.nivel_insumos["material_quirurgico"] = 1.0
    for i in range(200):
        paciente = des.Paciente(id=i, zona="Z1", gravedad="grave", instalacion=UVG)
        paciente.estado = "en_cola"
        des.encolar(r, paciente)
    llamadas = 500
    for _ in range(llamadas):
        des.admitir_pendientes(r, UVG, p)
    fallidos = sum(x.intentos_insumo_fallidos for x in r.instalaciones[UVG].colas["grave"])
    assert fallidos <= llamadas                                   # a lo sumo un intento por llamada


def _p3_r5_stock_nunca_negativo():
    res = simulacion.correr(_mini(), seed=1000)
    assert (res.stock_log["nivel"] >= 0).all()
    for _, g in res.stock_log.groupby("suministro"):
        niveles = g.sort_values("t")["nivel"].to_numpy()
        assert np.all(np.diff(niveles) <= 1e-9)                   # sin reabastecimiento, solo baja


def _p3_r5_inventario_reducido():
    """Al agotarse el consumible, el quirófano queda ocioso y la cola de graves crece."""
    p = _mini()
    consumo = {g: dict(d) for g, d in p.consumo_por_procedimiento.items()}
    consumo["grave"]["material_quirurgico"] = 30.0                # alcanza para ~2 cirugías
    escaso = replace(p, consumo_por_procedimiento=consumo)

    base = simulacion.correr(p, seed=1000)
    res = simulacion.correr(escaso, seed=1000)
    graves = lambda r: (r.patient_log["triage"].eq("grave")
                        & r.patient_log["estado"].isin(("atendido", "muerte_clinica"))).sum()
    assert graves(res) < graves(base)
    assert res.muertes_evitables > base.muertes_evitables

    quirofanos = res.state_log[res.state_log["recurso"] == "quirofano"]
    ocioso_con_cola = (quirofanos["ocupados"] == 0) & (quirofanos["cola"] > 0)
    assert ocioso_con_cola.any()                                  # quirófano libre y cola esperando
    faltan_kits = res.stock_log[res.stock_log["suministro"] == "material_quirurgico"]
    assert faltan_kits["nivel"].iloc[-1] < consumo["grave"]["material_quirurgico"]


# --------------------------------------------------------------------------- P4
def _fake_res_mc(n_rep=30, seed=0) -> outputs.ResultadoMC:
    rng = params.make_rng(seed)
    inst = [i.nombre for i in params.cargar().instalaciones]
    recursos = list(params.RECURSOS_BLOQUEO)
    ni, nb, nk = len(inst), params.N_BLOQUES, len(recursos)
    return outputs.ResultadoMC(
        n_replicas=n_rep, instalaciones=inst, n_bloques=nb, recursos=recursos,
        ocup_camas=np.clip(rng.normal(0.8, 0.15, (n_rep, ni, nb)), 0, 1.2),
        ocup_uci=np.clip(rng.normal(0.9, 0.1, (n_rep, ni, nb)), 0, 1.2),
        ocup_quirofanos=np.clip(rng.normal(0.7, 0.2, (n_rep, ni, nb)), 0, 1.2),
        bloqueo_horas=rng.gamma(2.0, 5.0, (n_rep, ni, nk)),
        muertes_evitables=rng.integers(200, 900, n_rep).astype(float),
        muertes_clinicas=rng.integers(100, 400, n_rep).astype(float),
        generados=np.full(n_rep, 11773.0),
    )


def _p4_outputs_tablas():
    outputs.DIR_TABLAS = Path(tempfile.mkdtemp())        # no ensuciar outputs/tables/ real

    res = _fake_res_mc()
    sat = outputs.tabla_saturacion(res)
    assert len(sat) == 5 * 2 * params.N_BLOQUES                  # inst × tipo × bloque
    assert (outputs.DIR_TABLAS / "saturacion_por_bloque.csv").exists()
    assert sat["ocupacion_media_pct"].between(0, 120).all()
    assert (sat["ic95_bajo_pct"] <= sat["ocupacion_media_pct"]).all()

    cb = outputs.tabla_cuellos_botella(res)
    assert cb["critico"].sum() == 2
    assert cb["rank"].tolist() == list(range(1, len(cb) + 1))
    assert cb["horas_bloqueadas_media"].is_monotonic_decreasing

    def run_fn(interv):
        extra = 0 if interv is None else (
            sum(interv.camas_extra.values()) + sum(interv.uci_extra.values())
            + sum(interv.quirofanos_extra.values()) + interv.medicos_extra * 5
            + interv.sangre_extra * 0.5)
        r = _fake_res_mc(seed=1)
        r.muertes_evitables = np.maximum(r.muertes_evitables - extra * 3, 0.0)
        return r

    rm = outputs.tabla_recursos_minimos(run_fn, params.cargar(), max_iter=3)
    assert rm.iloc[0]["recurso"] == "— base —"
    assert rm["alcanza_umbral"].any()


def _p4_semillas():
    assert len(set(montecarlo.semillas(30))) == 30
    assert montecarlo.semillas(30) == montecarlo.semillas(30)
    assert montecarlo.semillas(5, base=1) != montecarlo.semillas(5, base=2)


def _p4_misma_semilla_identica():
    p = _mini()
    a = simulacion.correr(p, seed=7)
    b = simulacion.correr(p, seed=7)
    assert (a.generados, a.muertes_evitables, a.atendidos) == (b.generados, b.muertes_evitables,
                                                               b.atendidos)
    assert a.patient_log.equals(b.patient_log)
    assert a.state_log.equals(b.state_log)


def _p4_semillas_distintas_difieren():
    p = _mini()
    a = simulacion.correr(p, seed=7)
    b = simulacion.correr(p, seed=8)
    assert (a.generados, a.muertes_evitables) != (b.generados, b.muertes_evitables) \
        or not a.patient_log.equals(b.patient_log)


def _p4_perturbar_no_muta_base():
    p = params.cargar()
    original = params.cargar()
    perturbado = montecarlo.perturbar(p, params.make_rng(1))
    assert perturbado is not p
    assert p.t_atencion_h == original.t_atencion_h
    assert p.heridos_t0 == original.heridos_t0
    assert p.presentismo == original.presentismo
    assert montecarlo.perturbar(p, params.make_rng(1)).presentismo == perturbado.presentismo
    assert montecarlo.perturbar(p, params.make_rng(2)).presentismo != perturbado.presentismo
    assert params.PRESENTISMO_MIN <= perturbado.presentismo <= params.PRESENTISMO_MAX
    assert all(z.leve >= 0 and z.grave >= 0 for z in perturbado.heridos_t0)
    assert all(s.stock_inicial >= 0 for s in perturbado.suministros)


def _p4_paralelo_igual_secuencial():
    p = _mini()
    a = montecarlo.correr_replicas(p, n=6, procesos=1)
    b = montecarlo.correr_replicas(p, n=6, procesos=3)
    assert np.array_equal(a.muertes_evitables, b.muertes_evitables)
    assert np.allclose(a.ocup_camas, b.ocup_camas)
    assert a.state_log.equals(b.state_log)


def _p4_replicas_minimo_30():
    res = montecarlo.correr_replicas(_mini(), n=30, guardar_logs=False)
    assert res.n_replicas == 30
    assert res.ocup_camas.shape == (30, len(res.instalaciones), params.N_BLOQUES)
    assert len(res.metadatos["semillas"]) == 30
    assert res.metadatos["metodo_ic"] == "t"


def _p4_ic95():
    x = params.make_rng(0).normal(10, 2, 30)
    media, lo, hi = outputs.ic95(x)
    assert lo < media < hi
    media_p, lo_p, hi_p = outputs.ic95(x, metodo="percentil")
    assert (hi - lo) < (hi_p - lo_p)                              # la t es más angosta que 2.5/97.5
    media_c, lo_c, hi_c = outputs.ic95(np.full(10, 3.0))
    assert lo_c == hi_c == media_c == 3.0
    _, lo1, hi1 = outputs.ic95(np.array([5.0]))
    assert np.isnan(lo1) and np.isnan(hi1)                        # n=1 no revienta


def _p4_resumen_bloques():
    res = montecarlo.correr_replicas(_mini(), n=4, procesos=1)
    df = montecarlo.resumen_bloques(res)
    assert len(df) == len(res.instalaciones) * len(params.RECURSOS_SERVIBLES) * params.N_BLOQUES
    assert df["bloque"].between(1, params.N_BLOQUES).all()
    assert (df["ic95_bajo"] <= df["ocupacion_media"] + 1e-9).all()
    assert (df["ocupacion_media"] <= df["ic95_alto"] + 1e-9).all()
    assert (df["metodo_ic"] == "t").all() and (df["n_replicas"] == 4).all()

    cola = montecarlo.cola_por_triage(res)
    assert set(cola["triage"]) == set(params.GRAVEDADES)
    assert (cola["cola_media"] >= -1e-9).all()
    muertes = montecarlo.muertes_por_bloque(res)
    evitables = muertes[muertes["tipo"] == "evitable"].sort_values("bloque")["muertes_media"]
    assert evitables.is_monotonic_increasing                      # acumulado
    stocks = montecarlo.stocks_en_tiempo(res)
    assert (stocks["nivel_medio"] >= 0).all()


def _p4_estabilidad():
    res = _fake_res_mc(n_rep=30)
    df = montecarlo.estabilidad_media_acumulada(res, metricas=("muertes_evitables",))
    assert df["k"].tolist() == list(range(1, 31))
    assert abs(df.iloc[-1]["media_acumulada"] - res.muertes_evitables.mean()) < 1e-12
    assert np.isnan(df.iloc[0]["ic95_bajo"])
    assert df["estable"].dtype == bool


def _p4_colapso_sintetico():
    res = _fake_res_mc(n_rep=5)
    inst = res.instalaciones[0]
    filas = []
    for nombre in res.instalaciones:
        for recurso in params.RECURSOS_SERVIBLES:
            for b in range(1, params.N_BLOQUES + 1):
                colapsa = nombre == inst and recurso == "medico" and b >= 5
                filas.append({"instalacion": nombre, "recurso": recurso, "bloque": b,
                              "ocupacion_media": 1.0 if colapsa else 0.3,
                              "ic95_bajo": 0.0, "ic95_alto": 1.0,
                              "cola_media": 10.0 * b if colapsa else 0.0,
                              "cola_ic95_bajo": 0.0, "cola_ic95_alto": 0.0,
                              "n_replicas": 5, "metodo_ic": "t"})
    df = montecarlo.bloque_de_colapso(res, df_bloques=pd.DataFrame(filas))
    fila = df[(df["instalacion"] == inst) & (df["recurso"] == "medico")].iloc[0]
    assert fila["bloque_colapso"] == 5
    assert df["bloque_colapso"].notna().sum() == 1


def _p4_cuello_botella_sintetico():
    res = _fake_res_mc(n_rep=5)
    res.bloqueo_horas[:] = 1.0
    res.bloqueo_horas[:, 2, 3] = 500.0                            # un (instalación, recurso) fuera de rango
    cb = montecarlo.recurso_cuello_botella(res, top=2)
    assert cb.iloc[0]["instalacion"] == res.instalaciones[2]
    assert cb.iloc[0]["recurso"] == res.recursos[3]
    assert cb["horas_bloqueadas_media"].is_monotonic_decreasing
    assert cb["critico"].sum() == 2


def _p4_sensibilidad_crn():
    """Números aleatorios comunes (una palanca que no vincula da diferencia cero exacta)."""
    df = montecarlo.sensibilidad_recursos(_mini(), n=4, delta=0.20,
                                          recursos=("camas", "medicos"), procesos=2)
    assert set(df["n"]) == {4}
    camas = df[df["recurso"] == "camas"].iloc[0]
    assert camas["dif_pareada_media"] == 0.0                      # las camas nunca vinculan
    assert "reduccion_pct" in df.columns and "determinante" in df.columns


def _p4_plots_png():
    plots.DIR_FIGURAS = Path(tempfile.mkdtemp())
    res = montecarlo.correr_replicas(_mini(), n=4, procesos=1)
    bloques = montecarlo.resumen_bloques(res)
    figuras = [
        plots.ocupacion_por_bloque(bloques, "medico"),
        plots.cola_por_triage(montecarlo.cola_por_triage(res)),
        plots.stocks_en_tiempo(montecarlo.stocks_en_tiempo(res)),
        plots.muertes_acumuladas(montecarlo.muertes_por_bloque(res)),
        plots.estabilidad(montecarlo.estabilidad_media_acumulada(res)),
        plots.euler_vs_rk4(),
    ]
    assert all(f is not None for f in figuras)
    generados = list(plots.DIR_FIGURAS.iterdir())
    assert len(generados) == 6 and all(f.stat().st_size > 0 for f in generados)

    import matplotlib.pyplot as plt
    _, ax = plt.subplots()
    antes = len(list(plots.DIR_FIGURAS.iterdir()))
    plots.ocupacion_por_bloque(bloques, "medico", ax=ax)
    assert len(list(plots.DIR_FIGURAS.iterdir())) == antes        # con `ax` no escribe archivo
    plt.close("all")


# --------------------------------------------------------------------------- main
def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("_p") and callable(v)]
    fallos = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except AssertionError as e:
            fallos += 1
            print(f"FALLO  {t.__name__}: {e}")
    print(f"\n{len(tests) - fallos}/{len(tests)} pasaron")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
