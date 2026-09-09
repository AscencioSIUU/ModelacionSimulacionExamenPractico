"""
T1 (P2) — Bucle de simulación y acoplamiento DES <-> SD.

`correr()` arma el `des.Calendario`, agenda todas las llegadas (base +
intercambio), los cortes de bloque (`FIN_BLOQUE`, cada 6 h) y los pasos del
sustrato SD (`TICK_SD`, cada `DT_SD` h), y despacha eventos hasta agotar el
horizonte de 72 h.

Contrato esperado de `sd.py` (P3) — éste es el módulo que aún no existe;
`simulacion.py` se programa contra esta firma para no bloquear a nadie
("nadie espera a nadie"). Mientras `sd.py` no exista (o no exponga estos
nombres), el acoplamiento SD->DES se degrada de forma segura a neutro
(`factor_fatiga = 1.0`, sin bloqueo por escasez) y P2 sigue siendo testeable
de forma aislada:

    sd.estado_inicial(params, sangre_extra: float = 0.0) -> EstadoSD
    sd.paso(estado: EstadoSD, cargas: dict[str, dict[str, int]],
            params: ParamsSistema, dt: float, metodo: str = "rk4") -> EstadoSD
    sd.factor_fatiga(energia: float) -> float
    EstadoSD.stocks: dict[str, float]     # nombre_suministro -> stock agregado
    EstadoSD.energia: dict[str, float]    # nombre_instalacion -> energía en [0,1]

`cargas` (DES->SD) es `{instalacion: {gravedad: n_pacientes_en_atencion}}`.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

from . import des
from .intercambio import LlegadaExtra
from .params import BLOQUE_H, HORIZONTE_H, N_BLOQUES, ParamsSistema
from .params import Intervencion

try:
    from . import sd as _sd  # type: ignore  # noqa: N812  (aún no existe — ver docstring)
except ImportError:
    _sd = None

DT_SD = 0.1  # h — paso del sustrato SD (TICK_SD)

RECURSOS_BLOQUEO = ["cama_general", "cama_uci", "medico", "sangre"]


@dataclass
class ResultadoCorrida:
    """Salida de UNA corrida. `montecarlo.py` (P4) apila N de éstas en `outputs.ResultadoMC`."""
    instalaciones: list[str]
    n_bloques: int
    recursos: list[str]
    ocup_camas: np.ndarray        # [i, b]  fracción 0..1
    ocup_uci: np.ndarray          # [i, b]  fracción 0..1
    bloqueo_horas: np.ndarray     # [i, k]  horas bloqueadas, k = RECURSOS_BLOQUEO
    muertes_evitables: int
    muertes_clinicas: int
    atendidos: int
    en_sistema: int
    generados: int


def correr(
    params: ParamsSistema,
    seed: int,
    intervencion: Intervencion | None = None,
    llegadas_extra: list[LlegadaExtra] | None = None,
) -> ResultadoCorrida:
    rng = np.random.default_rng(seed)
    cal = des.Calendario()

    instalaciones_op = [i for i in params.instalaciones if i.operativa]
    nombres_inst = [i.nombre for i in instalaciones_op]
    zonas_por_nombre = {z.zona: z for z in params.heridos_t0}

    medicos_extra = intervencion.medicos_extra if intervencion else 0
    medicos_base = round(params.medicos * params.presentismo) + medicos_extra
    derivar_leves = bool(intervencion and intervencion.derivar_leves_aparte)
    recursos = des.crear_recursos(
        instalaciones_op, medicos_base, params.pac_por_medico_normal,
        derivar_leves=derivar_leves,
    )
    des.actualizar_modo_emergencia(recursos, params)

    sangre_extra = intervencion.sangre_extra if intervencion else 0.0
    estado_sd = _sd.estado_inicial(params, sangre_extra=sangre_extra) if _sd is not None else None
    factor_fatiga: dict[str, float] = {n: 1.0 for n in nombres_inst}

    pacientes: dict[int, des.Paciente] = {}
    contador_id = itertools.count()
    stats = {"muertes_evitables": 0, "muertes_clinicas": 0, "atendidos": 0, "generados": 0}
    ocup_camas = np.zeros((len(nombres_inst), N_BLOQUES))
    ocup_uci = np.zeros((len(nombres_inst), N_BLOQUES))

    # --- helpers internos (closures sobre el estado de esta corrida) -----------
    def elegir_instalacion(zona: str) -> str:
        fila = params.ruteo[zona]
        nombres = list(fila.keys())
        pesos = list(fila.values())
        return nombres[rng.choice(len(nombres), p=pesos)]

    def iniciar_atencion(t: float, paciente: des.Paciente) -> None:
        paciente.t_inicio_atencion = t
        paciente.estado = "en_atencion"
        ff = factor_fatiga.get(paciente.instalacion, 1.0)
        dt_serv = des.tiempo_servicio(rng, params, paciente.gravedad, factor_fatiga=ff)
        cal.agendar(t + dt_serv, "FIN_ATENCION", paciente_id=paciente.id)

    def admitir_e_iniciar(t: float, instalacion: str) -> None:
        for paciente in des.admitir_pendientes(recursos, instalacion):
            cal.cancelar(paciente.id_evento_muerte_cola)
            iniciar_atencion(t, paciente)

    def actualizar_bloqueos(t: float) -> None:
        for nombre, inst in recursos.instalaciones.items():
            cola_grave = inst.colas["grave"]
            cola_mod = inst.colas["moderado"]
            recursos.marcar_bloqueo(
                t, nombre, "cama_uci",
                bool(cola_grave) and inst.uci_ocupadas >= inst.uci_capacidad)
            recursos.marcar_bloqueo(
                t, nombre, "cama_general",
                bool(cola_mod) and inst.camas_ocupadas >= inst.camas_capacidad)
            cola_total = len(cola_grave) + len(cola_mod) + len(inst.colas["leve"])
            recursos.marcar_bloqueo(
                t, nombre, "medico",
                cola_total > 0 and recursos.medicos_ocupados >= recursos.medicos_capacidad)
            recursos.marcar_bloqueo(
                t, nombre, "sangre",
                bool(cola_grave) and recursos.sangre_bloqueada)

    def procesar_llegada(t: float, evento: des.Evento) -> None:
        zona = evento.datos["zona"]
        gravedad = evento.datos.get("gravedad_forzada")
        if gravedad is None:
            gravedad = des.asignar_gravedad(rng, zonas_por_nombre[zona])
        instalacion = elegir_instalacion(zona)
        pid = next(contador_id)
        paciente = des.Paciente(id=pid, zona=zona, gravedad=gravedad,
                                 instalacion=instalacion, t_llegada=t)
        pacientes[pid] = paciente
        stats["generados"] += 1

        if des.puede_atender(recursos, instalacion, gravedad):
            des.tomar_recursos(recursos, instalacion, gravedad)
            iniciar_atencion(t, paciente)
        else:
            des.encolar(recursos, paciente)
            media_tol = params.t_tolerancia_cola_h[gravedad]
            paciente.id_evento_muerte_cola = cal.agendar(
                t + rng.exponential(media_tol), "MUERTE_EN_COLA", paciente_id=pid)
        des.actualizar_modo_emergencia(recursos, params)
        actualizar_bloqueos(t)

    def procesar_fin_atencion(t: float, evento: des.Evento) -> None:
        paciente = pacientes[evento.datos["paciente_id"]]
        des.liberar_recursos(recursos, paciente.instalacion, paciente.gravedad)
        paciente.t_fin_atencion = t
        if rng.uniform() < params.p_mortalidad_clinica[paciente.gravedad]:
            paciente.estado = "muerte_clinica"
            stats["muertes_clinicas"] += 1
        else:
            paciente.estado = "atendido"
            stats["atendidos"] += 1
        admitir_e_iniciar(t, paciente.instalacion)
        des.actualizar_modo_emergencia(recursos, params)
        actualizar_bloqueos(t)

    def procesar_muerte_cola(t: float, evento: des.Evento) -> None:
        paciente = pacientes[evento.datos["paciente_id"]]
        if paciente.estado != "en_cola":
            return  # ya fue admitido antes de que este evento se disparara
        recursos.instalaciones[paciente.instalacion].colas[paciente.gravedad].remove(paciente)
        paciente.estado = "muerte_evitable"
        stats["muertes_evitables"] += 1
        actualizar_bloqueos(t)

    def procesar_fin_bloque(t: float, evento: des.Evento) -> None:
        idx = evento.datos["bloque"] - 1
        for i, nombre in enumerate(nombres_inst):
            inst = recursos.instalaciones[nombre]
            ocup_camas[i, idx] = inst.camas_ocupadas / inst.camas_capacidad if inst.camas_capacidad else 0.0
            ocup_uci[i, idx] = inst.uci_ocupadas / inst.uci_capacidad if inst.uci_capacidad else 0.0

    def procesar_aplicar_intervencion(t: float, evento: des.Evento) -> None:
        assert intervencion is not None
        for nombre, extra in intervencion.camas_extra.items():
            if nombre in recursos.instalaciones:
                recursos.instalaciones[nombre].camas_capacidad += extra
        for nombre, extra in intervencion.uci_extra.items():
            if nombre in recursos.instalaciones:
                recursos.instalaciones[nombre].uci_capacidad += extra
        for nombre in nombres_inst:
            admitir_e_iniciar(t, nombre)
        des.actualizar_modo_emergencia(recursos, params)
        actualizar_bloqueos(t)

    def procesar_tick_sd(t: float, evento: des.Evento) -> None:
        nonlocal estado_sd
        if _sd is None or estado_sd is None:
            return
        cargas = {nombre: dict(recursos.instalaciones[nombre].en_atencion) for nombre in nombres_inst}
        estado_sd = _sd.paso(estado_sd, cargas, params, DT_SD, metodo="rk4")
        for nombre in nombres_inst:
            factor_fatiga[nombre] = _sd.factor_fatiga(estado_sd.energia[nombre])
        bloqueada_antes = recursos.sangre_bloqueada
        recursos.sangre_bloqueada = estado_sd.stocks.get("sangre", 1.0) <= 0.0
        if bloqueada_antes and not recursos.sangre_bloqueada:
            for nombre in nombres_inst:
                admitir_e_iniciar(t, nombre)
        actualizar_bloqueos(t)

    DESPACHO = {
        "LLEGADA": procesar_llegada,
        "FIN_ATENCION": procesar_fin_atencion,
        "MUERTE_EN_COLA": procesar_muerte_cola,
        "FIN_BLOQUE": procesar_fin_bloque,
        "APLICAR_INTERVENCION": procesar_aplicar_intervencion,
        "TICK_SD": procesar_tick_sd,
    }

    # --- 1. agendar llegadas base por zona (thinning de Lewis-Shedler) ---------
    for zona_heridos in params.heridos_t0:
        for t in des.llegadas_thinning(rng, params, zona_heridos.zona, zona_heridos.total_atencion):
            cal.agendar(t, "LLEGADA", zona=zona_heridos.zona)

    # --- 2. llegadas adicionales del intercambio con Grupo 1 --------------------
    if llegadas_extra:
        for lote in llegadas_extra:
            for gravedad, n in (("leve", lote.leve), ("moderado", lote.moderado), ("grave", lote.grave)):
                for _ in range(n):
                    cal.agendar(lote.t, "LLEGADA", zona=lote.zona, gravedad_forzada=gravedad)

    # --- 3. cortes de bloque (cada 6 h) y pasos SD (cada DT_SD h) ---------------
    for b in range(1, N_BLOQUES + 1):
        cal.agendar(b * BLOQUE_H, "FIN_BLOQUE", bloque=b)
    n_ticks = int(round(HORIZONTE_H / DT_SD))
    for k in range(1, n_ticks + 1):
        cal.agendar(k * DT_SD, "TICK_SD")

    # --- 4. intervención: camas/UCI extra entran en vigor desde `desde_bloque` --
    # (medicos_extra y sangre_extra ya se aplicaron al armar recursos/estado_sd)
    if intervencion is not None:
        t_activa = (intervencion.desde_bloque - 1) * BLOQUE_H
        cal.agendar(t_activa, "APLICAR_INTERVENCION")

    # --- 5. bucle principal: despachar hasta agotar el calendario o el horizonte
    while len(cal):
        evento = cal.pop()
        if evento is None:
            break
        if evento.tiempo > HORIZONTE_H:
            break  # heap ordenado por tiempo: todo lo que queda es >= éste
        DESPACHO[evento.tipo](evento.tiempo, evento)

    recursos.cerrar_bloqueos(HORIZONTE_H)

    en_sistema = sum(1 for p in pacientes.values() if p.estado in ("en_cola", "en_atencion"))
    assert (stats["atendidos"] + stats["muertes_evitables"] + stats["muertes_clinicas"] + en_sistema
            == stats["generados"])

    bloqueo_horas = np.zeros((len(nombres_inst), len(RECURSOS_BLOQUEO)))
    for i, nombre in enumerate(nombres_inst):
        for k, recurso in enumerate(RECURSOS_BLOQUEO):
            bloqueo_horas[i, k] = recursos.bloqueo_horas.get((nombre, recurso), 0.0)

    return ResultadoCorrida(
        instalaciones=nombres_inst,
        n_bloques=N_BLOQUES,
        recursos=list(RECURSOS_BLOQUEO),
        ocup_camas=ocup_camas,
        ocup_uci=ocup_uci,
        bloqueo_horas=bloqueo_horas,
        muertes_evitables=stats["muertes_evitables"],
        muertes_clinicas=stats["muertes_clinicas"],
        atendidos=stats["atendidos"],
        en_sistema=en_sistema,
        generados=stats["generados"],
    )
