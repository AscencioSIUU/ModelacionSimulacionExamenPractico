"""
Gráficas para el reporte. Todas guardan un PNG en `outputs/figures/`.

Cada función consume un DataFrame ya agregado por `montecarlo` (media e IC 95 %),
no el `ResultadoMC` crudo. De esta forma el módulo no depende del motor y se 
puede probar con datos sintéticos.

Convención común: si se pasa `ax`, la función dibuja ahí y no guarda archivo
(sirve para componer subplots); si no, crea su propia figura y la guarda.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from . import sd

DIR_FIGURAS = Path(__file__).resolve().parents[2] / "outputs" / "figures"


def _figura(ax: Axes | None, figsize=(8, 4.5)) -> tuple[Figure, Axes, bool]:
    """Devuelve (figura, ejes, propia). `propia` indica si hay que guardarla."""
    if ax is not None:
        return ax.figure, ax, False
    fig, ax = plt.subplots(figsize=figsize)
    return fig, ax, True


def _guardar(fig: Figure, nombre: str) -> Path:
    DIR_FIGURAS.mkdir(parents=True, exist_ok=True)
    ruta = DIR_FIGURAS / nombre
    fig.tight_layout()
    fig.savefig(ruta, dpi=130)
    return ruta


def serie_con_banda(ax: Axes, t, media, lo, hi, label: str | None = None,
                    color: str | None = None, alpha: float = 0.20) -> Axes:
    """Helper base: la media como línea y el IC 95 % como banda del mismo color."""
    linea, = ax.plot(t, media, label=label, color=color, linewidth=1.8)
    ax.fill_between(t, lo, hi, color=linea.get_color(), alpha=alpha, linewidth=0)
    return ax


def ocupacion_por_bloque(df_bloques: pd.DataFrame, recurso: str = "cama_general",
                         instalaciones=None, ax: Axes | None = None,
                         guardar: bool = True, nombre: str | None = None) -> Figure:
    """Ocupación por instalación en los 12 bloques, con banda de confianza."""
    fig, ax, propia = _figura(ax)
    sub = df_bloques[df_bloques["recurso"] == recurso]
    nombres = instalaciones if instalaciones is not None else sorted(sub["instalacion"].unique())
    for inst in nombres:
        g = sub[sub["instalacion"] == inst].sort_values("bloque")
        serie_con_banda(ax, g["bloque"], 100 * g["ocupacion_media"],
                        100 * g["ic95_bajo"], 100 * g["ic95_alto"], label=inst)
    ax.axhline(100, color="0.4", linestyle="--", linewidth=1)
    ax.set_xlabel("Bloque de 6 h")
    ax.set_ylabel("Ocupación (%)")
    ax.set_title(f"Ocupación de {recurso.replace('_', ' ')} por instalación")
    ax.set_xticks(range(1, 13))
    ax.legend(fontsize=8)
    if propia and guardar:
        _guardar(fig, nombre or f"ocupacion_{recurso}.png")
    return fig


def cola_por_triage(df_cola: pd.DataFrame, ax: Axes | None = None, guardar: bool = True,
                    nombre: str = "cola_por_triage.png") -> Figure:
    """Pacientes esperando atención a lo largo del tiempo, separados por gravedad."""
    fig, ax, propia = _figura(ax)
    for triage in ("grave", "moderado", "leve"):
        g = df_cola[df_cola["triage"] == triage].sort_values("t")
        if g.empty:
            continue
        serie_con_banda(ax, g["t"], g["cola_media"], g["ic95_bajo"], g["ic95_alto"],
                        label=triage)
    ax.set_xlabel("Horas desde el sismo")
    ax.set_ylabel("Pacientes en cola")
    ax.set_title("Longitud de cola por gravedad")
    ax.legend(fontsize=8)
    if propia and guardar:
        _guardar(fig, nombre)
    return fig


def stocks_en_tiempo(df_stocks: pd.DataFrame, suministros=None, guardar: bool = True,
                     nombre: str = "stocks.png") -> Figure:
    """Un panel por suministro, con el instante de agotamiento marcado."""
    nombres = list(suministros) if suministros is not None else sorted(df_stocks["suministro"].unique())
    filas = int(np.ceil(len(nombres) / 3))
    fig, ejes = plt.subplots(filas, 3, figsize=(11, 3 * filas), squeeze=False)
    for eje, suministro in zip(ejes.flat, nombres):
        g = df_stocks[df_stocks["suministro"] == suministro].sort_values("t")
        serie_con_banda(eje, g["t"], g["nivel_medio"], g["ic95_bajo"], g["ic95_alto"])
        agotado = g[g["nivel_medio"] <= 0]
        if not agotado.empty:
            eje.axvline(float(agotado["t"].iloc[0]), color="crimson", linestyle=":", linewidth=1.2)
        eje.set_title(suministro.replace("_", " "), fontsize=9)
        eje.set_xlabel("Horas")
        eje.set_ylabel("Nivel")
    for eje in ejes.flat[len(nombres):]:
        eje.axis("off")
    if guardar:
        _guardar(fig, nombre)
    return fig


def muertes_acumuladas(df_muertes: pd.DataFrame, tipos=("evitable",), ax: Axes | None = None,
                       guardar: bool = True, nombre: str = "muertes_acumuladas.png") -> Figure:
    """Muertes acumuladas por bloque, con banda de confianza."""
    fig, ax, propia = _figura(ax)
    for tipo in tipos:
        g = df_muertes[df_muertes["tipo"] == tipo].sort_values("bloque")
        serie_con_banda(ax, g["bloque"], g["muertes_media"], g["ic95_bajo"], g["ic95_alto"],
                        label=f"muertes {tipo}s")
    ax.set_xlabel("Bloque de 6 h")
    ax.set_ylabel("Muertes acumuladas")
    ax.set_title("Muertes acumuladas en espera")
    ax.set_xticks(range(1, 13))
    ax.legend(fontsize=8)
    if propia and guardar:
        _guardar(fig, nombre)
    return fig


def estabilidad(df_estab: pd.DataFrame, metrica: str = "tasa_evitable",
                ax: Axes | None = None, guardar: bool = True,
                nombre: str = "estabilidad.png") -> Figure:
    """Media acumulada contra el número de réplicas (confirma que ya se aplanó)."""
    fig, ax, propia = _figura(ax)
    g = df_estab[df_estab["metrica"] == metrica].sort_values("k")
    serie_con_banda(ax, g["k"], g["media_acumulada"], g["ic95_bajo"], g["ic95_alto"],
                    label="media acumulada")
    ax.axhline(float(g["media_acumulada"].iloc[-1]), color="0.4", linestyle="--", linewidth=1,
               label="media final")
    estables = g[g["estable"]]
    if not estables.empty:
        ax.axvline(int(estables["k"].iloc[0]), color="seagreen", linestyle=":", linewidth=1.2,
                   label=f"estable desde k={int(estables['k'].iloc[0])}")
    ax.set_xlabel("Réplicas acumuladas")
    ax.set_ylabel(metrica.replace("_", " "))
    ax.set_title(f"Estabilidad de la media: {metrica.replace('_', ' ')}")
    ax.legend(fontsize=8)
    if propia and guardar:
        _guardar(fig, nombre)
    return fig


def sensibilidad(df_sens: pd.DataFrame, ax: Axes | None = None, guardar: bool = True,
                 nombre: str = "sensibilidad.png") -> Figure:
    """Muertes evitadas al relajar cada recurso, con el IC de la diferencia pareada."""
    fig, ax, propia = _figura(ax)
    g = df_sens[df_sens["recurso"] != "— base —"].sort_values("dif_pareada_media")
    error = np.vstack([g["dif_pareada_media"] - g["dif_ic95_bajo"],
                       g["dif_ic95_alto"] - g["dif_pareada_media"]])
    colores = ["seagreen" if d else "0.6" for d in g["determinante"]]
    ax.barh(g["recurso"], g["dif_pareada_media"], xerr=error, color=colores,
            error_kw={"ecolor": "0.3", "capsize": 3})
    ax.axvline(0, color="0.3", linewidth=1)
    ax.set_xlabel("Muertes evitables reducidas (diferencia pareada)")
    ax.set_title("Sensibilidad: efecto de relajar cada recurso")
    if propia and guardar:
        _guardar(fig, nombre)
    return fig


def euler_vs_rk4(comparacion: dict | None = None, ax: Axes | None = None,
                 guardar: bool = True, nombre: str = "euler_vs_rk4.png") -> Figure:
    """Error de cada integrador contra la solución analítica de una EDO de prueba."""
    datos = comparacion if comparacion is not None else sd.comparar_integradores()
    fig, ax, propia = _figura(ax)
    ax.plot(datos["t"], datos["exacta"], color="0.3", linewidth=2, label="solución exacta")
    ax.plot(datos["t"], datos["euler"], "o--", markersize=4, label="Euler (orden 1)")
    ax.plot(datos["t"], datos["rk4"], "s--", markersize=4, label="RK4 (orden 4)")
    ax.set_xlabel("t")
    ax.set_ylabel("y(t)")
    ax.set_title("Euler vs RK4 sobre dy/dt = −k·y")
    ax.legend(fontsize=8)
    if propia and guardar:
        _guardar(fig, nombre)
    return fig
