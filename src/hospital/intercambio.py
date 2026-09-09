"""
Incorporación del intercambio (Grupo 1 -> Grupo 2).

El Grupo 1 entrega la proyección de desplazados que requieren atención médica por
zona y bloque de tiempo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .params import BLOQUE_H, GRAVEDADES, N_BLOQUES, RAIZ, ZONAS, ParamsSistema

CSV_DEMANDA = RAIZ / "data" / "intercambio" / "grupo1_demanda.csv"
COLS = ["zona", "bloque", "heridos_leves", "heridos_moderados", "heridos_graves"]
_COL_GRAVEDAD = {"leve": "heridos_leves", "moderado": "heridos_moderados", "grave": "heridos_graves"}


def cargar_demanda_grupo1(path: Path = CSV_DEMANDA) -> pd.DataFrame | None:
    """
    Devuelve el DataFrame de demanda adicional del Grupo 1, o `None` si el CSV
    está ausente o vacío (caso pre-intercambio -> el escenario B se salta).
    """
    if not path.exists() or path.stat().st_size == 0:
        return None
    df = pd.read_csv(path)
    if df.empty:
        return None

    faltan = set(COLS) - set(df.columns)
    if faltan:
        raise ValueError(f"{path.name}: faltan columnas {sorted(faltan)}")

    zonas_malas = set(df["zona"]) - set(ZONAS)
    if zonas_malas:
        raise ValueError(f"{path.name}: zonas desconocidas {sorted(zonas_malas)}")
    if not df["bloque"].between(1, N_BLOQUES).all():
        raise ValueError(f"{path.name}: 'bloque' fuera de 1..{N_BLOQUES}")
    heridos = df[["heridos_leves", "heridos_moderados", "heridos_graves"]]
    if (heridos < 0).any().any():
        raise ValueError(f"{path.name}: hay conteos de heridos negativos")

    return df[COLS].astype({"bloque": int}).reset_index(drop=True)


@dataclass(frozen=True)
class LlegadaExtra:
    """Un lote de heridos nuevos que entra al sistema en el instante `t` (horas)."""
    zona: str
    t: float
    leve: int
    moderado: int
    grave: int

    @property
    def total(self) -> int:
        return self.leve + self.moderado + self.grave


def aplicar_demanda(params: ParamsSistema, demanda: pd.DataFrame) -> list[LlegadaExtra]:
    """
    Traduce la tabla del Grupo 1 en lotes de llegadas adicionales ordenados por
    tiempo.

    `params` se recibe para futuras reglas dependientes de la zona (p. ej. tope
    por capacidad de ruteo).
    """
    _ = params  # reservado para reglas por zona
    lotes: list[LlegadaExtra] = []
    for r in demanda.itertuples(index=False):
        t = (int(r.bloque) - 1) * BLOQUE_H
        lote = LlegadaExtra(
            zona=r.zona, t=float(t),
            leve=int(r.heridos_leves),
            moderado=int(r.heridos_moderados),
            grave=int(r.heridos_graves),
        )
        if lote.total > 0:
            lotes.append(lote)
    lotes.sort(key=lambda x: (x.t, x.zona))
    return lotes


def resumen_impacto(params: ParamsSistema, demanda: pd.DataFrame) -> pd.DataFrame:
    """
    Cuánto crece el pool de heridos por zona respecto al escenario base.
    """
    base = {z.zona: {g: getattr(z, g) for g in GRAVEDADES} for z in params.heridos_t0}
    filas = []
    for zona in ZONAS:
        add = {g: int(demanda.loc[demanda["zona"] == zona, _COL_GRAVEDAD[g]].sum())
               for g in GRAVEDADES}
        b_tot = sum(base[zona].values())
        a_tot = sum(add.values())
        filas.append({
            "zona": zona,
            "base_total": b_tot,
            "grupo1_total": a_tot,
            "nuevo_total": b_tot + a_tot,
            "incremento_pct": round(100 * a_tot / b_tot, 1) if b_tot else float("nan"),
        })
    return pd.DataFrame(filas)


if __name__ == "__main__":
    from .params import cargar
    d = cargar_demanda_grupo1()
    if d is None:
        print("grupo1_demanda.csv vacío — escenario base únicamente (pre-intercambio)")
    else:
        p = cargar()
        print(resumen_impacto(p, d).to_string(index=False))
        print(f"\n{len(aplicar_demanda(p, d))} lotes de llegada adicionales")
