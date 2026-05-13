# redePeakSimplePy.py

import argparse
import json
from pathlib import Path
import matplotlib.pyplot as plt
from pyomo.environ import (
    ConcreteModel,
    Binary,
    Constraint,
    NonNegativeReals,
    Objective,
    Param,
    RangeSet,
    SolverFactory,
    Var,
    maximize,
    value,
)
from pyomo.opt import SolverStatus, TerminationCondition

# ==============================================================================
# MODELO CONCRETO SIMPLIFICADO PARA MICRORREDE DE ELETROPOSTO
# Objetivo: Maximizar (Lucro Operacional Anual - CAPEX Inicial)
# Premissas: Exportação para a rede proibida (operação "grid-parallel" apenas importa)
# ==============================================================================

DEFAULT_VERSION = "v1_2026-05-12"
DEFAULT_GRAFICO_VERSION = "v1"

COLOR = {
    "pv": "tab:blue",
    "grid": "tab:orange",
    "ev": "tab:green",
    "bess_charge": "tab:purple",
    "bess_discharge": "tab:red",
    "soc": "tab:cyan",
    "price": "tab:olive",
    "limit": "tab:gray",
    "residual": "tab:black",
}
LEGEND_KW = {"loc": "best", "frameon": False}

def criar_modelo(dados):
    """
    Cria e popula um modelo concreto do Pyomo com os dados fornecidos.
    
    Args:
        dados (dict): Dicionário com todos os parâmetros e séries temporais.
        
    Returns:
        ConcreteModel: Modelo Pyomo pronto para ser resolvido.
    """
    model = ConcreteModel()
    
    # -------------------- CONJUNTOS --------------------
    model.T = RangeSet(1, 24) # 24 horas
    
    # -------------------- PARÂMETROS (Escalares) --------------------
    # Econômicos
    model.delta_t = Param(initialize=dados['delta_t'], within=NonNegativeReals)
    model.operational_days_equivalent = Param(initialize=dados['operational_days_equivalent'], within=NonNegativeReals)
    model.tariff_ev = Param(initialize=dados['tariff_ev'], within=NonNegativeReals)
    
    # CAPEX (Investimento)
    model.capex_pv_kw = Param(initialize=dados['capex_pv_kw'], within=NonNegativeReals)
    model.capex_bess_kwh = Param(initialize=dados['capex_bess_kwh'], within=NonNegativeReals)
    model.capex_trafo_kw = Param(initialize=dados['capex_trafo_kw'], within=NonNegativeReals)
    
    # Técnicos BESS
    model.eta_charge = Param(initialize=dados['eta_charge'], within=NonNegativeReals)
    model.eta_discharge = Param(initialize=dados['eta_discharge'], within=NonNegativeReals)
    model.soc_min_frac = Param(initialize=dados['soc_min_frac'], within=NonNegativeReals)
    model.soc_max_frac = Param(initialize=dados['soc_max_frac'], within=NonNegativeReals)
    model.soc_initial_frac = Param(initialize=dados['soc_initial_frac'], within=NonNegativeReals)
    model.c_rate_charge = Param(initialize=dados['c_rate_charge'], within=NonNegativeReals)
    model.c_rate_discharge = Param(initialize=dados['c_rate_discharge'], within=NonNegativeReals)
    
    # Limites de Capacidade
    model.E_bess_cap_max = Param(initialize=dados['E_bess_cap_max'], within=NonNegativeReals)
    model.P_pv_cap_max = Param(initialize=dados['P_pv_cap_max'], within=NonNegativeReals)
    model.P_trafo_cap_max = Param(initialize=dados['P_trafo_cap_max'], within=NonNegativeReals)

    # -------------------- PARÂMETROS (Séries Temporais) --------------------
    model.irradiance_cf = Param(model.T, initialize=dados['irradiance_cf'], within=NonNegativeReals)
    model.grid_price = Param(model.T, initialize=dados['grid_price'], within=NonNegativeReals)
    model.P_ev_load_kw = Param(model.T, initialize=dados['P_ev_load_kw'], within=NonNegativeReals)
    
    # -------------------- VARIÁVEIS DE DECISÃO --------------------
    # Investimento (Capacidades Instaladas)
    model.P_pv_cap = Var(within=NonNegativeReals, bounds=(0, model.P_pv_cap_max))
    model.E_bess_cap = Var(within=NonNegativeReals, bounds=(0, model.E_bess_cap_max))
    model.P_trafo_cap = Var(within=NonNegativeReals, bounds=(0, model.P_trafo_cap_max))
    
    # Operação (Despacho Horário)
    model.P_pv_gen = Var(model.T, within=NonNegativeReals)
    model.P_grid_import = Var(model.T, within=NonNegativeReals)
    model.P_bess_charge = Var(model.T, within=NonNegativeReals)
    model.P_bess_discharge = Var(model.T, within=NonNegativeReals)
    model.SOC = Var(model.T, within=NonNegativeReals)
    model.y_bess = Var(model.T, within=Binary)  # 1 = Carregando, 0 = Descarregando
    model.E_bess_cap_charge = Var(model.T, within=NonNegativeReals)
    model.E_bess_cap_discharge = Var(model.T, within=NonNegativeReals)
    
    # -------------------- RESTRIÇÕES --------------------
    
    # 1. Limite de Geração FV por fator de capacidade
    def pv_limit_rule(model, t):
        return model.P_pv_gen[t] <= model.P_pv_cap * model.irradiance_cf[t]
    model.PVLimit = Constraint(model.T, rule=pv_limit_rule)
    
    # 2. Limite do Transformador (apenas importação)
    def trafo_limit_rule(model, t):
        return model.P_grid_import[t] <= model.P_trafo_cap
    model.TrafoLimit = Constraint(model.T, rule=trafo_limit_rule)
    
    # 3. Limites de Potência do BESS (c-rate) e bloqueio por modo
    def bess_charge_cap_rule(model, t):
        return model.E_bess_cap_charge[t] <= model.E_bess_cap
    model.BESSChargeCap = Constraint(model.T, rule=bess_charge_cap_rule)

    def bess_charge_mode_rule(model, t):
        return model.E_bess_cap_charge[t] <= model.E_bess_cap_max * model.y_bess[t]
    model.BESSChargeMode = Constraint(model.T, rule=bess_charge_mode_rule)

    def bess_charge_limit_rule(model, t):
        return model.P_bess_charge[t] <= model.c_rate_charge * model.E_bess_cap_charge[t]
    model.BESSChargeLimit = Constraint(model.T, rule=bess_charge_limit_rule)

    def bess_discharge_cap_rule(model, t):
        return model.E_bess_cap_discharge[t] <= model.E_bess_cap
    model.BESSDischargeCap = Constraint(model.T, rule=bess_discharge_cap_rule)

    def bess_discharge_mode_rule(model, t):
        return model.E_bess_cap_discharge[t] <= model.E_bess_cap_max * (1 - model.y_bess[t])
    model.BESSDischargeMode = Constraint(model.T, rule=bess_discharge_mode_rule)

    def bess_discharge_limit_rule(model, t):
        return model.P_bess_discharge[t] <= model.c_rate_discharge * model.E_bess_cap_discharge[t]
    model.BESSDischargeLimit = Constraint(model.T, rule=bess_discharge_limit_rule)
    
    # 5. Limites de Estado de Carga (SOC)
    def soc_min_rule(model, t):
        return model.SOC[t] >= model.soc_min_frac * model.E_bess_cap
    model.SOCMin = Constraint(model.T, rule=soc_min_rule)
    
    def soc_max_rule(model, t):
        return model.SOC[t] <= model.soc_max_frac * model.E_bess_cap
    model.SOCMax = Constraint(model.T, rule=soc_max_rule)
    
    # 6. Balanço de Energia do SOC (dinâmica da bateria)
    def soc_balance_rule(model, t):
        if t == model.T.first():
            return model.SOC[t] == model.soc_initial_frac * model.E_bess_cap + model.delta_t * (
                model.eta_charge * model.P_bess_charge[t] - model.P_bess_discharge[t] / model.eta_discharge
            )
        t_prev = model.T.prev(t)
        return model.SOC[t] == model.SOC[t_prev] + model.delta_t * (
            model.eta_charge * model.P_bess_charge[t] - model.P_bess_discharge[t] / model.eta_discharge
        )
    model.SOCBalance = Constraint(model.T, rule=soc_balance_rule)
    
    # 7. Condição de Ciclagem Completa (SOC final = SOC inicial)
    def terminal_soc_rule(model):
        return model.SOC[model.T.last()] == model.soc_initial_frac * model.E_bess_cap
    model.TerminalSOC = Constraint(rule=terminal_soc_rule)
    
    # 8. Balanço de Potência no Barramento (Conservação de Energia)
    #    Nota: P_grid_export é 0 por definição do cenário.
    def energy_balance_rule(model, t):
        return (
            model.P_pv_gen[t] + model.P_grid_import[t] + model.P_bess_discharge[t]
            == model.P_ev_load_kw[t] + model.P_bess_charge[t]
        )
    model.EnergyBalance = Constraint(model.T, rule=energy_balance_rule)
    
    # -------------------- FUNÇÃO OBJETIVO --------------------
    # Maximizar -> Lucro Operacional Anual - CAPEX Total
    # Lucro Operacional Anual = (Receita Recarga VE - Custo Importação) * 365 dias
    # CAPEX Total = Custo de instalação de PV, BESS e Transformador
    def objective_rule(model):
        # Receita diária com recarga de VE (fixa, pois a demanda é um parâmetro)
        daily_revenue_ev = sum(model.tariff_ev * model.P_ev_load_kw[t] * model.delta_t for t in model.T)
        # Custo diário com importação de energia da rede
        daily_cost_import = sum(model.grid_price[t] * model.P_grid_import[t] * model.delta_t for t in model.T)
        
        annual_operational_profit = model.operational_days_equivalent * (daily_revenue_ev - daily_cost_import)
        
        # Custo de investimento total (CAPEX)
        capex_total = (model.capex_pv_kw * model.P_pv_cap +
                       model.capex_bess_kwh * model.E_bess_cap +
                       model.capex_trafo_kw * model.P_trafo_cap)
        
        # Simplificado: Lucro anualizado - Investimento inicial
        return annual_operational_profit - capex_total
    
    model.Obj = Objective(rule=objective_rule, sense=maximize)
    
    return model

# ==============================================================================
# FUNÇÕES DE PÓS-PROCESSAMENTO E VISUALIZAÇÃO (SIMPLIFICADAS)
# ==============================================================================

def resolver_modelo(model, solver_name='gurobi'):
    """Resolve o modelo Pyomo usando o solver especificado."""
    solver = SolverFactory(solver_name)
    if not solver.available(False):
        raise RuntimeError(f"Solver '{solver_name}' não está disponível.")
    
    # Define um timeout (opcional, aqui 60 segundos)
    results = solver.solve(model, options={'TimeLimit': 60}, tee=False) 
    
    if results.solver.termination_condition not in [TerminationCondition.optimal, TerminationCondition.locallyOptimal]:
        raise RuntimeError(f"Otimização falhou. Condição de término: {results.solver.termination_condition}")
    
    print("Solução ótima encontrada!")
    return results

def extrair_resultados(model):
    """Extrai resultados do modelo em um dicionario serializavel."""
    T = list(model.T)
    resultados = {
        "T": T,
        "params": {
            "delta_t": float(value(model.delta_t)),
            "operational_days_equivalent": float(value(model.operational_days_equivalent)),
            "tariff_ev": float(value(model.tariff_ev)),
            "capex_pv_kw": float(value(model.capex_pv_kw)),
            "capex_bess_kwh": float(value(model.capex_bess_kwh)),
            "capex_trafo_kw": float(value(model.capex_trafo_kw)),
            "eta_charge": float(value(model.eta_charge)),
            "eta_discharge": float(value(model.eta_discharge)),
            "soc_min_frac": float(value(model.soc_min_frac)),
            "soc_max_frac": float(value(model.soc_max_frac)),
            "soc_initial_frac": float(value(model.soc_initial_frac)),
            "c_rate_charge": float(value(model.c_rate_charge)),
            "c_rate_discharge": float(value(model.c_rate_discharge)),
            "E_bess_cap_max": float(value(model.E_bess_cap_max)),
            "P_pv_cap_max": float(value(model.P_pv_cap_max)),
            "P_trafo_cap_max": float(value(model.P_trafo_cap_max)),
        },
        "caps": {
            "P_pv_cap": float(value(model.P_pv_cap)),
            "E_bess_cap": float(value(model.E_bess_cap)),
            "P_trafo_cap": float(value(model.P_trafo_cap)),
        },
        "series": {
            "irradiance_cf": [float(value(model.irradiance_cf[t])) for t in T],
            "grid_price": [float(value(model.grid_price[t])) for t in T],
            "P_ev_load_kw": [float(value(model.P_ev_load_kw[t])) for t in T],
            "P_pv_gen": [float(value(model.P_pv_gen[t])) for t in T],
            "P_grid_import": [float(value(model.P_grid_import[t])) for t in T],
            "P_bess_charge": [float(value(model.P_bess_charge[t])) for t in T],
            "P_bess_discharge": [float(value(model.P_bess_discharge[t])) for t in T],
            "y_bess": [int(round(value(model.y_bess[t]))) for t in T],
            "SOC": [float(value(model.SOC[t])) for t in T],
        },
        "objective": float(value(model.Obj)),
    }
    return resultados

def salvar_resultados_json(resultados, caminho_json):
    """Salva resultados em JSON para reuso posterior."""
    caminho_json.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho_json, 'w', encoding='utf-8') as f:
        json.dump(resultados, f, indent=2, sort_keys=True)

def carregar_resultados_json(caminho_json):
    """Carrega resultados salvos em JSON."""
    with open(caminho_json, 'r', encoding='utf-8') as f:
        return json.load(f)

def normalizar_resultados(obj, ndigits=4):
    """Normaliza resultados para comparação estável entre execuções."""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, list):
        return [normalizar_resultados(x, ndigits) for x in obj]
    if isinstance(obj, dict):
        return {k: normalizar_resultados(v, ndigits) for k, v in obj.items()}
    return obj

def render_relatorio(model, resultados=None):
    """Renderiza o relatório textual com os principais resultados."""
    if resultados is None:
        resultados = extrair_resultados(model)
    linhas = []
    linhas.append("=" * 60)
    linhas.append("RELATORIO DA OTIMIZAÇÃO - MICRORREDE SIMPLIFICADA")
    linhas.append("=" * 60)
    linhas.append("")

    linhas.append("1. CAPACIDADES ÓTIMAS INSTALADAS:")
    linhas.append(f"   - Geração Fotovoltaica (PV): {value(model.P_pv_cap):.2f} kW")
    linhas.append(f"   - Bateria (BESS):           {value(model.E_bess_cap):.2f} kWh")
    linhas.append(f"   - Transformador:            {value(model.P_trafo_cap):.2f} kW")
    linhas.append("")

    daily_revenue_ev = sum(value(model.tariff_ev) * value(model.P_ev_load_kw[t]) * value(model.delta_t) for t in model.T)
    daily_cost_import = sum(value(model.grid_price[t]) * value(model.P_grid_import[t]) * value(model.delta_t) for t in model.T)
    annual_profit = value(model.operational_days_equivalent) * (daily_revenue_ev - daily_cost_import)
    capex_total = (value(model.capex_pv_kw) * value(model.P_pv_cap) +
                   value(model.capex_bess_kwh) * value(model.E_bess_cap) +
                   value(model.capex_trafo_kw) * value(model.P_trafo_cap))

    linhas.append("2. RESULTADOS FINANCEIROS:")
    linhas.append(f"   - Receita Anual com Recarga de VE: R$ {daily_revenue_ev * value(model.operational_days_equivalent):,.2f}")
    linhas.append(f"   - Custo Anual com Importação de Energia: R$ {daily_cost_import * value(model.operational_days_equivalent):,.2f}")
    linhas.append(f"   - Lucro Operacional Anual: R$ {annual_profit:,.2f}")
    linhas.append(f"   - CAPEX Total (Investimento Inicial): R$ {capex_total:,.2f}")
    linhas.append(f"   - FUNÇÃO OBJETIVO (Lucro Anual - CAPEX): R$ {value(model.Obj):,.2f}")
    linhas.append("")

    linhas.append("3. DESPACHO HORÁRIO (kW / kWh):")
    linhas.append("Hr   PV Ger     RedeImp   BESS Car   BESS Desc   SOC (kWh)   Demanda EV")
    linhas.append("-" * 70)
    for t in model.T:
        linhas.append(
            f"{t:2d}   {value(model.P_pv_gen[t]):8.2f}   {value(model.P_grid_import[t]):8.2f}   "
            f"{value(model.P_bess_charge[t]):8.2f}   {value(model.P_bess_discharge[t]):8.2f}   "
            f"{value(model.SOC[t]):8.2f}   {value(model.P_ev_load_kw[t]):8.2f}"
        )

    # -------------------- CHECAGENS DE QUALIDADE --------------------
    params = resultados["params"]
    caps = resultados["caps"]
    series = resultados["series"]
    T = resultados["T"]
    tol = 1e-4

    pv_limit = [caps["P_pv_cap"] * cf for cf in series["irradiance_cf"]]
    grid_limit = [caps["P_trafo_cap"] for _ in T]
    bess_charge_limit = [params["c_rate_charge"] * caps["E_bess_cap"] for _ in T]
    bess_discharge_limit = [params["c_rate_discharge"] * caps["E_bess_cap"] for _ in T]

    balance_residual = [
        series["P_pv_gen"][i]
        + series["P_grid_import"][i]
        + series["P_bess_discharge"][i]
        - series["P_ev_load_kw"][i]
        - series["P_bess_charge"][i]
        for i in range(len(T))
    ]
    max_balance_residual = max(abs(x) for x in balance_residual)

    overlap = [min(series["P_bess_charge"][i], series["P_bess_discharge"][i]) for i in range(len(T))]
    max_overlap = max(overlap) if overlap else 0.0
    overlap_hours = [i + 1 for i, v in enumerate(overlap) if v > tol]

    soc_min = params["soc_min_frac"] * caps["E_bess_cap"]
    soc_max = params["soc_max_frac"] * caps["E_bess_cap"]

    soc_below = sum(1 for x in series["SOC"] if x < soc_min - tol)
    soc_above = sum(1 for x in series["SOC"] if x > soc_max + tol)
    pv_at_limit = sum(1 for i in range(len(T)) if abs(series["P_pv_gen"][i] - pv_limit[i]) <= tol)
    grid_at_limit = sum(1 for i in range(len(T)) if abs(series["P_grid_import"][i] - grid_limit[i]) <= tol)
    bess_c_at_limit = sum(1 for i in range(len(T)) if abs(series["P_bess_charge"][i] - bess_charge_limit[i]) <= tol)
    bess_d_at_limit = sum(1 for i in range(len(T)) if abs(series["P_bess_discharge"][i] - bess_discharge_limit[i]) <= tol)

    linhas.append("")
    linhas.append("4. CHECAGENS DE QUALIDADE (boas práticas de otimização):")
    linhas.append(f"   - Resíduo máximo do balanço de energia: {max_balance_residual:.6f} kW")
    linhas.append(f"   - SOC abaixo do mínimo: {soc_below} horas")
    linhas.append(f"   - SOC acima do máximo: {soc_above} horas")
    linhas.append(f"   - Horas com PV no limite (capacidade x irradiância): {pv_at_limit}")
    linhas.append(f"   - Horas com importação no limite do trafo: {grid_at_limit}")
    linhas.append(f"   - Horas com carga do BESS no limite: {bess_c_at_limit}")
    linhas.append(f"   - Horas com descarga do BESS no limite: {bess_d_at_limit}")
    linhas.append(f"   - Máx sobreposição carga/descarga: {max_overlap:.6f} kW")
    if overlap_hours:
        linhas.append(f"   - Horas com sobreposição acima da tolerância: {overlap_hours}")

    linhas.append("")
    linhas.append("5. FIGURAS RECOMENDADAS (como construir):")
    linhas.append("   - Balanço de potência: PV, rede, carga, BESS (série horária).")
    linhas.append("   - SOC com limites: SOC + linhas em SOCmin/SOCmax.")
    linhas.append("   - PV vs limite: P_pv_gen vs P_pv_cap * irradiancia_cf.")
    linhas.append("   - Rede vs limite: P_grid_import vs P_trafo_cap.")
    linhas.append("   - Limites BESS: carga/descarga vs c_rate * E_bess_cap.")
    linhas.append("   - Resíduo do balanço: série de PV + rede + desc - carga - EV.")

    return "\n".join(linhas) + "\n"

def gerar_relatorio(model, caminho_relatorio, resultados=None):
    """Gera um relatório textual com os principais resultados."""
    texto = render_relatorio(model, resultados=resultados)
    with open(caminho_relatorio, 'w', encoding='utf-8') as f:
        f.write(texto)
    print(f"Relatorio salvo em: {caminho_relatorio}")

def gerar_figuras_resultados(resultados, diretorio_figuras):
    """Gera graficos a partir de resultados serializados."""
    diretorio_figuras.mkdir(parents=True, exist_ok=True)
    T = resultados["T"]
    params = resultados["params"]
    caps = resultados["caps"]
    series = resultados["series"]

    ev_load = series["P_ev_load_kw"]
    grid_price = series["grid_price"]
    pv_gen = series["P_pv_gen"]
    grid_imp = series["P_grid_import"]
    soc = series["SOC"]
    bess_c = series["P_bess_charge"]
    bess_d = series["P_bess_discharge"]
    
    # Figura 1: Balanço de Potência
    plt.figure(figsize=(12, 6))
    plt.plot(T, pv_gen, label='Geração PV (kW)', linewidth=2, color=COLOR["pv"])
    plt.plot(T, grid_imp, label='Importação da Rede (kW)', linewidth=2, color=COLOR["grid"])
    plt.bar(T, ev_load, label='Demanda EV (kW)', alpha=0.3, color=COLOR["ev"])

    plt.fill_between(T, 0, bess_c, label='Carga BESS', alpha=0.3, color=COLOR["bess_charge"])
    plt.fill_between(T, 0, [-x for x in bess_d], label='Descarga BESS', alpha=0.3, color=COLOR["bess_discharge"])
    plt.xlabel('Hora')
    plt.ylabel('Potência (kW)')
    plt.title('Balanço de Potência da Microrrede (Zero-Grid Export)')
    plt.grid(True, linestyle=':', alpha=0.7)
    plt.legend(**LEGEND_KW)
    plt.tight_layout()
    plt.savefig(diretorio_figuras / 'balanco_potencia.png', dpi=150)
    plt.show()
    
    # Figura 2: Tarifa, SOC e Geração PV
    fig, ax1 = plt.subplots(figsize=(12, 6))
    ax1.plot(T, grid_price, 'o-', label='Tarifa de Energia (R$/kWh)', color=COLOR["price"])
    ax1.set_xlabel('Hora')
    ax1.set_ylabel('Tarifa (R$/kWh)', color=COLOR["price"])
    ax1.tick_params(axis='y', labelcolor=COLOR["price"])
    
    ax2 = ax1.twinx()
    ax2.plot(T, soc, 's-', label='SOC da Bateria (kWh)', color=COLOR["soc"])
    ax2.set_ylabel('SOC (kWh)', color=COLOR["soc"])
    ax2.tick_params(axis='y', labelcolor=COLOR["soc"])
    
    ax3 = ax1.twinx()
    ax3.spines['right'].set_position(('outward', 60))
    ax3.plot(T, pv_gen, 'd-', label='Geração PV (kW)', color=COLOR["pv"], linestyle=':')
    ax3.set_ylabel('Potência PV (kW)', color=COLOR["pv"])
    ax3.tick_params(axis='y', labelcolor=COLOR["pv"])
    
    fig.suptitle('Tarifa, Estado de Carga e Geração Fotovoltaica')
    fig.legend(**LEGEND_KW)
    fig.tight_layout()
    plt.savefig(diretorio_figuras / 'tarifa_soc_pv.png', dpi=150)
    plt.show()

    # Figura 3: SOC com limites
    soc_min = params["soc_min_frac"] * caps["E_bess_cap"]
    soc_max = params["soc_max_frac"] * caps["E_bess_cap"]
    plt.figure(figsize=(12, 6))
    plt.plot(T, soc, label='SOC (kWh)', linewidth=2, color=COLOR["soc"])
    plt.axhline(soc_min, color=COLOR["limit"], linestyle='--', label='SOC mínimo')
    plt.axhline(soc_max, color=COLOR["limit"], linestyle='--', label='SOC máximo')
    plt.xlabel('Hora')
    plt.ylabel('SOC (kWh)')
    plt.title('Estado de Carga com Limites Operacionais')
    plt.grid(True, linestyle=':', alpha=0.7)
    plt.legend(**LEGEND_KW)
    plt.tight_layout()
    plt.savefig(diretorio_figuras / 'soc_limites.png', dpi=150)
    plt.show()

    # Figura 4: PV gerado vs limite por irradiância
    pv_limit = [caps["P_pv_cap"] * cf for cf in series["irradiance_cf"]]
    plt.figure(figsize=(12, 6))
    plt.plot(T, pv_gen, label='PV gerado (kW)', linewidth=2, color=COLOR["pv"])
    plt.plot(T, pv_limit, label='Limite PV (cap x irradiância)', linewidth=2, linestyle='--', color=COLOR["limit"])
    plt.xlabel('Hora')
    plt.ylabel('Potência (kW)')
    plt.title('PV Gerado vs Limite de Capacidade')
    plt.grid(True, linestyle=':', alpha=0.7)
    plt.legend(**LEGEND_KW)
    plt.tight_layout()
    plt.savefig(diretorio_figuras / 'pv_limite.png', dpi=150)
    plt.show()

    # Figura 5: Importação vs limite do transformador
    plt.figure(figsize=(12, 6))
    plt.plot(T, grid_imp, label='Importação da Rede (kW)', linewidth=2, color=COLOR["grid"])
    plt.axhline(caps["P_trafo_cap"], color=COLOR["limit"], linestyle='--', label='Limite do Trafo (kW)')
    plt.xlabel('Hora')
    plt.ylabel('Potência (kW)')
    plt.title('Importação vs Limite do Transformador')
    plt.grid(True, linestyle=':', alpha=0.7)
    plt.legend(**LEGEND_KW)
    plt.tight_layout()
    plt.savefig(diretorio_figuras / 'trafo_limite.png', dpi=150)
    plt.show()

    # Figura 6: Limites de potência do BESS
    bess_charge_limit = params["c_rate_charge"] * caps["E_bess_cap"]
    bess_discharge_limit = params["c_rate_discharge"] * caps["E_bess_cap"]
    plt.figure(figsize=(12, 6))
    plt.bar(T, bess_c, label='Carga BESS (kW)', alpha=0.35, color=COLOR["bess_charge"])
    plt.plot(T, [-x for x in bess_d], label='Descarga BESS (kW)', linewidth=2, color=COLOR["bess_discharge"])
    plt.axhline(bess_charge_limit, color=COLOR["bess_charge"], linestyle='--', label='Limite de carga')
    plt.axhline(-bess_discharge_limit, color=COLOR["bess_discharge"], linestyle='--', label='Limite de descarga')
    plt.xlabel('Hora')
    plt.ylabel('Potência (kW)')
    plt.title('BESS: Potência vs Limites')
    plt.grid(True, linestyle=':', alpha=0.7)
    plt.legend(**LEGEND_KW)
    plt.tight_layout()
    plt.savefig(diretorio_figuras / 'bess_limites.png', dpi=150)
    plt.show()

    # Figura 7: Resíduo do balanço de energia
    balance_residual = [
        pv_gen[i] + grid_imp[i] + bess_d[i] - ev_load[i] - bess_c[i]
        for i in range(len(T))
    ]
    plt.figure(figsize=(12, 6))
    plt.plot(T, balance_residual, label='Resíduo (kW)', linewidth=2, color=COLOR["residual"])
    plt.axhline(0, color=COLOR["limit"], linestyle='--')
    plt.xlabel('Hora')
    plt.ylabel('Resíduo (kW)')
    plt.title('Resíduo do Balanço de Energia')
    plt.grid(True, linestyle=':', alpha=0.7)
    plt.legend(**LEGEND_KW)
    plt.tight_layout()
    plt.savefig(diretorio_figuras / 'residuo_balanco.png', dpi=150)
    plt.show()

def gerar_figuras(model, diretorio_figuras):
    """Gera graficos a partir do modelo resolvido."""
    resultados = extrair_resultados(model)
    gerar_figuras_resultados(resultados, diretorio_figuras)

def parse_args():
    parser = argparse.ArgumentParser(description="Executa a otimizacao da microrrede.")
    parser.add_argument(
        "--versao",
        default=DEFAULT_VERSION,
        help="Nome da pasta de saida da versao (ex.: v1_2026-05-12).",
    )
    parser.add_argument(
        "--somente-figuras",
        action="store_true",
        help="Gera figuras a partir de resultados salvos, sem reotimizar.",
    )
    parser.add_argument(
        "--resultados",
        default=None,
        help="Caminho do JSON de resultados (padrao: <versao>/resultados.json).",
    )
    parser.add_argument(
        "--versao-grafico",
        default=DEFAULT_GRAFICO_VERSION,
        help="Versao/tipo de grafico para pasta de figuras (ex.: v1, v2).",
    )
    return parser.parse_args()

# ==============================================================================
# EXECUÇÃO PRINCIPAL
# ==============================================================================
if __name__ == "__main__":
    args = parse_args()
    dir_base = Path(__file__).parent
    dir_saida = dir_base / args.versao
    dir_figuras = dir_saida / f"figuras_microrrede_{args.versao_grafico}"
    dir_saida.mkdir(parents=True, exist_ok=True)

    if args.somente_figuras:
        caminho_resultados = Path(args.resultados) if args.resultados else (dir_saida / "resultados.json")
        resultados = carregar_resultados_json(caminho_resultados)
        gerar_figuras_resultados(resultados, dir_figuras)
        print("\nFiguras geradas a partir de resultados salvos.")
        raise SystemExit(0)

    # ---------- 1. CARREGAR OS DADOS (simulando o arquivo .dat) ----------
    # Para manter o exemplo auto-contido, os dados são definidos aqui.
    # Em um cenário real, você poderia lê-los de um arquivo.
    dados_exemplo = {
        'delta_t': 1,
        'operational_days_equivalent': 365,
        'tariff_ev': 1.60,              # BRL/kWh
        'capex_pv_kw': 1200,            # BRL/kW
        'capex_bess_kwh': 700,          # BRL/kWh
        'capex_trafo_kw': 1000,         # BRL/kW
        'eta_charge': 0.91,
        'eta_discharge': 0.91,
        'soc_min_frac': 0.05,
        'soc_max_frac': 0.95,
        'soc_initial_frac': 0.50,
        'c_rate_charge': 1.00,
        'c_rate_discharge': 1.00,
        'E_bess_cap_max': 2000,          # kWh
        'P_pv_cap_max': 1000,            # kW
        'P_trafo_cap_max': 500,          # kW
        'irradiance_cf': {
            1: 0.00, 2: 0.00, 3: 0.00, 4: 0.00, 5: 0.10, 6: 0.30, 7: 0.50, 8: 0.70, 9: 0.90, 10: 1.00,
            11: 0.95, 12: 0.90, 13: 0.85, 14: 0.80, 15: 0.70, 16: 0.50, 17: 0.30, 18: 0.10, 19: 0.00, 20: 0.00,
            21: 0.00, 22: 0.00, 23: 0.00, 24: 0.00
        },
        'grid_price': {
            1: 0.25, 2: 0.25, 3: 0.25, 4: 0.25, 5: 0.25, 6: 0.25, 7: 0.54, 8: 0.88, 9: 0.88, 10: 0.88,
            11: 0.88, 12: 0.54, 13: 0.54, 14: 0.54, 15: 0.54, 16: 0.54, 17: 0.88, 18: 1.10, 19: 1.10, 20: 1.10,
            21: 0.88, 22: 0.54, 23: 0.25, 24: 0.25
        },
        
        'P_ev_load_kw': {
            1: 35, 2: 28, 3: 22, 4: 20, 5: 25, 6: 48, 7: 72, 8: 98, 9: 105, 10: 115,
            11: 110, 12: 100, 13: 95, 14: 90, 15: 98, 16: 112, 17: 126, 18: 135, 19: 128, 20: 116,
            21: 94, 22: 72, 23: 54, 24: 42
        }
    }
    
    # ---------- 2. CRIAR, RESOLVER E ANALISAR O MODELO ----------
    modelo = criar_modelo(dados_exemplo)
    resolver_modelo(modelo)  # usa 'gurobi' por padrão; troque para 'glpk' se necessário

    resultados = extrair_resultados(modelo)

    caminho_resultados = dir_saida / "resultados.json"
    resultados_norm = normalizar_resultados(resultados)
    resultados_antigos_norm = None
    if caminho_resultados.exists():
        resultados_antigos = carregar_resultados_json(caminho_resultados)
        resultados_antigos_norm = normalizar_resultados(resultados_antigos)

    if resultados_antigos_norm is not None and resultados_antigos_norm == resultados_norm:
        print("Resultados identicos (com arredondamento). Mantendo versao salva.")
    else:
        salvar_resultados_json(resultados, caminho_resultados)
        caminho_relatorio = dir_saida / "relatorio_microrrede.txt"
        relatorio_novo = render_relatorio(modelo, resultados=resultados)
        caminho_relatorio.write_text(relatorio_novo, encoding='utf-8')
        print(f"Relatorio salvo em: {caminho_relatorio}")
        gerar_figuras_resultados(resultados, dir_figuras)
    
    print("\nProcesso concluído com sucesso!")
# ----------- Teste de imagens

 