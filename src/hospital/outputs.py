"""
T1 (P1) — Tablas que el Grupo 2 entrega al Grupo 7.

Las 3 funciones toman el resultado agregado de Monte Carlo (`montecarlo.py`, P4),
escriben un CSV en `outputs/tables/` y devuelven el DataFrame para el notebook.

Excel 6 — OUTPUT REQUERIDO:
  a) Saturación hospitalaria: % ocupación de camas por instalación en cada bloque de 6 h.
  b) Los dos cuellos de botella críticos (qué recurso/instalación falla primero y cuándo).
  c) Recursos médicos adicionales mínimos para mantener la mortalidad evitable < 15 %.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .params import RAIZ

DIR_TABLAS = RAIZ / "outputs" / "tables"


def tabla_saturacion(res_mc) -> pd.DataFrame:
    """(a) % ocupación de camas por instalación × bloque — media e IC 95 %."""
    raise NotImplementedError("P1: depende del formato de snapshots de montecarlo (P4)")


def tabla_cuellos_botella(res_mc) -> pd.DataFrame:
    """(b) Ranking de horas-recurso bloqueadas; los 2 primeros son el entregable."""
    raise NotImplementedError("P1: usa res_mc.bloqueo_horas agregado")


def tabla_recursos_minimos(res_mc) -> pd.DataFrame:
    """(c) Barrido +camas / +médicos / +sangre hasta mortalidad evitable < 15 %."""
    raise NotImplementedError("P1: requiere correr montecarlo con distintas Intervencion")


def _guardar(df: pd.DataFrame, nombre: str) -> Path:
    DIR_TABLAS.mkdir(parents=True, exist_ok=True)
    p = DIR_TABLAS / nombre
    df.to_csv(p, index=False)
    return p
