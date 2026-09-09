"""
Dinámica de Sistemas: suministros y energía del personal.

El estado reúne los stocks de suministros y la energía de cada instalación. Su
parte continua satisface una EDO de primer orden que se integra por Euler explícito
con Δt = 0.25 h; las cargas que entrega el DES se consideran constantes durante
cada tick.

Los suministros se mueven por dos vías distintas:

- Impulso discreto (R5). El DES descuenta una cantidad fija por procedimiento
  y la acumula en un buffer. ``paso`` lo resta de golpe, antes de integrar y sin
  integrarlo.
- Flujo continuo. Solo el combustible (consumo por instalación operativa) y
  el reabastecimiento externo, que en el escenario base vale 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .params import EPS_STOCK, TOL_STOCK, ParamsSistema


@dataclass
class EstadoSD:
    """Stocks agregados y energía (0 = agotada, 1 = plena) por instalación."""

    stocks: dict[str, float]
    energia: dict[str, float]


@dataclass(frozen=True)
class _LayoutSD:
    """Orden explícito del adaptador; no depende del orden accidental de dicts."""

    suministros: tuple[str, ...]
    instalaciones: tuple[str, ...]


def _layout(params: ParamsSistema) -> _LayoutSD:
    return _LayoutSD(
        suministros=tuple(s.nombre for s in params.suministros),
        instalaciones=tuple(i.nombre for i in params.instalaciones),
    )


def _validar_claves(estado: EstadoSD, layout: _LayoutSD) -> None:
    if set(estado.stocks) != set(layout.suministros):
        raise ValueError("Los stocks de EstadoSD no coinciden con params.suministros")
    if set(estado.energia) != set(layout.instalaciones):
        raise ValueError("La energía de EstadoSD no coincide con params.instalaciones")


def _a_vector(estado: EstadoSD, layout: _LayoutSD) -> np.ndarray:
    """Serializa EstadoSD en el orden contractual suministros + instalaciones."""
    _validar_claves(estado, layout)
    vector = np.array(
        [estado.stocks[k] for k in layout.suministros]
        + [estado.energia[f] for f in layout.instalaciones],
        dtype=float,
    )
    if not np.isfinite(vector).all():
        raise ValueError("EstadoSD contiene NaN o infinito")
    return vector


def _desde_vector(vector: np.ndarray, layout: _LayoutSD, *, acotar: bool) -> EstadoSD:
    """Reconstruye EstadoSD sin perder el mapeo definido por ``layout``."""
    vector = np.asarray(vector, dtype=float)
    n_stocks = len(layout.suministros)
    if vector.shape != (n_stocks + len(layout.instalaciones),):
        raise ValueError("Dimensión incompatible para reconstruir EstadoSD")
    if not np.isfinite(vector).all():
        raise ValueError("El integrador produjo NaN o infinito")

    stocks = vector[:n_stocks]
    energia = vector[n_stocks:]
    if acotar:
        # Proyección mínima: corrige el sobrepaso numérico de un paso finito.
        stocks = np.maximum(stocks, 0.0)
        energia = np.clip(energia, 0.0, 1.0)
    return EstadoSD(
        stocks=dict(zip(layout.suministros, stocks.tolist())),
        energia=dict(zip(layout.instalaciones, energia.tolist())),
    )


def estado_inicial(params: ParamsSistema, sangre_extra: float = 0.0) -> EstadoSD:
    """Construye el estado SD inicial a partir de todos los recursos definidos."""
    if not np.isfinite(sangre_extra) or sangre_extra < 0:
        raise ValueError("sangre_extra debe ser finita y no negativa")
    stocks = {s.nombre: max(0.0, float(s.stock_inicial)) for s in params.suministros}
    if "sangre" in stocks:
        stocks["sangre"] += float(sangre_extra)
    return EstadoSD(
        stocks=stocks,
        energia={i.nombre: 1.0 for i in params.instalaciones},
    )


def derivadas(
    estado: EstadoSD,
    cargas: dict[str, dict[str, int | float]],
    params: ParamsSistema,
) -> EstadoSD:
    """
    Parte continua de la EDO. Los cinco suministros que se consumen por
    procedimiento tienen derivada 0 salvo reabastecimiento.

        dS_k/dt = r_k − [k es combustible] · c · n_operativas · S_k/(S_k+EPS)
        dE_f/dt = −alfa · carga_f/cap_f + beta · (1 − E_f)
    """
    layout = _layout(params)
    _a_vector(estado, layout)  # valida claves y finitud antes de operar

    n_operativas = sum(1 for i in params.instalaciones if i.operativa)
    d_stocks: dict[str, float] = {}
    for suministro in layout.suministros:
        stock = max(0.0, float(estado.stocks[suministro]))
        entrada = float(params.tasa_reabastecimiento_h.get(suministro, 0.0))
        salida = 0.0
        if suministro == "combustible_generadores":
            # S/(S+eps) hace que el outflow tienda a cero al agotarse el stock.
            salida = params.combustible_gal_h * n_operativas * stock / (stock + EPS_STOCK)
        d_stocks[suministro] = entrada - salida

    instalaciones = {i.nombre: i for i in params.instalaciones}
    d_energia: dict[str, float] = {}
    for nombre in layout.instalaciones:
        energia = float(estado.energia[nombre])
        carga = sum(float(n) for n in cargas.get(nombre, {}).values())
        if carga < 0:
            raise ValueError("Las cargas deben ser no negativas")
        instalacion = instalaciones[nombre]
        capacidad = float(instalacion.camas + instalacion.uci)
        # Capacidad efectiva mínima de 1: evita la división por cero en una
        # instalación sin camas sin ocultar el efecto de la carga.
        carga_relativa = carga / max(capacidad, 1.0)
        d_energia[nombre] = (
            -float(params.fatiga_alfa) * carga_relativa
            + float(params.fatiga_beta) * (1.0 - energia)
        )

    derivada = EstadoSD(d_stocks, d_energia)
    if not np.isfinite(_a_vector(derivada, layout)).all():
        raise ValueError("Las derivadas contienen NaN o infinito")
    return derivada


_FuncionVector = Callable[[np.ndarray], np.ndarray]


def euler(f: _FuncionVector, y: np.ndarray, dt: float) -> np.ndarray:
    # Euler usa la pendiente inicial: y(t+dt) ≈ y(t) + dt*f(y(t)).
    # Su error de truncamiento global es de primer orden.
    return y + dt * f(y)


def rk4(f: _FuncionVector, y: np.ndarray, dt: float) -> np.ndarray:
    # RK4 combina cuatro pendientes (dos intermedias): cuesta más evaluaciones,
    # pero reduce el error de truncamiento global a cuarto orden.
    k1 = f(y)
    k2 = f(y + 0.5 * dt * k1)
    k3 = f(y + 0.5 * dt * k2)
    k4 = f(y + dt * k3)
    return y + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)


def paso(
    estado: EstadoSD,
    consumo_discreto: dict[str, float],
    cargas: dict[str, dict[str, int | float]],
    params: ParamsSistema,
    dt: float,
    metodo: str = "euler",
) -> EstadoSD:
    """
    Avanza un tick: primero aplica el impulso de R5, luego integra la parte
    continua y por último restablece los invariantes físicos.
    """
    if not np.isfinite(dt) or dt <= 0:
        raise ValueError("dt debe ser finito y positivo")
    if metodo not in {"euler", "rk4"}:
        raise ValueError("metodo debe ser 'euler' o 'rk4'")

    layout = _layout(params)

    # 1. Impulso discreto: cantidad fija que el DES ya verificó y descontó.
    stocks = dict(estado.stocks)
    for suministro, cantidad in (consumo_discreto or {}).items():
        if suministro not in stocks:
            raise KeyError(f"consumo de un suministro desconocido: {suministro}")
        stocks[suministro] -= float(cantidad)
        assert stocks[suministro] >= -TOL_STOCK, (
            f"stock negativo tras el impulso: {suministro}={stocks[suministro]:.6g}")
    estado = EstadoSD(stocks=stocks, energia=dict(estado.energia))

    # 2. Integración de la parte continua.
    vector = _a_vector(estado, layout)

    def f(y: np.ndarray) -> np.ndarray:
        estado_intermedio = _desde_vector(y, layout, acotar=False)
        return _a_vector(derivadas(estado_intermedio, cargas, params), layout)

    actualizado = euler(f, vector, dt) if metodo == "euler" else rk4(f, vector, dt)

    # 3. Proyección de invariantes: stocks >= 0, energía en [0, 1].
    resultado = _desde_vector(actualizado, layout, acotar=True)
    for suministro, nivel in resultado.stocks.items():
        assert nivel >= 0.0, f"stock negativo tras integrar: {suministro}={nivel:.6g}"
    return resultado


def factor_fatiga(energia_f: float, factor_max: float | None = None) -> float:
    """
    S-7. Multiplicador del tiempo de servicio: 1 con energía plena y `factor_max`
    al agotarse.
    """
    from .params import FATIGA_FACTOR_MAX

    maximo = FATIGA_FACTOR_MAX if factor_max is None else float(factor_max)
    if not np.isfinite(energia_f):
        raise ValueError("energia_f debe ser finita")
    energia_acotada = float(np.clip(energia_f, 0.0, 1.0))
    return 1.0 + (maximo - 1.0) * (1.0 - energia_acotada)


def comparar_integradores(
    tasa: float = 0.5,
    dt: float = 0.5,
    t_final: float = 5.0,
) -> dict[str, np.ndarray | float]:
    """Compara Euler/RK4 contra la solución analítica exp(-tasa*t)."""
    if not all(np.isfinite(x) for x in (tasa, dt, t_final)):
        raise ValueError("Los parámetros deben ser finitos")
    if tasa < 0 or dt <= 0 or t_final <= 0:
        raise ValueError("Se requiere tasa >= 0, dt > 0 y t_final > 0")

    tiempos = [0.0]
    valores_euler = [1.0]
    valores_rk4 = [1.0]
    f = lambda y: -tasa * y
    while tiempos[-1] < t_final:
        h = min(dt, t_final - tiempos[-1])
        valores_euler.append(float(euler(f, np.array(valores_euler[-1]), h)))
        valores_rk4.append(float(rk4(f, np.array(valores_rk4[-1]), h)))
        tiempos.append(tiempos[-1] + h)

    t = np.asarray(tiempos)
    solucion = np.exp(-tasa * t)
    y_euler = np.asarray(valores_euler)
    y_rk4 = np.asarray(valores_rk4)
    return {
        "t": t,
        "exacta": solucion,
        "euler": y_euler,
        "rk4": y_rk4,
        "error_euler": float(abs(y_euler[-1] - solucion[-1])),
        "error_rk4": float(abs(y_rk4[-1] - solucion[-1])),
    }
