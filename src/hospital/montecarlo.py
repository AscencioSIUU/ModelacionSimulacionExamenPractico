"""
Monte Carlo: réplicas independientes, agregación con IC 95 % y análisis.

Aquí se corren N réplicas con semillas independientes y se reporta media
con intervalo de confianza.
"""

from __future__ import annotations

import hashlib
import os
import pickle
from concurrent.futures import ProcessPoolExecutor
from dataclasses import fields, replace
from pathlib import Path
from typing import Callable, NamedTuple, Sequence, TypeVar

import numpy as np
import pandas as pd

from . import outputs, params as P, simulacion
from .outputs import ResultadoMC, ic95
from .params import BLOQUE_H, GRAVEDADES, HORIZONTE_H, Intervencion, N_BLOQUES, ParamsSistema

DIR_CACHE = Path(__file__).resolve().parents[2] / "outputs" / "cache"

RECURSOS_SENSIBILIDAD = ("camas", "uci", "quirofanos", "medicos", "sangre")

T = TypeVar("T")


# =============================================================================
# Semillas y perturbación
# =============================================================================
def semillas(n: int, base: int = P.SEMILLA_BASE) -> list[int]:
    """`n` semillas independientes y reproducibles a partir de una sola raíz."""
    return [int(s.generate_state(1)[0]) for s in np.random.SeedSequence(base).spawn(n)]


def _mult(rng: np.random.Generator, sigma: float) -> float:
    """Multiplicador lognormal de mediana 1: exp(N(0, sigma))."""
    return float(np.exp(rng.normal(0.0, sigma))) if sigma > 0 else 1.0


def perturbar(p: ParamsSistema, rng: np.random.Generator) -> ParamsSistema:
    """
    S-6. Devuelve una COPIA de los parámetros con la incertidumbre de la réplica.

    Nunca muta el objeto recibido: los dicts y las listas de dataclasses son
    compartidos por referencia y contaminarían las demás réplicas del modo
    secuencial.
    """
    s = P.PERTURBACION_MC

    heridos = []
    for z in p.heridos_t0:
        m = _mult(rng, s["heridos_t0"])
        heridos.append(replace(z, leve=max(0, round(z.leve * m)),
                               moderado=max(0, round(z.moderado * m)),
                               grave=max(0, round(z.grave * m))))

    m_lambda = _mult(rng, s["lambda_llegada"])
    m_atencion = _mult(rng, s["t_atencion"])
    m_mort = _mult(rng, s["p_mort_clinica"])
    m_tol = _mult(rng, s["t_tolerancia_cola"])
    suministros = [replace(x, stock_inicial=max(0.0, x.stock_inicial * _mult(rng, s["stock_inicial"])))
                   for x in p.suministros]
    presentismo = float(np.clip(p.presentismo + rng.normal(0.0, s["presentismo"]),
                                P.PRESENTISMO_MIN, P.PRESENTISMO_MAX))

    return replace(
        p,
        heridos_t0=heridos,
        suministros=suministros,
        lambda_llegada_h={k: v * m_lambda for k, v in p.lambda_llegada_h.items()},
        t_atencion_h={k: v * m_atencion for k, v in p.t_atencion_h.items()},
        p_mortalidad_clinica={k: float(np.clip(v * m_mort, 0.0, 1.0))
                              for k, v in p.p_mortalidad_clinica.items()},
        t_tolerancia_cola_h={k: v * m_tol for k, v in p.t_tolerancia_cola_h.items()},
        presentismo=presentismo,
        consumo_por_procedimiento={g: dict(d) for g, d in p.consumo_por_procedimiento.items()},
        tasa_reabastecimiento_h=dict(p.tasa_reabastecimiento_h),
        dist_zonas_km={o: dict(d) for o, d in p.dist_zonas_km.items()},
    )


# =============================================================================
# Corrida de una réplica (nivel de módulo: debe ser picklable)
# =============================================================================
class _Tarea(NamedTuple):
    replica: int
    params: ParamsSistema
    seed: int
    intervencion: Intervencion | None
    perturbar_params: bool
    guardar_logs: bool
    llegadas_extra: tuple | None


class _Salida(NamedTuple):
    replica: int
    instalaciones: list[str]
    recursos: list[str]
    ocup_camas: np.ndarray          # [i, b]
    ocup_uci: np.ndarray
    ocup_quirofanos: np.ndarray
    bloqueo_horas: np.ndarray       # [i, k]
    muertes_evitables: int
    muertes_clinicas: int
    muertes_evitables_bloque: np.ndarray    # [b]
    muertes_clinicas_bloque: np.ndarray     # [b]
    atendidos: int
    generados: int
    patient_log: pd.DataFrame
    state_log: pd.DataFrame
    stock_log: pd.DataFrame


_RECURSO_DE_OCUPACION = {"cama_general": "ocup_camas", "cama_uci": "ocup_uci",
                         "quirofano": "ocup_quirofanos"}


def _bloque_de(t: pd.Series) -> pd.Series:
    """
    Bloque 1..12 al que pertenece cada instante. El bloque b cubre el intervalo
    (6(b−1), 6b], y t = 0 cuenta como bloque 1.
    """
    return np.ceil(np.asarray(t, dtype=float) / BLOQUE_H).clip(1, N_BLOQUES).astype(int)


def _ocupacion_media_por_bloque(state_log: pd.DataFrame, instalaciones: Sequence[str],
                                recurso: str) -> np.ndarray:
    """
    Media de ocupación del bloque (24 muestras de 0.25 h), no el instante de
    corte.
    """
    sub = state_log[state_log["recurso"] == recurso].copy()
    sub["bloque"] = _bloque_de(sub["t"])
    sub["fraccion"] = np.where(sub["capacidad"] > 0, sub["ocupados"] / sub["capacidad"].replace(0, 1), 0.0)
    tabla = sub.pivot_table(index="instalacion", columns="bloque", values="fraccion", aggfunc="mean")
    tabla = tabla.reindex(index=list(instalaciones), columns=range(1, N_BLOQUES + 1))
    return tabla.to_numpy(dtype=float, na_value=0.0)


def _muertes_por_bloque_replica(patient_log: pd.DataFrame, estado: str) -> np.ndarray:
    """Muertes de un tipo en cada bloque de 6 h, para una sola réplica."""
    conteo = np.zeros(N_BLOQUES)
    sub = patient_log[patient_log["estado"] == estado].dropna(subset=["t_fin"])
    if not sub.empty:
        for bloque, n in pd.Series(_bloque_de(sub["t_fin"])).value_counts().items():
            conteo[int(bloque) - 1] = n
    return conteo


def _corrida(tarea: _Tarea) -> _Salida:
    semilla_pert, semilla_sim = np.random.SeedSequence(tarea.seed).spawn(2)
    p = tarea.params
    if tarea.perturbar_params:
        p = perturbar(p, P.make_rng(int(semilla_pert.generate_state(1)[0])))

    res = simulacion.correr(p, seed=int(semilla_sim.generate_state(1)[0]),
                            intervencion=tarea.intervencion,
                            llegadas_extra=list(tarea.llegadas_extra or ()),
                            replica=tarea.replica)

    ocupaciones = {r: _ocupacion_media_por_bloque(res.state_log, res.instalaciones, r)
                   for r in _RECURSO_DE_OCUPACION}
    vacio = pd.DataFrame()
    return _Salida(
        replica=tarea.replica,
        instalaciones=res.instalaciones,
        recursos=res.recursos,
        ocup_camas=ocupaciones["cama_general"],
        ocup_uci=ocupaciones["cama_uci"],
        ocup_quirofanos=ocupaciones["quirofano"],
        bloqueo_horas=res.bloqueo_horas,
        muertes_evitables=res.muertes_evitables,
        muertes_clinicas=res.muertes_clinicas,
        muertes_evitables_bloque=_muertes_por_bloque_replica(res.patient_log, "muerte_evitable"),
        muertes_clinicas_bloque=_muertes_por_bloque_replica(res.patient_log, "muerte_clinica"),
        atendidos=res.atendidos,
        generados=res.generados,
        patient_log=res.patient_log if tarea.guardar_logs else vacio,
        state_log=res.state_log if tarea.guardar_logs else vacio,
        stock_log=res.stock_log if tarea.guardar_logs else vacio,
    )


def correr_replicas(
    p: ParamsSistema,
    n: int = 30,
    intervencion: Intervencion | None = None,
    procesos: int | None = None,
    *,
    etiqueta: str = "base",
    perturbar_params: bool = True,
    guardar_logs: bool = True,
    semillas_fijas: Sequence[int] | None = None,
    llegadas_extra: Sequence | None = None,
) -> ResultadoMC:
    """
    Corre `n` réplicas independientes y las apila en un `ResultadoMC`.

    `llegadas_extra` son los lotes que devuelve `intercambio.aplicar_demanda`: con
    ellos el escenario incluye la demanda del Grupo 1; sin ellos es el escenario
    base que el grupo proyectó antes del intercambio.
    """
    if n < 1:
        raise ValueError("n debe ser al menos 1")
    seeds = list(semillas_fijas) if semillas_fijas is not None else semillas(n)
    if len(seeds) < n:
        raise ValueError("semillas_fijas tiene menos elementos que réplicas")
    extra = tuple(llegadas_extra) if llegadas_extra else None
    tareas = [_Tarea(r, p, seeds[r], intervencion, perturbar_params, guardar_logs, extra)
              for r in range(n)]

    if procesos is None:
        procesos = min(n, os.cpu_count() or 1)
    salidas: list[_Salida]
    if procesos <= 1 or n < 4:
        salidas = [_corrida(t) for t in tareas]
    else:
        try:
            with ProcessPoolExecutor(max_workers=procesos) as pool:
                salidas = list(pool.map(_corrida, tareas))   # map conserva el orden
        except (OSError, RuntimeError, ImportError):
            salidas = [_corrida(t) for t in tareas]          # el notebook nunca revienta

    instalaciones = salidas[0].instalaciones
    recursos = salidas[0].recursos
    apilar = lambda campo: np.stack([getattr(s, campo) for s in salidas])
    concatenar = lambda campo: (
        pd.concat([getattr(s, campo) for s in salidas], ignore_index=True)
        if guardar_logs else pd.DataFrame())

    return ResultadoMC(
        n_replicas=n,
        instalaciones=list(instalaciones),
        n_bloques=N_BLOQUES,
        recursos=list(recursos),
        ocup_camas=apilar("ocup_camas"),
        ocup_uci=apilar("ocup_uci"),
        ocup_quirofanos=apilar("ocup_quirofanos"),
        bloqueo_horas=apilar("bloqueo_horas"),
        muertes_evitables=np.array([s.muertes_evitables for s in salidas], dtype=float),
        muertes_clinicas=np.array([s.muertes_clinicas for s in salidas], dtype=float),
        generados=np.array([s.generados for s in salidas], dtype=float),
        muertes_evitables_bloque=apilar("muertes_evitables_bloque"),
        muertes_clinicas_bloque=apilar("muertes_clinicas_bloque"),
        patient_log=concatenar("patient_log"),
        state_log=concatenar("state_log"),
        stock_log=concatenar("stock_log"),
        metadatos={"etiqueta": etiqueta, "n": n, "semillas": seeds[:n],
                   "intervencion": intervencion, "perturbado": perturbar_params,
                   "con_grupo1": extra is not None, "metodo_ic": "t", "procesos": procesos,
                   "atendidos": [s.atendidos for s in salidas]},
    )


# =============================================================================
# Agregación
# =============================================================================
def resumen_bloques(res: ResultadoMC, recursos: Sequence[str] | None = None,
                    metodo_ic: str = "t") -> pd.DataFrame:
    """Ocupación y cola medias por instalación, recurso y bloque, con IC 95 %."""
    if res.state_log.empty:
        raise ValueError("resumen_bloques necesita state_log (correr con guardar_logs=True)")
    sub = res.state_log.copy()
    if recursos is not None:
        sub = sub[sub["recurso"].isin(list(recursos))]
    sub["bloque"] = _bloque_de(sub["t"])
    sub["ocupacion"] = np.where(sub["capacidad"] > 0,
                                sub["ocupados"] / sub["capacidad"].replace(0, 1), 0.0)
    por_replica = (sub.groupby(["replica", "instalacion", "recurso", "bloque"])
                      [["ocupacion", "cola"]].mean().reset_index())

    filas = []
    for (inst, rec, bloque), g in por_replica.groupby(["instalacion", "recurso", "bloque"]):
        ocup_m, ocup_lo, ocup_hi = ic95(g["ocupacion"].to_numpy(), metodo=metodo_ic)
        cola_m, cola_lo, cola_hi = ic95(g["cola"].to_numpy(), metodo=metodo_ic)
        filas.append({"instalacion": inst, "recurso": rec, "bloque": int(bloque),
                      "ocupacion_media": float(ocup_m), "ic95_bajo": float(ocup_lo),
                      "ic95_alto": float(ocup_hi), "cola_media": float(cola_m),
                      "cola_ic95_bajo": float(cola_lo), "cola_ic95_alto": float(cola_hi),
                      "n_replicas": int(len(g)), "metodo_ic": metodo_ic})
    return pd.DataFrame(filas).sort_values(["instalacion", "recurso", "bloque"]).reset_index(drop=True)


def cola_por_triage(res: ResultadoMC, dt: float = P.DT_SD_H,
                    metodo_ic: str = "t") -> pd.DataFrame:
    """
    Pacientes esperando atención en cada instante, por gravedad. Se reconstruye
    del `patient_log`: +1 al entrar al sistema, −1 al iniciar atención o al morir
    esperando. Incluye el traslado, igual que la columna `espera`.
    """
    if res.patient_log.empty:
        raise ValueError("cola_por_triage necesita patient_log")
    malla = np.arange(0.0, HORIZONTE_H + dt / 2, dt)
    filas = []
    for triage in GRAVEDADES:
        curvas = []
        for _, g in res.patient_log[res.patient_log["triage"] == triage].groupby("replica"):
            salida = g["t_inicio"].fillna(g["t_fin"])
            entra, _ = np.histogram(g["t_llegada"].to_numpy(dtype=float), bins=np.append(malla, np.inf))
            sale, _ = np.histogram(salida.dropna().to_numpy(dtype=float), bins=np.append(malla, np.inf))
            curvas.append(np.cumsum(entra - sale)[:len(malla)])
        arr = np.vstack(curvas)
        media, lo, hi = ic95(arr, axis=0, metodo=metodo_ic)
        filas.append(pd.DataFrame({"t": malla, "triage": triage, "cola_media": media,
                                   "ic95_bajo": lo, "ic95_alto": hi}))
    return pd.concat(filas, ignore_index=True)


def stocks_en_tiempo(res: ResultadoMC, metodo_ic: str = "t") -> pd.DataFrame:
    """Nivel medio de cada suministro a lo largo del tiempo, con IC 95 %."""
    if res.stock_log.empty:
        raise ValueError("stocks_en_tiempo necesita stock_log")
    filas = []
    for (t, suministro), g in res.stock_log.groupby(["t", "suministro"]):
        niveles = g["nivel"].to_numpy(dtype=float)
        media, lo, hi = ic95(niveles, metodo=metodo_ic)
        filas.append({"t": float(t), "suministro": suministro, "nivel_medio": float(media),
                      "ic95_bajo": float(lo), "ic95_alto": float(hi),
                      "frac_replicas_agotado": float(np.mean(niveles <= 0.0))})
    return pd.DataFrame(filas).sort_values(["suministro", "t"]).reset_index(drop=True)


def muertes_por_bloque(res: ResultadoMC, acumulado: bool = True,
                       metodo_ic: str = "t") -> pd.DataFrame:
    """Muertes evitables y clínicas por bloque de 6 h, con IC 95 %."""
    filas = []
    for tipo, arr in (("evitable", res.muertes_evitables_bloque),
                      ("clinica", res.muertes_clinicas_bloque)):
        conteo = np.cumsum(arr, axis=1) if acumulado else np.asarray(arr, dtype=float)
        media, lo, hi = ic95(conteo, axis=0, metodo=metodo_ic)
        for b in range(N_BLOQUES):
            filas.append({"bloque": b + 1, "tipo": tipo, "muertes_media": float(media[b]),
                          "ic95_bajo": float(lo[b]), "ic95_alto": float(hi[b]),
                          "acumulado": acumulado})
    return pd.DataFrame(filas)


def muertes_evitables_hasta(res: ResultadoMC, horas: float = 48.0) -> np.ndarray:
    """Muertes evitables acumuladas por réplica dentro de las primeras `horas`."""
    bloques = int(round(horas / BLOQUE_H))
    if not 1 <= bloques <= N_BLOQUES:
        raise ValueError(f"horas debe caer dentro del horizonte de {HORIZONTE_H} h")
    return res.muertes_evitables_bloque[:, :bloques].sum(axis=1)


def _metrica(res: ResultadoMC, nombre: str) -> np.ndarray:
    if nombre == "tasa_evitable":
        return res.tasa_evitable
    if nombre == "muertes_evitables":
        return res.muertes_evitables
    if nombre == "ocup_uci_pico":
        return res.ocup_uci.max(axis=(1, 2))
    raise KeyError(f"métrica desconocida: {nombre}")


def estabilidad_media_acumulada(
    res: ResultadoMC,
    metricas: Sequence[str] = ("tasa_evitable", "muertes_evitables", "ocup_uci_pico"),
    tol: float = 0.02,
    metodo_ic: str = "t",
) -> pd.DataFrame:
    """
    Media acumulada contra el número de réplicas. `estable` marca desde qué
    réplica la media acumulada deja de separarse de su valor final más de `tol`
    en términos relativos: si nunca se aplana, hay que subir n.
    """
    filas = []
    for nombre in metricas:
        x = np.asarray(_metrica(res, nombre), dtype=float)
        final = float(np.mean(x))
        acumuladas = np.cumsum(x) / np.arange(1, len(x) + 1)
        # Se aplana en k si desde k en adelante la media acumulada ya no se
        # separa del valor final más de `tol` en términos relativos.
        desviacion = np.abs(acumuladas - final) / abs(final) if final else np.full(len(x), np.nan)
        aplanada = np.array([bool(np.all(desviacion[k:] <= tol)) for k in range(len(x))])
        for k in range(1, len(x) + 1):
            media, lo, hi = ic95(x[:k], metodo=metodo_ic)
            semiancho = float(hi - media) if k > 1 else float("nan")
            filas.append({"k": k, "metrica": nombre, "media_acumulada": float(media),
                          "ic95_bajo": float(lo), "ic95_alto": float(hi),
                          "semiancho_relativo": semiancho / abs(final) if final else float("nan"),
                          "desviacion_relativa": float(desviacion[k - 1]),
                          "estable": bool(aplanada[k - 1])})
    return pd.DataFrame(filas)


# =============================================================================
# Preguntas del examen
# =============================================================================
def bloque_de_colapso(res: ResultadoMC, umbral_ocup: float = 0.98,
                      bloques_consecutivos: int = 2,
                      df_bloques: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Primer bloque en que un recurso queda saturado con cola creciente sostenida.

    Criterio: ocupación media >= `umbral_ocup` y cola media positiva y no
    decreciente durante `bloques_consecutivos` bloques seguidos.
    """
    df = resumen_bloques(res, recursos=P.RECURSOS_SERVIBLES) if df_bloques is None else df_bloques
    filas = []
    for (inst, rec), g in df.groupby(["instalacion", "recurso"]):
        g = g.sort_values("bloque")
        ocup = g["ocupacion_media"].to_numpy()
        cola = g["cola_media"].to_numpy()
        bloque_colapso = pd.NA
        for b in range(len(g) - bloques_consecutivos + 1):
            ventana = slice(b, b + bloques_consecutivos)
            if (ocup[ventana] >= umbral_ocup).all() and (cola[ventana] > 0).all() \
                    and all(np.diff(cola[ventana]) >= 0):
                bloque_colapso = int(g["bloque"].to_numpy()[b])
                break
        if bloque_colapso is pd.NA:
            filas.append({"instalacion": inst, "recurso": rec, "bloque_colapso": pd.NA,
                          "ocupacion_media": float(ocup.max()), "cola_media": float(cola.max()),
                          "ic95_bajo": np.nan, "ic95_alto": np.nan})
            continue
        idx = g["bloque"].to_numpy().tolist().index(bloque_colapso)
        filas.append({"instalacion": inst, "recurso": rec, "bloque_colapso": bloque_colapso,
                      "ocupacion_media": float(ocup[idx]), "cola_media": float(cola[idx]),
                      "ic95_bajo": float(g["ic95_bajo"].to_numpy()[idx]),
                      "ic95_alto": float(g["ic95_alto"].to_numpy()[idx])})
    df_out = pd.DataFrame(filas)
    df_out["bloque_colapso"] = df_out["bloque_colapso"].astype("Int64")
    return df_out.sort_values(["bloque_colapso", "instalacion"], na_position="last").reset_index(drop=True)


def recurso_cuello_botella(res: ResultadoMC, top: int = 2) -> pd.DataFrame:
    """Ranking de (instalación, recurso) por horas bloqueadas."""
    return outputs.tabla_cuellos_botella(res, top=top, guardar=False)


def primer_colapso(res: ResultadoMC) -> dict:
    """La respuesta a la Pregunta 1."""
    colapsos = bloque_de_colapso(res).dropna(subset=["bloque_colapso"])
    cuellos = recurso_cuello_botella(res, top=2)
    if colapsos.empty:
        fila = cuellos.iloc[0]
        return {"instalacion": fila["instalacion"], "recurso": fila["recurso"],
                "bloque": None, "ocupacion": float("nan"), "cola": float("nan"),
                "horas_bloqueadas": float(fila["horas_bloqueadas_media"]),
                "nota": "ningún recurso cumple el criterio de colapso sostenido"}
    horas = {(r["instalacion"], r["recurso"]): r["horas_bloqueadas_media"]
             for _, r in cuellos.iterrows()}
    colapsos = colapsos.assign(
        horas=[horas.get((r["instalacion"], r["recurso"]), 0.0) for _, r in colapsos.iterrows()])
    fila = colapsos.sort_values(["bloque_colapso", "horas"], ascending=[True, False]).iloc[0]
    return {"instalacion": fila["instalacion"], "recurso": fila["recurso"],
            "bloque": int(fila["bloque_colapso"]), "ocupacion": float(fila["ocupacion_media"]),
            "cola": float(fila["cola_media"]), "horas_bloqueadas": float(fila["horas"]),
            "nota": ""}


# =============================================================================
# Análisis de sensibilidad
# =============================================================================
def _palanca(recurso: str, p: ParamsSistema, delta: float) -> Intervencion:
    """Intervención que relaja un recurso en `delta` (fracción) sobre su dotación."""
    operativas = [i for i in p.instalaciones if i.operativa]
    if recurso == "camas":
        return Intervencion(camas_extra={i.nombre: round(i.camas * delta) for i in operativas})
    if recurso == "uci":
        return Intervencion(uci_extra={i.nombre: round(i.uci * delta)
                                       for i in operativas if i.uci > 0})
    if recurso == "quirofanos":
        return Intervencion(quirofanos_extra={i.nombre: max(1, round(i.quirofanos * delta))
                                              for i in operativas if i.quirofanos > 0})
    if recurso == "medicos":
        return Intervencion(medicos_extra=round(p.medicos_efectivos() * delta))
    if recurso == "sangre":
        stock = next(s.stock_inicial for s in p.suministros if s.nombre == "sangre")
        return Intervencion(sangre_extra=float(stock * delta))
    raise KeyError(f"recurso de sensibilidad desconocido: {recurso}")


def _objetivo(res: ResultadoMC, metrica: str) -> np.ndarray:
    """Vector por réplica de la métrica que la sensibilidad busca reducir."""
    if metrica == "muertes_evitables":
        return res.muertes_evitables
    if metrica == "muertes_evitables_48h":
        return muertes_evitables_hasta(res, 48.0)
    raise KeyError(f"métrica de sensibilidad desconocida: {metrica}")


def sensibilidad_recursos(
    p: ParamsSistema,
    n: int = 30,
    delta: float = 0.20,
    procesos: int | None = None,
    recursos: Sequence[str] = RECURSOS_SENSIBILIDAD,
    res_base: ResultadoMC | None = None,
    llegadas_extra: Sequence | None = None,
    metrica: str = "muertes_evitables",
) -> pd.DataFrame:
    """
    Relaja cada recurso por separado en `delta` y mide el efecto sobre la
    mortalidad evitable, con el mismo número de réplicas y las mismas semillas
    en todos los escenarios (números aleatorios comunes).

    La comparación es pareada réplica a réplica: `determinante` marca el recurso
    con mayor reducción entre los que tienen un IC pareado que excluye el 0.

    Para la Pregunta 2 se pasa `llegadas_extra` con la demanda del Grupo 1 y
    `metrica="muertes_evitables_48h"`: así se mide qué intervención recorta más
    las muertes evitables de las primeras 48 h del escenario con desplazados.
    """
    seeds = semillas(n)
    mismo_escenario = (res_base is not None
                       and res_base.n_replicas == n
                       and res_base.metadatos.get("semillas") == seeds
                       and res_base.metadatos.get("con_grupo1", False) == bool(llegadas_extra))
    if not mismo_escenario:
        res_base = correr_replicas(p, n=n, procesos=procesos, etiqueta="base",
                                   guardar_logs=False, semillas_fijas=seeds,
                                   llegadas_extra=llegadas_extra)
    base = _objetivo(res_base, metrica)

    filas = [{"escenario": "base", "recurso": "— base —", "delta": 0.0, "n": n,
              "metrica": metrica, "palanca": "", "muertes_evitables_media": float(np.mean(base)),
              "ic95_bajo": float(ic95(base)[1]), "ic95_alto": float(ic95(base)[2]),
              "tasa_evitable_media": float(np.mean(res_base.tasa_evitable)),
              "dif_pareada_media": 0.0, "dif_ic95_bajo": 0.0, "dif_ic95_alto": 0.0,
              "reduccion_pct": 0.0, "determinante": False}]

    for recurso in recursos:
        interv = _palanca(recurso, p, delta)
        res = correr_replicas(p, n=n, intervencion=interv, procesos=procesos,
                              etiqueta=f"+{int(delta * 100)}% {recurso}",
                              guardar_logs=False, semillas_fijas=seeds,
                              llegadas_extra=llegadas_extra)
        objetivo = _objetivo(res, metrica)
        dif = base - objetivo                         # positivo = muertes evitadas
        d_media, d_lo, d_hi = ic95(dif)
        m, lo, hi = ic95(objetivo)
        filas.append({
            "escenario": f"+{int(delta * 100)}% {recurso}", "recurso": recurso,
            "delta": delta, "n": n, "metrica": metrica, "palanca": str(interv),
            "muertes_evitables_media": float(m), "ic95_bajo": float(lo), "ic95_alto": float(hi),
            "tasa_evitable_media": float(np.mean(res.tasa_evitable)),
            "dif_pareada_media": float(d_media), "dif_ic95_bajo": float(d_lo),
            "dif_ic95_alto": float(d_hi),
            "reduccion_pct": float(100 * d_media / np.mean(base)) if np.mean(base) else 0.0,
            "determinante": False,
        })

    df = pd.DataFrame(filas)
    significativos = df[(df["recurso"] != "— base —") & (df["dif_ic95_bajo"] > 0)]
    if not significativos.empty:
        df.loc[significativos["reduccion_pct"].idxmax(), "determinante"] = True
    return df


# =============================================================================
# Pregunta 2 — intercambio con el Grupo 1
# =============================================================================
def comparar_con_grupo1(res_base: ResultadoMC, res_grupo1: ResultadoMC,
                        horas: float = 48.0, metodo_ic: str = "t") -> pd.DataFrame:
    """
    Muertes evitables adicionales que provoca la demanda del Grupo 1.

    `res_base` es el escenario que el grupo proyectó antes del intercambio y
    `res_grupo1` el mismo escenario con los desplazados inyectados. Ambos deben
    haberse corrido con las mismas semillas: la comparación es pareada réplica a
    réplica, que es lo que permite medir la diferencia con 30 corridas.
    """
    if res_base.n_replicas != res_grupo1.n_replicas:
        raise ValueError("ambos escenarios deben tener el mismo número de réplicas")
    if res_base.metadatos.get("semillas") != res_grupo1.metadatos.get("semillas"):
        raise ValueError("los escenarios no comparten semillas: la comparación no sería pareada")

    filas = []
    ventanas = ((f"primeras {horas:.0f} h", muertes_evitables_hasta(res_base, horas),
                 muertes_evitables_hasta(res_grupo1, horas)),
                (f"{HORIZONTE_H} h completas", res_base.muertes_evitables,
                 res_grupo1.muertes_evitables))
    for ventana, base, con_g1 in ventanas:
        dif = con_g1 - base                          # positivo = muertes adicionales
        d_media, d_lo, d_hi = ic95(dif, metodo=metodo_ic)
        b_media, b_lo, b_hi = ic95(base, metodo=metodo_ic)
        g_media, g_lo, g_hi = ic95(con_g1, metodo=metodo_ic)
        filas.append({
            "ventana": ventana, "n_replicas": res_base.n_replicas,
            "base_media": float(b_media), "base_ic95_bajo": float(b_lo),
            "base_ic95_alto": float(b_hi),
            "con_grupo1_media": float(g_media), "con_grupo1_ic95_bajo": float(g_lo),
            "con_grupo1_ic95_alto": float(g_hi),
            "adicionales_media": float(d_media), "adicionales_ic95_bajo": float(d_lo),
            "adicionales_ic95_alto": float(d_hi),
            "incremento_pct": float(100 * d_media / b_media) if b_media else float("nan"),
            "metodo_ic": metodo_ic,
        })
    return pd.DataFrame(filas)


# =============================================================================
# Cache en disco
# =============================================================================
# Firma del contrato de salida. Entra en la clave del cache para que un cambio de
# campos en `ResultadoMC` invalide los archivos viejos en vez de devolver objetos
# incompletos que reventarían más adelante.
_ESQUEMA_MC = tuple(f.name for f in fields(ResultadoMC))

# Un pickle escrito con otra versión del código puede fallar de varias formas al
# leerse.
_ERRORES_DE_CACHE = (pickle.UnpicklingError, AttributeError, EOFError, ImportError,
                     ModuleNotFoundError, TypeError, ValueError)


def cargar_o_correr(clave: str, fn: Callable[[], T], usar_cache: bool = True,
                    dir_cache: Path = DIR_CACHE, firma: object = None) -> T:
    """
    Devuelve el resultado cacheado de `fn` o lo calcula y lo guarda.

    Las semillas son deterministas, así que el resultado es idéntico con o sin
    cache: borrar `outputs/cache/` solo hace que el cuaderno tarde más. Si el
    archivo cacheado quedó ilegible o lo escribió una versión anterior del
    contrato, se recalcula en silencio.
    """
    sha = hashlib.sha1(repr((clave, firma, _ESQUEMA_MC)).encode()).hexdigest()[:12]
    ruta = Path(dir_cache) / f"{clave}_{sha}.pkl"
    if usar_cache and ruta.exists():
        try:
            with ruta.open("rb") as f:
                return pickle.load(f)
        except _ERRORES_DE_CACHE:
            ruta.unlink(missing_ok=True)
    resultado = fn()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("wb") as f:
        pickle.dump(resultado, f)
    return resultado
