"""
Tablas que el Grupo 2 entrega al Grupo 7.

OUTPUT REQUERIDO:
  a) Saturación hospitalaria: % ocupación de camas por instalación en cada bloque de 6 h.
  b) Los dos cuellos de botella críticos (qué recurso o instalación falla primero).
  c) Recursos médicos adicionales mínimos para mantener la mortalidad evitable < 15 %.

Aquí vive también `ResultadoMC`, el contrato de salida agregada que produce
`montecarlo.correr_replicas` y que consumen estas tablas y `plots.py`.

Las tres funciones escriben su CSV en `outputs/tables/` y devuelven el DataFrame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from .params import Intervencion, ParamsSistema

DIR_TABLAS = Path(__file__).resolve().parents[2] / "outputs" / "tables"

# Valor crítico de la t de Student a dos colas al 95 %, por grados de libertad.
_T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
         8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
         15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
         21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056,
         27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042, 35: 2.030, 40: 2.021}


@dataclass
class ResultadoMC:
    """Salida agregada de las N réplicas. Ejes: r = réplica, i = instalación, b = bloque."""

    n_replicas: int
    instalaciones: list[str]
    n_bloques: int
    recursos: list[str]                    # servibles + suministros
    ocup_camas: np.ndarray                 # [r, i, b]  fracción 0..1 de camas generales
    ocup_uci: np.ndarray                   # [r, i, b]
    ocup_quirofanos: np.ndarray            # [r, i, b]
    bloqueo_horas: np.ndarray              # [r, i, recurso]  horas con cola y recurso agotado
    muertes_evitables: np.ndarray          # [r]
    muertes_clinicas: np.ndarray           # [r]
    generados: np.ndarray                  # [r]
    # Muertes por bloque de 6 h. Se guardan aparte del patient_log para poder
    # medir ventanas de tiempo (p. ej. las primeras 48 h) sin arrastrar los logs.
    muertes_evitables_bloque: np.ndarray   # [r, b]
    muertes_clinicas_bloque: np.ndarray    # [r, b]
    patient_log: pd.DataFrame = field(default_factory=pd.DataFrame)
    state_log: pd.DataFrame = field(default_factory=pd.DataFrame)
    stock_log: pd.DataFrame = field(default_factory=pd.DataFrame)
    metadatos: dict = field(default_factory=dict)

    @property
    def tasa_evitable(self) -> np.ndarray:
        """Muertes evitables / heridos generados, por réplica."""
        return self.muertes_evitables / np.maximum(self.generados, 1)


def _t_critico(gl: int) -> float:
    """t de Student al 97.5 % con `gl` grados de libertad; 1.96 para gl grandes."""
    if gl <= 0:
        return float("nan")
    if gl in _T975:
        return _T975[gl]
    if gl > 40:
        return 1.96
    return _T975[max(k for k in _T975 if k <= gl)]


def ic95(x: np.ndarray, axis: int = 0, metodo: str = "t"
         ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Media e intervalo de confianza al 95 % entre réplicas."""
    x = np.asarray(x, dtype=float)
    media = np.mean(x, axis=axis)
    if metodo == "percentil":
        return media, np.percentile(x, 2.5, axis=axis), np.percentile(x, 97.5, axis=axis)
    if metodo != "t":
        raise ValueError("metodo debe ser 't' o 'percentil'")
    n = x.shape[axis]
    if n < 2:
        nan = np.full_like(media, np.nan, dtype=float)
        return media, nan, nan
    semiancho = _t_critico(n - 1) * np.std(x, axis=axis, ddof=1) / np.sqrt(n)
    return media, media - semiancho, media + semiancho


def _guardar(df: pd.DataFrame, nombre: str) -> Path:
    DIR_TABLAS.mkdir(parents=True, exist_ok=True)
    p = DIR_TABLAS / nombre
    df.to_csv(p, index=False)
    return p


# --- (a) saturación hospitalaria ----------------------------------------------
def tabla_saturacion(res: ResultadoMC, metodo_ic: str = "t") -> pd.DataFrame:
    """% de ocupación de camas por instalación × bloque, media + IC 95 %."""
    filas = []
    for tipo, arr in (("general", res.ocup_camas), ("uci", res.ocup_uci)):
        media, lo, hi = ic95(arr, axis=0, metodo=metodo_ic)       # -> [i, b]
        for i, inst in enumerate(res.instalaciones):
            for b in range(res.n_bloques):
                filas.append({
                    "instalacion": inst,
                    "tipo_cama": tipo,
                    "bloque": b + 1,
                    "ocupacion_media_pct": round(100 * media[i, b], 1),
                    "ic95_bajo_pct": round(100 * lo[i, b], 1),
                    "ic95_alto_pct": round(100 * hi[i, b], 1),
                    "metodo_ic": metodo_ic,
                })
    df = pd.DataFrame(filas)
    _guardar(df, "saturacion_por_bloque.csv")
    return df


# --- (b) cuellos de botella ----------------------------------------------------
def tabla_cuellos_botella(res: ResultadoMC, top: int = 2, metodo_ic: str = "t",
                          guardar: bool = True) -> pd.DataFrame:
    """
    Ranking de (instalación, recurso) por horas-recurso bloqueadas, promediadas
    entre réplicas.
    """
    media, lo, hi = ic95(res.bloqueo_horas, axis=0, metodo=metodo_ic)   # -> [i, recurso]
    filas = []
    for i, inst in enumerate(res.instalaciones):
        for k, rec in enumerate(res.recursos):
            filas.append({
                "instalacion": inst,
                "recurso": rec,
                "horas_bloqueadas_media": round(float(media[i, k]), 1),
                "ic95_bajo": round(float(lo[i, k]), 1),
                "ic95_alto": round(float(hi[i, k]), 1),
                "metodo_ic": metodo_ic,
            })
    df = pd.DataFrame(filas).sort_values("horas_bloqueadas_media", ascending=False)
    df = df.reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)
    df["critico"] = df["rank"] <= top
    if guardar:
        _guardar(df, "cuellos_botella.csv")
    return df


# --- (c) recursos adicionales mínimos ------------------------------------------
_PALANCAS: dict[str, tuple[str, float, Callable[[float, ParamsSistema], Intervencion]]] = {
    # nombre -> (unidad, paso por iteración, constructor de Intervencion)
    "camas_generales": ("camas", 40, lambda n, p: Intervencion(
        camas_extra={i.nombre: int(n) for i in p.instalaciones if i.operativa})),
    "camas_uci": ("camas UCI", 8, lambda n, p: Intervencion(
        uci_extra={i.nombre: int(n) for i in p.instalaciones if i.operativa and i.uci > 0})),
    "quirofanos": ("quirófanos", 4, lambda n, p: Intervencion(
        quirofanos_extra={i.nombre: int(n) for i in p.instalaciones
                          if i.operativa and i.quirofanos > 0})),
    "medicos": ("médicos", 20, lambda n, p: Intervencion(medicos_extra=int(n))),
    "sangre": ("unidades", 300, lambda n, p: Intervencion(sangre_extra=float(n))),
}


def tabla_recursos_minimos(
    run_fn: Callable[[Intervencion | None], ResultadoMC],
    params: ParamsSistema,
    *,
    umbral: float = 0.15,
    max_iter: int = 4,
) -> pd.DataFrame:
    """
    Para cada palanca incrementa la dotación en pasos fijos y vuelve a correr
    Monte Carlo (`run_fn`) hasta que la tasa media de mortalidad evitable baje
    del `umbral`. Reporta la cantidad mínima hallada.
    """
    base = run_fn(None)
    tasa_base = float(np.mean(base.tasa_evitable))

    filas = [{
        "recurso": "— base —", "unidad": "", "cantidad_minima": 0,
        "tasa_evitable_resultante": round(tasa_base, 4),
        "alcanza_umbral": tasa_base < umbral,
    }]

    for nombre, (unidad, paso, constructor) in _PALANCAS.items():
        alcanzado = False
        cantidad = 0
        tasa = tasa_base
        for it in range(1, max_iter + 1):
            cantidad = paso * it
            res = run_fn(constructor(cantidad, params))
            tasa = float(np.mean(res.tasa_evitable))
            if tasa < umbral:
                alcanzado = True
                break
        filas.append({
            "recurso": nombre,
            "unidad": unidad,
            "cantidad_minima": cantidad if alcanzado else np.nan,
            "tasa_evitable_resultante": round(tasa, 4),
            "alcanza_umbral": alcanzado,
        })

    df = pd.DataFrame(filas)
    _guardar(df, "recursos_minimos.csv")
    return df
