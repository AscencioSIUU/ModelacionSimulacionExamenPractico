"""
Suite de asserts planos (sin pytest).  Correr:  uv run python tests/test_modelo.py

Cada persona agrega su bloque `def _p<N>_...()` y lo llama desde `main()`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
FIXTURES = Path(__file__).resolve().parent / "fixtures"

from hospital import intercambio, outputs, params  # noqa: E402


# --------------------------------------------------------------------------- P1
def _p1_params():
    p = params.cargar()
    assert len(p.instalaciones) == 5
    assert sum(i.operativa for i in p.instalaciones) == 4        # Z3 cerrada
    assert sum(z.total_atencion for z in p.heridos_t0) == 11773
    for z, fila in p.ruteo.items():
        assert abs(sum(fila.values()) - 1.0) < 1e-9, z
    # tasas normalizadas a base horaria: consumo/hora = consumo/día / 24
    s = next(x for x in p.suministros if x.nombre == "sangre")
    assert abs(s.consumo_hora_base - 120 / 24) < 1e-9


def _p1_intercambio_csv_vacio():
    vacio = FIXTURES / "_vacio.csv"
    vacio.write_text("")
    try:
        assert intercambio.cargar_demanda_grupo1(vacio) is None
        assert intercambio.cargar_demanda_grupo1(FIXTURES / "no_existe.csv") is None
    finally:
        vacio.unlink()


def _p1_intercambio_fixture():
    df = intercambio.cargar_demanda_grupo1(FIXTURES / "grupo1_demo.csv")
    assert df is not None and len(df) == 3

    p = params.cargar()
    lotes = intercambio.aplicar_demanda(p, df)
    assert len(lotes) == 3
    assert [l.t for l in lotes] == [6.0, 12.0, 18.0]             # bloque b -> hora (b-1)*6
    assert lotes[0].zona == "Z1" and lotes[0].total == 265

    imp = intercambio.resumen_impacto(p, df)
    z5 = imp.loc[imp["zona"] == "Z5"].iloc[0]
    assert z5["grupo1_total"] == 320
    assert z5["nuevo_total"] == 4570 + 320


def _fake_res_mc(n_rep=30, seed=0) -> outputs.ResultadoMC:
    rng = np.random.default_rng(seed)
    inst = [i.nombre for i in params.cargar().instalaciones]
    recursos = ["cama_general", "cama_uci", "medico", "sangre"]
    ni, nb, nk = len(inst), params.N_BLOQUES, len(recursos)
    return outputs.ResultadoMC(
        n_replicas=n_rep, instalaciones=inst, n_bloques=nb, recursos=recursos,
        ocup_camas=np.clip(rng.normal(0.8, 0.15, (n_rep, ni, nb)), 0, 1.2),
        ocup_uci=np.clip(rng.normal(0.9, 0.1, (n_rep, ni, nb)), 0, 1.2),
        bloqueo_horas=rng.gamma(2.0, 5.0, (n_rep, ni, nk)),
        muertes_evitables=rng.integers(200, 900, n_rep).astype(float),
        muertes_clinicas=rng.integers(100, 400, n_rep).astype(float),
        generados=np.full(n_rep, 11773.0),
    )


def _p1_outputs_tablas():
    import tempfile
    outputs.DIR_TABLAS = Path(tempfile.mkdtemp())        # no ensuciar outputs/tables/ real

    res = _fake_res_mc()
    sat = outputs.tabla_saturacion(res)
    assert len(sat) == 5 * 2 * params.N_BLOQUES                  # inst × tipo × bloque
    assert (outputs.DIR_TABLAS / "saturacion_por_bloque.csv").exists()
    assert sat["ocupacion_media_pct"].between(0, 120).all()

    cb = outputs.tabla_cuellos_botella(res)
    assert cb["critico"].sum() == 2
    assert cb["rank"].tolist() == list(range(1, len(cb) + 1))
    assert cb["horas_bloqueadas_media"].is_monotonic_decreasing

    # barrido: run_fn falso que baja la tasa cuanto mayor sea la intervención
    def run_fn(interv):
        extra = 0 if interv is None else (
            sum(interv.camas_extra.values()) + sum(interv.uci_extra.values())
            + interv.medicos_extra * 5 + interv.sangre_extra * 0.5)
        r = _fake_res_mc(seed=1)
        r.muertes_evitables = np.maximum(r.muertes_evitables - extra * 3, 0.0)
        return r

    rm = outputs.tabla_recursos_minimos(run_fn, params.cargar(), max_iter=6)
    assert rm.iloc[0]["recurso"] == "— base —"
    assert rm["alcanza_umbral"].any()


# --------------------------------------------------------------------------- main
def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("_p") and callable(v)]
    fallos = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except AssertionError as e:
            fallos += 1
            print(f"FALLO  {t.__name__}: {e}")
    print(f"\n{len(tests) - fallos}/{len(tests)} pasaron")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
