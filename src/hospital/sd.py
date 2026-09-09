"""Sustrato de Dinámica de Sistemas: suministros y energía del personal.

El estado satisface una EDO de primer orden ``dx/dt = f(t, x)``. En este
modelo, ``x`` reúne los stocks agregados y la energía de cada instalación;
las cargas que entrega el DES se consideran constantes durante cada tick SD.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .params import EPS_STOCK, ParamsSistema


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
        # La EDO saturante frena el consumo en cero; esta proyección mínima
        # corrige el sobrepaso numérico que un paso finito aún puede producir.
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
    """Evalúa las derivadas de stocks y energía para las cargas del DES."""
    layout = _layout(params)
    _a_vector(estado, layout)  # valida claves y finitud antes de operar

    carga_total_gravedad: dict[str, float] = {}
    for cargas_inst in cargas.values():
        for gravedad, cantidad in cargas_inst.items():
            cantidad = float(cantidad)
            if not np.isfinite(cantidad) or cantidad < 0:
                raise ValueError("Las cargas deben ser finitas y no negativas")
            carga_total_gravedad[gravedad] = carga_total_gravedad.get(gravedad, 0.0) + cantidad

    d_stocks: dict[str, float] = {}
    for suministro in layout.suministros:
        stock = max(0.0, float(estado.stocks[suministro]))
        consumo = sum(
            float(coef) * carga_total_gravedad.get(gravedad, 0.0)
            for gravedad, coef in params.coef_consumo.get(suministro, {}).items()
        )
        # S/(S+eps) hace que el outflow tienda naturalmente a cero al agotarse S.
        d_stocks[suministro] = -consumo * stock / (stock + EPS_STOCK)

    instalaciones = {i.nombre: i for i in params.instalaciones}
    d_energia: dict[str, float] = {}
    for nombre in layout.instalaciones:
        energia = float(estado.energia[nombre])
        carga = sum(float(n) for n in cargas.get(nombre, {}).values())
        instalacion = instalaciones[nombre]
        capacidad = float(instalacion.camas + instalacion.uci)
        # En el flujo normal una instalación con capacidad cero no recibe carga.
        # Si llega una carga inconsistente, una capacidad efectiva mínima de 1
        # evita la división por cero sin ocultar su efecto de agotamiento.
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
    cargas: dict[str, dict[str, int | float]],
    params: ParamsSistema,
    dt: float,
    metodo: str = "rk4",
) -> EstadoSD:
    """Avanza un tick mediante Euler o RK4 y restablece invariantes físicos."""
    if not np.isfinite(dt) or dt <= 0:
        raise ValueError("dt debe ser finito y positivo")
    if metodo not in {"euler", "rk4"}:
        raise ValueError("metodo debe ser 'euler' o 'rk4'")

    layout = _layout(params)
    vector = _a_vector(estado, layout)

    def f(y: np.ndarray) -> np.ndarray:
        estado_intermedio = _desde_vector(y, layout, acotar=False)
        return _a_vector(derivadas(estado_intermedio, cargas, params), layout)

    actualizado = euler(f, vector, dt) if metodo == "euler" else rk4(f, vector, dt)
    return _desde_vector(actualizado, layout, acotar=True)


def factor_fatiga(energia_f: float) -> float:
    """Multiplicador de servicio lineal: 1 con energía plena y 2 al agotarse."""
    if not np.isfinite(energia_f):
        raise ValueError("energia_f debe ser finita")
    energia_acotada = float(np.clip(energia_f, 0.0, 1.0))
    return 2.0 - energia_acotada


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
