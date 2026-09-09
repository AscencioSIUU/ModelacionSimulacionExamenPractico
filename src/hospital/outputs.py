"""
T1 (P1) — Tablas que el Grupo 2 entrega al Grupo 7.

Excel 6 — OUTPUT REQUERIDO:
  a) Saturación hospitalaria: % ocupación de camas por instalación en cada bloque de 6 h.
  b) Los dos cuellos de botella críticos (qué recurso/instalación falla primero y cuándo).
  c) Recursos médicos adicionales mínimos para mantener la mortalidad evitable < 15 %.

Contrato de entrada — `ResultadoMC`
-----------------------------------
Es lo que `montecarlo.correr_replicas` (P4) debe devolver. P1 lo define aquí
porque P1 es el consumidor; P4 sólo tiene que rellenar estos arrays.

Todas las funciones escriben su CSV en `outputs/tables/` y devuelven el DataFrame.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from .params import Intervencion, ParamsSistema

DIR_TABLAS = Path(__file__).resolve().parents[2] / "outputs" / "tables"


@dataclass
class ResultadoMC:
    """Salida agregada de las N réplicas. Ejes: r = réplica, i = instalación, b = bloque."""
    n_replicas: int
    instalaciones: list[str]
    n_bloques: int
    recursos: list[str]                    # p. ej. ["cama_general", "cama_uci", "medico", "sangre", ...]
    ocup_camas: np.ndarray                 # [r, i, b]  fracción 0..1 de camas generales ocupadas
    ocup_uci: np.ndarray                   # [r, i, b]  fracción 0..1 de camas UCI ocupadas
    bloqueo_horas: np.ndarray             # [r, i, recurso]  horas con cola y ese recurso agotado
    muertes_evitables: np.ndarray         # [r]
    muertes_clinicas: np.ndarray          # [r]
    generados: np.ndarray                 # [r]
    metadatos: dict = field(default_factory=dict)

    @property
    def tasa_evitable(self) -> np.ndarray:
        """Muertes evitables / heridos generados, por réplica."""
        return self.muertes_evitables / np.maximum(self.generados, 1)


# --- helpers ------------------------------------------------------------------
def _ic95(x: np.ndarray, axis: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Media e IC 95 % por PERCENTILES (2.5 / 97.5) — los colapsos son sesgados."""
    media = np.mean(x, axis=axis)
    lo = np.percentile(x, 2.5, axis=axis)
    hi = np.percentile(x, 97.5, axis=axis)
    return media, lo, hi


def _guardar(df: pd.DataFrame, nombre: str) -> Path:
    DIR_TABLAS.mkdir(parents=True, exist_ok=True)
    p = DIR_TABLAS / nombre
    df.to_csv(p, index=False)
    return p


# --- (a) saturación hospitalaria --------------------------------------------
def tabla_saturacion(res: ResultadoMC) -> pd.DataFrame:
    """% de ocupación de camas por instalación × bloque, media + IC 95 %."""
    filas = []
    for tipo, arr in (("general", res.ocup_camas), ("uci", res.ocup_uci)):
        media, lo, hi = _ic95(arr, axis=0)               # -> [i, b]
        for i, inst in enumerate(res.instalaciones):
            for b in range(res.n_bloques):
                filas.append({
                    "instalacion": inst,
                    "tipo_cama": tipo,
                    "bloque": b + 1,
                    "ocupacion_media_pct": round(100 * media[i, b], 1),
                    "ic95_bajo_pct": round(100 * lo[i, b], 1),
                    "ic95_alto_pct": round(100 * hi[i, b], 1),
                })
    df = pd.DataFrame(filas)
    _guardar(df, "saturacion_por_bloque.csv")
    return df


# --- (b) cuellos de botella --------------------------------------------------
def tabla_cuellos_botella(res: ResultadoMC, top: int = 2) -> pd.DataFrame:
    """
    Ranking de (instalación, recurso) por horas-recurso bloqueadas (media de las
    réplicas). Los `top` primeros son el entregable (b) para el Grupo 7.
    """
    media, lo, hi = _ic95(res.bloqueo_horas, axis=0)      # -> [i, recurso]
    filas = []
    for i, inst in enumerate(res.instalaciones):
        for k, rec in enumerate(res.recursos):
            filas.append({
                "instalacion": inst,
                "recurso": rec,
                "horas_bloqueadas_media": round(float(media[i, k]), 1),
                "ic95_bajo": round(float(lo[i, k]), 1),
                "ic95_alto": round(float(hi[i, k]), 1),
            })
    df = pd.DataFrame(filas).sort_values("horas_bloqueadas_media", ascending=False)
    df = df.reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)
    df["critico"] = df["rank"] <= top
    _guardar(df, "cuellos_botella.csv")
    return df


# --- (c) recursos adicionales mínimos --------------------------------------
_PALANCAS: dict[str, tuple[str, float, Callable[[float, ParamsSistema], Intervencion]]] = {
    # nombre -> (unidad, paso por iteración, constructor de Intervencion)
    "camas_generales": ("camas", 20, lambda n, p: Intervencion(
        camas_extra={i.nombre: int(n) for i in p.instalaciones if i.operativa})),
    "camas_uci": ("camas UCI", 4, lambda n, p: Intervencion(
        uci_extra={i.nombre: int(n) for i in p.instalaciones if i.operativa and i.uci > 0})),
    "medicos": ("médicos", 10, lambda n, p: Intervencion(medicos_extra=int(n))),
    "sangre": ("unidades", 100, lambda n, p: Intervencion(sangre_extra=float(n))),
}


def tabla_recursos_minimos(
    run_fn: Callable[[Intervencion | None], ResultadoMC],
    params: ParamsSistema,
    *,
    umbral: float = 0.15,
    max_iter: int = 8,
) -> pd.DataFrame:
    """
    Para cada palanca (camas / camas UCI / médicos / sangre), incrementa en pasos
    fijos y vuelve a correr Monte Carlo (`run_fn`) hasta que la tasa media de
    mortalidad evitable baje del `umbral`. Reporta la cantidad mínima hallada.

    `run_fn(None)` debe devolver el escenario base.
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
