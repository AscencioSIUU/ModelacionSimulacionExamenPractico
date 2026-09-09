"""
T1 — Incorporación del intercambio presencial (Grupo 1 -> Grupo 2).

El Grupo 1 entrega la proyección de desplazados que requieren atención médica por
zona y bloque de tiempo. Llega el día del intercambio: hasta entonces
`data/intercambio/grupo1_demanda.csv` está VACÍO y `cargar_demanda_grupo1`
devuelve None -> el escenario B (con demanda real) simplemente se salta.

Formato esperado del CSV:
    zona,bloque,heridos_leves,heridos_moderados,heridos_graves
    Z1,1,180,60,25
    ...
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .params import RAIZ, ParamsSistema

CSV_DEMANDA = RAIZ / "data" / "intercambio" / "grupo1_demanda.csv"
COLS = ["zona", "bloque", "heridos_leves", "heridos_moderados", "heridos_graves"]


def cargar_demanda_grupo1(path: Path = CSV_DEMANDA) -> pd.DataFrame | None:
    """Devuelve el DataFrame de demanda adicional, o None si el CSV está vacío/ausente."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    df = pd.read_csv(path)
    if df.empty:
        return None
    faltan = set(COLS) - set(df.columns)
    if faltan:
        raise ValueError(f"grupo1_demanda.csv: faltan columnas {faltan}")
    return df


def aplicar_demanda(params: ParamsSistema, demanda: pd.DataFrame) -> dict:
    """
    Combina los heridos T0 del modelo propio con la demanda del Grupo 1 por
    (zona, bloque). Devuelve una estructura que `simulacion.correr` inyecta como
    llegadas adicionales programadas en el bloque correspondiente.

    Documentar en el reporte: cómo esta data alteró los supuestos iniciales
    (pool por zona, tramo de llegada, adelanto del colapso).
    """
    raise NotImplementedError("T1: bloqueado hasta tener el CSV real del Grupo 1")
