import argparse
from datetime import date
from pathlib import Path

from rgridCAPEXSimple import (
    criar_modelo,
    resolver_modelo,
    extrair_resultados,
    salvar_resultados_json,
    gerar_relatorio,
)


def dados_base():
    return {
        "delta_t": 1,
        "operational_days_equivalent": 365,
        "tariff_ev": 1.60,
        "capex_pv_kw": 1200,
        "capex_bess_kwh": 700,
        "capex_trafo_kw": 1000,
        "eta_charge": 0.91,
        "eta_discharge": 0.91,
        "soc_min_frac": 0.05,
        "soc_max_frac": 0.95,
        "soc_initial_frac": 0.50,
        "c_rate_charge": 1.00,
        "c_rate_discharge": 1.00,
        "E_bess_cap_max": 2000,
        "P_pv_cap_max": 1000,
        "P_trafo_cap_max": 500,
        "irradiance_cf": {
            1: 0.00, 2: 0.00, 3: 0.00, 4: 0.00, 5: 0.10, 6: 0.30, 7: 0.50, 8: 0.70, 9: 0.90, 10: 1.00,
            11: 0.95, 12: 0.90, 13: 0.85, 14: 0.80, 15: 0.70, 16: 0.50, 17: 0.30, 18: 0.10, 19: 0.00, 20: 0.00,
            21: 0.00, 22: 0.00, 23: 0.00, 24: 0.00,
        },
        "grid_price": {
            1: 0.25, 2: 0.25, 3: 0.25, 4: 0.25, 5: 0.25, 6: 0.25, 7: 0.54, 8: 0.88, 9: 0.88, 10: 0.88,
            11: 0.88, 12: 0.54, 13: 0.54, 14: 0.54, 15: 0.54, 16: 0.54, 17: 0.88, 18: 1.10, 19: 1.10, 20: 1.10,
            21: 0.88, 22: 0.54, 23: 0.25, 24: 0.25,
        },
        "P_ev_load_kw": {
            1: 35, 2: 28, 3: 22, 4: 20, 5: 25, 6: 48, 7: 72, 8: 98, 9: 105, 10: 115,
            11: 110, 12: 100, 13: 95, 14: 90, 15: 98, 16: 112, 17: 126, 18: 135, 19: 128, 20: 116,
            21: 94, 22: 72, 23: 54, 24: 42,
        },
    }


def ensure_unique_dir(base_dir: Path) -> Path:
    if not base_dir.exists():
        return base_dir
    idx = 1
    while True:
        candidate = Path(f"{base_dir}_{idx}")
        if not candidate.exists():
            return candidate
        idx += 1


def sanitize_label(label: str) -> str:
    cleaned = []
    for ch in label.lower():
        if ch.isalnum():
            cleaned.append(ch)
        elif ch in {" ", "-", "_"}:
            cleaned.append("_")
    slug = "".join(cleaned)
    slug = "_".join([part for part in slug.split("_") if part])
    return slug or "relatorio"


def annual_operational_profit(resultados: dict) -> float:
    params = resultados["params"]
    series = resultados["series"]
    delta_t = params["delta_t"]
    days = params["operational_days_equivalent"]

    daily_revenue = params["tariff_ev"] * sum(series["P_ev_load_kw"]) * delta_t
    daily_cost = sum(
        series["grid_price"][i] * series["P_grid_import"][i] * delta_t
        for i in range(len(series["P_grid_import"]))
    )
    return days * (daily_revenue - daily_cost)


def run_cenario(nome: str, label: str, dados: dict, solver: str, root_dir: Path) -> dict:
    dir_saida = ensure_unique_dir(root_dir / nome)
    dir_saida.mkdir(parents=True, exist_ok=True)

    modelo = criar_modelo(dados)
    resolver_modelo(modelo, solver_name=solver)

    resultados = extrair_resultados(modelo)
    salvar_resultados_json(resultados, dir_saida / "resultados.json")
    report_slug = sanitize_label(label)
    report_path = dir_saida / f"relatorio_{report_slug}.txt"
    gerar_relatorio(modelo, report_path, resultados=resultados)
    return {
        "dir_saida": dir_saida,
        "report_path": report_path,
        "label": label,
        "annual_profit": annual_operational_profit(resultados),
    }


def main():
    parser = argparse.ArgumentParser(description="Roda 3 cenarios automaticamente.")
    parser.add_argument("--solver", default="gurobi", help="Solver Pyomo (padrao: gurobi).")
    args = parser.parse_args()

    base = dados_base()
    stamp = date.today().isoformat()
    root_dir = Path(__file__).parent / f"cenarios_{stamp}"
    root_dir.mkdir(parents=True, exist_ok=True)

    cenarios = []

    # Cenario 1: 100% rede, trafo max 500 kW, sem PV e BESS
    d1 = dict(base)
    d1["P_trafo_cap_max"] = 500
    d1["P_pv_cap_max"] = 0
    d1["E_bess_cap_max"] = 0
    cenarios.append(("cenario_1_rede_pura", "100_rede_trafo_500kw", d1))

    # Cenario 2: trafo max 75 kW, PV e BESS complementares
    d2 = dict(base)
    d2["P_trafo_cap_max"] = 75
    cenarios.append(("cenario_2_trafo_75_pv_bess", "trafo_75kw_pv_bess", d2))

    # Cenario 3: BESS zerado, PV e rede complementares
    d3 = dict(base)
    d3["E_bess_cap_max"] = 0
    cenarios.append(("cenario_3_sem_bess", "sem_bess_pv_rede", d3))

    resumo = []
    for nome, label, dados in cenarios:
        info = run_cenario(nome, label, dados, args.solver, root_dir)
        resumo.append(info)
        print(f"Concluido: {info['dir_saida']}")

    comparativo_path = root_dir / "comparativo_cenarios.txt"
    with open(comparativo_path, "w", encoding="utf-8") as f:
        f.write("COMPARATIVO DE CENARIOS - LUCRO OPERACIONAL ANUAL\n")
        f.write("=" * 60 + "\n\n")
        f.write("Configuracao | Lucro operacional anual (R$) | Relatorio\n")
        f.write("-" * 60 + "\n")
        for item in resumo:
            lucro = f"{item['annual_profit']:,.2f}"
            rel_path = item["report_path"].name
            f.write(f"{item['label']} | {lucro} | {rel_path}\n")
    print(f"Comparativo salvo em: {comparativo_path}")


if __name__ == "__main__":
    main()
