"""
Bucle de simulación y acoplamiento DES <-> SD.

`correr()` arma el calendario, agenda las llegadas (base y las del Grupo 1), los
cortes de bloque, los pasos del sustrato SD y la intervención, y despacha
eventos hasta agotar el horizonte de 72 h.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import des, params as P, sd
from .intercambio import LlegadaExtra
from .params import (BLOQUE_H, GRAVEDADES, HORIZONTE_H, N_BLOQUES, Intervencion,
                     ParamsSistema)


@dataclass
class ResultadoCorrida:
    """Salida de UNA corrida."""

    replica: int
    instalaciones: list[str]
    n_bloques: int
    recursos: list[str]
    ocup_camas: np.ndarray        # [i, b]  fracción 0..1 al cierre del bloque
    ocup_uci: np.ndarray          # [i, b]
    ocup_quirofanos: np.ndarray   # [i, b]
    bloqueo_horas: np.ndarray     # [i, k]  horas con cola y el recurso k agotado
    muertes_evitables: int
    muertes_clinicas: int
    atendidos: int
    en_sistema: int
    generados: int
    patient_log: pd.DataFrame     # params.COLS_PATIENT_LOG
    state_log: pd.DataFrame       # params.COLS_STATE_LOG
    stock_log: pd.DataFrame       # params.COLS_STOCK_LOG


def _cola_de_recurso(inst: des.EstadoInstalacion, recurso: str) -> int:
    """Colas de las gravedades que exigen ese recurso."""
    return sum(len(inst.colas[g]) for g in GRAVEDADES
               if recurso in P.RECURSOS_POR_GRAVEDAD[g])


def correr(
    params: ParamsSistema,
    seed: int,
    intervencion: Intervencion | None = None,
    llegadas_extra: list[LlegadaExtra] | None = None,
    replica: int = 0,
) -> ResultadoCorrida:
    rng = P.make_rng(seed)
    cal = des.Calendario()

    nombres_inst = [i.nombre for i in params.instalaciones if i.operativa]
    zonas_por_nombre = {z.zona: z for z in params.heridos_t0}
    dt = params.dt_sd_h

    sangre_extra = intervencion.sangre_extra if intervencion else 0.0
    derivar_leves = bool(intervencion and intervencion.derivar_leves_aparte)
    estado_sd = sd.estado_inicial(params, sangre_extra=sangre_extra)
    recursos = des.crear_recursos(params, derivar_leves=derivar_leves,
                                  nivel_insumos=estado_sd.stocks)
    des.actualizar_modo_emergencia(recursos, params)
    factor_fatiga = {n: 1.0 for n in nombres_inst}

    pacientes: dict[int, des.Paciente] = {}
    contador_id = itertools.count()
    stats = {"muertes_evitables": 0, "muertes_clinicas": 0, "atendidos": 0, "generados": 0}
    ocup_camas = np.zeros((len(nombres_inst), N_BLOQUES))
    ocup_uci = np.zeros((len(nombres_inst), N_BLOQUES))
    ocup_quirofanos = np.zeros((len(nombres_inst), N_BLOQUES))
    filas_state: list[tuple] = []
    filas_stock: list[tuple] = []

    # --- helpers internos (closures sobre el estado de esta corrida) -----------
    def actualizar_bloqueos(t: float) -> None:
        """Horas con cola y el recurso agotado, por instalación y recurso."""
        for nombre, inst in recursos.instalaciones.items():
            for r in P.RECURSOS_SERVIBLES:
                hay_cola = _cola_de_recurso(inst, r) > 0
                recursos.marcar_bloqueo(
                    t, nombre, r, hay_cola and des.ocupados(inst, r) >= des.capacidad(inst, r))
            for s in params.suministros:
                bloqueado = any(
                    inst.colas[g] and s.nombre in params.consumo_por_procedimiento[g]
                    and recursos.nivel_insumos[s.nombre] < params.consumo_por_procedimiento[g][s.nombre]
                    for g in GRAVEDADES)
                recursos.marcar_bloqueo(t, nombre, s.nombre, bloqueado)

    def admitir_e_iniciar(t: float, instalacion: str) -> None:
        resultado = des.admitir_pendientes(recursos, instalacion, params)
        for paciente in resultado.admitidos:
            cal.cancelar(paciente.id_evento_muerte_cola)     # sobrevivió la cola
            paciente.id_evento_muerte_cola = None
            paciente.t_inicio_atencion = t
            paciente.estado = "en_atencion"
            servicio = des.tiempo_servicio(rng, params, paciente.gravedad,
                                           factor_fatiga=factor_fatiga[instalacion])
            cal.agendar(t + servicio, "FIN_ATENCION", paciente_id=paciente.id)

    def muestrear(t: float) -> None:
        for nombre in nombres_inst:
            inst = recursos.instalaciones[nombre]
            for r in P.RECURSOS_SERVIBLES:
                filas_state.append((replica, t, nombre, r, des.ocupados(inst, r),
                                    des.capacidad(inst, r), _cola_de_recurso(inst, r)))
        for suministro, nivel in recursos.nivel_insumos.items():
            filas_stock.append((replica, t, suministro, nivel))

    def procesar_llegada(t: float, evento: des.Evento) -> None:
        zona = evento.datos["zona"]
        gravedad = evento.datos.get("gravedad_forzada")
        if gravedad is None:
            gravedad = des.asignar_gravedad(rng, zonas_por_nombre[zona])
        destino = des.elegir_instalacion(recursos, zona, gravedad, params)   # R2
        pid = next(contador_id)
        paciente = des.Paciente(id=pid, zona=zona, gravedad=gravedad,
                                instalacion=destino, t_llegada=t)
        pacientes[pid] = paciente
        stats["generados"] += 1
        # R6: la ventana de supervivencia corre desde la lesión, así que el
        # traslado también consume tiempo vital.
        media_tol = params.t_tolerancia_cola_h[gravedad]
        paciente.id_evento_muerte_cola = cal.agendar(
            t + rng.exponential(media_tol), "MUERTE_EN_COLA", paciente_id=pid)
        cal.agendar(t + params.tau(zona, params.zona_de(destino)), "ARRIBO", paciente_id=pid)

    def procesar_arribo(t: float, evento: des.Evento) -> None:
        paciente = pacientes[evento.datos["paciente_id"]]
        if paciente.estado != "en_traslado":
            return                                   # murió durante el traslado
        paciente.t_arribo = t
        paciente.estado = "en_cola"
        des.encolar(recursos, paciente)              # encolar antes de admitir respeta R4
        admitir_e_iniciar(t, paciente.instalacion)
        des.actualizar_modo_emergencia(recursos, params)
        actualizar_bloqueos(t)

    def procesar_fin_atencion(t: float, evento: des.Evento) -> None:
        paciente = pacientes[evento.datos["paciente_id"]]
        des.liberar_recursos(recursos, paciente.instalacion, paciente.gravedad, params)
        paciente.t_fin_atencion = t
        paciente.t_salida = t
        if rng.uniform() < params.p_mortalidad_clinica[paciente.gravedad]:
            paciente.estado = "muerte_clinica"       # S-5, inevitable
            stats["muertes_clinicas"] += 1
        else:
            paciente.estado = "atendido"
            stats["atendidos"] += 1
        admitir_e_iniciar(t, paciente.instalacion)
        des.actualizar_modo_emergencia(recursos, params)
        actualizar_bloqueos(t)

    def procesar_muerte_cola(t: float, evento: des.Evento) -> None:
        paciente = pacientes[evento.datos["paciente_id"]]
        if paciente.estado == "en_cola":
            recursos.instalaciones[paciente.instalacion].colas[paciente.gravedad].remove(paciente)
        elif paciente.estado != "en_traslado":
            return                                   # ya entró a atención
        paciente.estado = "muerte_evitable"
        paciente.t_salida = t
        stats["muertes_evitables"] += 1
        actualizar_bloqueos(t)

    def procesar_fin_bloque(t: float, evento: des.Evento) -> None:
        idx = evento.datos["bloque"] - 1
        for i, nombre in enumerate(nombres_inst):
            inst = recursos.instalaciones[nombre]
            for arr, cap, ocu in ((ocup_camas, inst.camas_capacidad, inst.camas_ocupadas),
                                  (ocup_uci, inst.uci_capacidad, inst.uci_ocupadas),
                                  (ocup_quirofanos, inst.quirofanos_capacidad,
                                   inst.quirofanos_ocupados)):
                arr[i, idx] = ocu / cap if cap else 0.0

    def procesar_tick_sd(t: float, evento: des.Evento) -> None:
        nonlocal estado_sd
        buffer = dict(recursos.consumo_pendiente)
        for k in recursos.consumo_pendiente:
            recursos.consumo_pendiente[k] = 0.0
        cargas = {n: dict(recursos.instalaciones[n].en_atencion) for n in nombres_inst}
        nivel_previo = dict(recursos.nivel_insumos)
        estado_sd = sd.paso(estado_sd, buffer, cargas, params, dt,
                            metodo=params.metodo_integracion)
        recursos.nivel_insumos = dict(estado_sd.stocks)     # el SD es la fuente de verdad
        for nombre in nombres_inst:
            factor_fatiga[nombre] = sd.factor_fatiga(estado_sd.energia[nombre],
                                                     params.fatiga_factor_max)
        muestrear(t)
        # Solo si algún nivel subió (reabastecimiento) vale la pena reintentar.
        if any(recursos.nivel_insumos[k] > nivel_previo[k] for k in nivel_previo):
            for nombre in nombres_inst:
                admitir_e_iniciar(t, nombre)
        actualizar_bloqueos(t)

    def procesar_aplicar_intervencion(t: float, evento: des.Evento) -> None:
        assert intervencion is not None
        for nombre, extra in intervencion.camas_extra.items():
            if nombre in recursos.instalaciones:
                recursos.instalaciones[nombre].camas_capacidad += extra
        for nombre, extra in intervencion.uci_extra.items():
            if nombre in recursos.instalaciones:
                recursos.instalaciones[nombre].uci_capacidad += extra
        for nombre, extra in intervencion.quirofanos_extra.items():
            if nombre in recursos.instalaciones:
                recursos.instalaciones[nombre].quirofanos_capacidad += extra
        if intervencion.medicos_extra:
            operativas = [i for i in params.instalaciones if i.operativa]
            total = params.medicos_efectivos() + intervencion.medicos_extra
            for nombre, n in P.reparto_personal(operativas, total).items():
                recursos.instalaciones[nombre].medicos_asignados = n
        # Más capacidad puede volver candidata a una instalación que estaba excluida.
        recursos.orden_derivacion = P.orden_derivacion(
            [P.Instalacion(nombre=i.nombre, zona=i.zona, camas=i.camas_capacidad,
                           uci=i.uci_capacidad, quirofanos=i.quirofanos_capacidad,
                           operativa=True)
             for i in recursos.instalaciones.values()],
            params.dist_zonas_km)
        for nombre in nombres_inst:
            admitir_e_iniciar(t, nombre)
        des.actualizar_modo_emergencia(recursos, params)
        actualizar_bloqueos(t)

    DESPACHO = {
        "LLEGADA": procesar_llegada,
        "ARRIBO": procesar_arribo,
        "FIN_ATENCION": procesar_fin_atencion,
        "MUERTE_EN_COLA": procesar_muerte_cola,
        "FIN_BLOQUE": procesar_fin_bloque,
        "APLICAR_INTERVENCION": procesar_aplicar_intervencion,
        "TICK_SD": procesar_tick_sd,
    }

    # --- 1. llegadas base por zona (R1, thinning de Lewis-Shedler) -------------
    for zona_heridos in params.heridos_t0:
        for t in des.llegadas_thinning(rng, params, zona_heridos.zona,
                                       zona_heridos.total_atencion):
            cal.agendar(t, "LLEGADA", zona=zona_heridos.zona)

    # --- 2. llegadas adicionales del intercambio con el Grupo 1 ----------------
    if llegadas_extra:
        for lote in llegadas_extra:
            for gravedad, n in (("leve", lote.leve), ("moderado", lote.moderado),
                                ("grave", lote.grave)):
                for _ in range(n):
                    cal.agendar(lote.t, "LLEGADA", zona=lote.zona, gravedad_forzada=gravedad)

    # --- 3. cortes de bloque y pasos del sustrato SD ---------------------------
    for b in range(1, N_BLOQUES + 1):
        cal.agendar(b * BLOQUE_H, "FIN_BLOQUE", bloque=b)
    for k in range(1, int(round(HORIZONTE_H / dt)) + 1):
        cal.agendar(k * dt, "TICK_SD")

    # --- 4. intervención: entra en vigor al inicio de `desde_bloque` -----------
    if intervencion is not None:
        cal.agendar((intervencion.desde_bloque - 1) * BLOQUE_H, "APLICAR_INTERVENCION")

    # --- 5. bucle principal ----------------------------------------------------
    muestrear(0.0)
    while len(cal):
        evento = cal.pop()
        if evento is None:
            break
        if evento.tiempo > HORIZONTE_H:
            break  # heap ordenado por tiempo: todo lo que queda es posterior
        DESPACHO[evento.tipo](evento.tiempo, evento)

    recursos.cerrar_bloqueos(HORIZONTE_H)

    en_sistema = sum(1 for p in pacientes.values() if p.estado in P.ESTADOS_EN_SISTEMA)
    assert (stats["atendidos"] + stats["muertes_evitables"] + stats["muertes_clinicas"]
            + en_sistema == stats["generados"]), "no se conserva el total de pacientes"

    bloqueo_horas = np.zeros((len(nombres_inst), len(P.RECURSOS_BLOQUEO)))
    for i, nombre in enumerate(nombres_inst):
        for k, recurso in enumerate(P.RECURSOS_BLOQUEO):
            bloqueo_horas[i, k] = recursos.bloqueo_horas.get((nombre, recurso), 0.0)

    return ResultadoCorrida(
        replica=replica,
        instalaciones=nombres_inst,
        n_bloques=N_BLOQUES,
        recursos=list(P.RECURSOS_BLOQUEO),
        ocup_camas=ocup_camas,
        ocup_uci=ocup_uci,
        ocup_quirofanos=ocup_quirofanos,
        bloqueo_horas=bloqueo_horas,
        muertes_evitables=stats["muertes_evitables"],
        muertes_clinicas=stats["muertes_clinicas"],
        atendidos=stats["atendidos"],
        en_sistema=en_sistema,
        generados=stats["generados"],
        patient_log=_patient_log(pacientes, replica),
        state_log=pd.DataFrame(filas_state, columns=list(P.COLS_STATE_LOG)),
        stock_log=pd.DataFrame(filas_stock, columns=list(P.COLS_STOCK_LOG)),
    )


def _patient_log(pacientes: dict[int, des.Paciente], replica: int) -> pd.DataFrame:
    """
    Un renglón por paciente generado. Los censurados a las 72 h llevan NaN en los
    tiempos que nunca ocurrieron; `espera` incluye el traslado, porque mide desde
    que el herido entra al sistema hasta que recibe atención.
    """
    filas = []
    for p in pacientes.values():
        if p.estado in ("atendido", "muerte_clinica"):
            t_inicio, t_fin = p.t_inicio_atencion, p.t_fin_atencion
            espera = t_inicio - p.t_llegada
        elif p.estado == "muerte_evitable":
            t_inicio, t_fin = np.nan, p.t_salida
            espera = p.t_salida - p.t_llegada
        elif p.estado == "en_atencion":
            t_inicio, t_fin = p.t_inicio_atencion, np.nan
            espera = t_inicio - p.t_llegada
        else:                                   # en_traslado o en_cola al cortar
            t_inicio, t_fin = np.nan, np.nan
            espera = HORIZONTE_H - p.t_llegada
        filas.append((replica, p.id, p.zona, p.gravedad, p.t_llegada, t_inicio,
                      t_fin, p.instalacion, p.estado, espera))
    return pd.DataFrame(filas, columns=list(P.COLS_PATIENT_LOG))
