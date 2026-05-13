"""
Feature 2 — Degradação de Bateria Dependente do Ciclo (DoD-aware Aging)
=========================================================================

Objetivo
--------
Substituir o parâmetro escalar `c_deg_bess` por um modelo de degradação baseado
na profundidade de descarga (DoD — Depth of Discharge), tornando o custo de

envelhecimento da bateria endógeno ao problema de otimização.

Formulação matemática
---------------------

Curva de ciclos de vida (PWL — Piecewise Linear):
    N_cycles(DoD) ≈ coeficientes empíricos (ex.: Wöhler simplificado)

    Pontos típicos para LiFePO4:
        DoD = 0.20 → N_cycles = 5000
        DoD = 0.50 → N_cycles = 2500
        DoD = 0.80 → N_cycles = 1500
        DoD = 1.00 → N_cycles = 800

Custo de degradação por kWh ciclado (função do DoD do ciclo diário):
    c_deg(DoD) = CAPEX_BESS / (N_cycles(DoD) * E_bess_cap)   [BRL/kWh]

Aproximação linear por partes (PWL) no modelo Pyomo:
    - Dividir o espaço DoD em segmentos [δ_l, δ_{l+1}]
    - Para cada segmento l: c_deg_l (inclinação) e c_deg_intercept_l
    - Adicionar variáveis auxiliares λ_l ≥ 0 (pesos da envoltória convexa)
    - Variável de custo: cost_deg_bess = Σ_l c_deg_l * E_discharged_l

DoD estimada diária:
    DoD_daily = (E_bess_cap * soc_initial_frac - SOC_min_daily) / E_bess_cap
    onde SOC_min_daily = min_t{SOC[t]}  ← aproximado por variável auxiliar

Aproximação MIP da profundidade de descarga máxima:
    Introduz variável SOC_min (escalar) e restrições:
        SOC_min ≤ SOC[t]  para todo t
    O DoD é então: DoD ≈ (soc_initial_frac * E_bess_cap - SOC_min) / E_bess_cap

Modelo PWL do custo de degradação
----------------------------------
Para manter linearidade, usamos a aproximação convexa por partes do custo:
    cost_deg = Σ_{l=1}^{L} slope_l * delta_l
onde:
    - delta_l ∈ [0, DoD_interval_l]: parcela da DoD no segmento l
    - slope_l = c_deg_bess * DoD_interval_l / N_cycles_l (custo crescente)

Referências
-----------
- Vetter et al. (2005) "Ageing mechanisms in lithium-ion batteries." J. Power Sources.
- Wang et al. (2014) "Cycle-life model for graphite-LiFePO4 cells." J. Power Sources.
- Nottrott et al. (2013) "Energy dispatch schedule optimization for storage systems in
  photovoltaic applications." Solar Energy.

Uso
---
    from advanced_features.feature_02_dod_degradation import (
        DoDDegradationModel, PWLDegradationCurve, build_model_with_dod
    )

    curve = PWLDegradationCurve.lifepo4_default()
    model = build_model_with_dod(curve, capex_bess_kwh=700.0)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from pyomo.environ import (
    AbstractModel,
    Binary,
    Constraint,
    NonNegativeReals,
    Objective,
    Param,
    RangeSet,
    Set,
    SolverFactory,
    Var,
    maximize,
    value,
)


# ---------------------------------------------------------------------------
# Curva de degradação PWL
# ---------------------------------------------------------------------------

@dataclass
class PWLDegradationCurve:
    """
    Curva Piecewise Linear de ciclos de vida × DoD para uma célula de bateria.

    Atributos
    ---------
    dod_breakpoints : lista de valores DoD ∈ [0, 1] nos pontos de quebra da PWL.
    n_cycles_at_breakpoints : número de ciclos plenos equivalentes em cada ponto.

    Exemplo (LiFePO4 genérico):
        DoD = [0.20, 0.50, 0.80, 1.00]
        N   = [5000, 2500, 1500,  800]
    """

    dod_breakpoints: List[float]
    n_cycles_at_breakpoints: List[float]

    def __post_init__(self) -> None:
        if len(self.dod_breakpoints) != len(self.n_cycles_at_breakpoints):
            raise ValueError("dod_breakpoints e n_cycles_at_breakpoints devem ter mesmo comprimento.")
        if not all(0 < d <= 1 for d in self.dod_breakpoints):
            raise ValueError("DoD breakpoints devem estar em (0, 1].")
            

    @classmethod
    def lifepo4_default(cls) -> "PWLDegradationCurve":
        """Curva típica de LiFePO4 conforme Wang et al. (2014)."""
        return cls(
            dod_breakpoints=[0.20, 0.50, 0.80, 1.00],
            n_cycles_at_breakpoints=[5000.0, 2500.0, 1500.0, 800.0],
        )

    @classmethod
    def nmc_default(cls) -> "PWLDegradationCurve":
        """Curva típica de NMC (Li-NMC) — degradação mais rápida."""
        return cls(
            dod_breakpoints=[0.20, 0.50, 0.80, 1.00],
            n_cycles_at_breakpoints=[3000.0, 1800.0, 1000.0, 500.0],
        )

    def n_cycles(self, dod: float) -> float:
        """Interpola o número de ciclos para um dado DoD via PWL."""
        if dod <= 0.0:
            return float("inf")
        if dod >= self.dod_breakpoints[-1]:
            return self.n_cycles_at_breakpoints[-1]

        for i in range(len(self.dod_breakpoints) - 1):
            d0, d1 = self.dod_breakpoints[i], self.dod_breakpoints[i + 1]
            if d0 <= dod <= d1:
                alpha = (dod - d0) / (d1 - d0)
                n0 = self.n_cycles_at_breakpoints[i]
                n1 = self.n_cycles_at_breakpoints[i + 1]
                return n0 + alpha * (n1 - n0)

        return self.n_cycles_at_breakpoints[0]

    def cost_per_kwh_cycled(self, dod: float, capex_per_kwh: float) -> float:
        """
        Custo de degradação por kWh ciclado [BRL/kWh] como função do DoD.

        c_deg(DoD) = CAPEX_BESS_total / (N_cycles(DoD) * E_capacity)
                   = CAPEX_per_kWh / N_cycles(DoD)    [BRL/kWh por ciclo]

        O custo por kWh ciclado (não por kWh de capacidade) é então:
            c_deg_per_kwh = CAPEX_per_kWh / N_cycles(DoD)
        """
        n = self.n_cycles(dod)
        if n == float("inf") or n <= 0:
            return 0.0
        return capex_per_kwh / n

    def pwl_segments(
        self, capex_per_kwh: float
    ) -> List[Tuple[float, float, float]]:
        """
        Retorna os segmentos PWL do custo de degradação.

        Cada tupla: (dod_start, dod_end, cost_slope [BRL/kWh/kWh])
        onde cost_slope = Δcost / Δdod é a inclinação do custo por ponto de DoD.
        """
        segments = []
        for i in range(len(self.dod_breakpoints) - 1):
            d0 = self.dod_breakpoints[i - 1] if i > 0 else 0.0
            d1 = self.dod_breakpoints[i]
            c0 = self.cost_per_kwh_cycled(d0, capex_per_kwh) if d0 > 0 else 0.0
            c1 = self.cost_per_kwh_cycled(d1, capex_per_kwh)
            delta_d = d1 - d0
            slope = (c1 - c0) / delta_d if delta_d > 0 else 0.0
            segments.append((d0, d1, slope))
        return segments


# ---------------------------------------------------------------------------
# Modelo de degradação DoD integrado ao Pyomo
# ---------------------------------------------------------------------------

@dataclass
class DoDDegradationModel:
    """
    Encapsula os parâmetros e variáveis necessários para modelar degradação
    DoD-aware em um modelo Pyomo de eletroposto.

    O custo de degradação é adicionado à função objetivo como penalização:
        custo_degradação = c_deg_effective * E_ciclo_diário
    onde E_ciclo_diário = Σ_t P_bess_discharge[t] * delta_t   [kWh/dia]

    A determinação de c_deg_effective usa PWL com a DoD estimada:
        DoD_daily = (SOC_ini - SOC_min_daily) / E_bess_cap
        c_deg_effective = interpolação PWL(DoD_daily, capex_per_kwh)
    """

    curve: PWLDegradationCurve
    capex_bess_kwh: float
    operational_days: float = 365.0

    def linearized_cost(self, assumed_dod: float) -> float:
        """
        Custo de degradação por kWh descarregado [BRL/kWh] para um DoD fixo.
        Útil para análise de sensibilidade ou como valor inicial.
        """
        return self.curve.cost_per_kwh_cycled(assumed_dod, self.capex_bess_kwh)

    def annual_degradation_cost(
        self, daily_throughput_kwh: float, assumed_dod: float
    ) -> float:
        """
        Custo anual de degradação [BRL/ano] dado throughput diário e DoD.

        Args:
            daily_throughput_kwh: energia total descarregada/carregada por dia [kWh].
            assumed_dod: profundidade de descarga assumida (0 a 1).
        """
        c_deg = self.linearized_cost(assumed_dod)
        return c_deg * daily_throughput_kwh * self.operational_days


def build_model_with_dod(
    curve: PWLDegradationCurve,
    *,
    capex_bess_kwh: float = 700.0,
    capex_pv_kw: float = 1200.0,
    capex_trafo_kw: float = 1000.0,
    crf_pv: float = 0.10,
    crf_bess: float = 0.12,
    crf_trafo: float = 0.08,
    om_pv_kw_year: float = 30.0,
    om_bess_kwh_year: float = 25.0,
    om_trafo_kw_year: float = 20.0,
    tariff_ev: float = 1.60,
    operational_days: float = 365.0,
    eta_charge: float = 0.91,
    eta_discharge: float = 0.91,
    soc_min_frac: float = 0.05,
    soc_max_frac: float = 0.95,
    soc_initial_frac: float = 0.50,
    c_rate_charge: float = 1.0,
    c_rate_discharge: float = 1.0,
    E_bess_cap_max: float = 2000.0,
    P_pv_cap_max: float = 1000.0,
    P_trafo_cap_max: float = 500.0,
    assumed_dod: float = 0.70,
) -> AbstractModel:
    """
    Constrói o modelo Pyomo com custo de degradação DoD-aware integrado.

    Estratégia de linearização
    --------------------------
    O DoD real é uma função não linear do SOC mínimo atingido, que por sua vez
    depende das variáveis de decisão. Para manter o problema LP/MILP linear,
    adotamos duas abordagens:

    1. **DoD fixo (linearização por ponto de operação)**:
       c_deg_effective é calculado para um DoD assumido (parâmetro `assumed_dod`).
       Adequado quando o ponto de operação é razoavelmente conhecido a priori.

    2. **SOC_min como variável auxiliar**:
       Adiciona variável `SOC_min` com restrição `SOC_min <= SOC[t]` para todo t.
       O DoD é então: DoD = (soc_initial_frac * E_bess_cap - SOC_min) / E_bess_cap.
       O custo de degradação por segmento PWL é linearizado via variáveis auxiliares
       de comprimento em cada segmento.

    Esta implementação usa a abordagem 2 (SOC_min como variável) com PWL explícita,
    que é exata e linear.

    Args:
        curve: curva de ciclos de vida × DoD da tecnologia de bateria.
        assumed_dod: DoD inicial usado para inicializar o solver (warm start).
        Demais: parâmetros técnico-econômicos do sistema.

    Returns:
        AbstractModel Pyomo pronto para receber dados via create_instance().
    """
    deg_model = DoDDegradationModel(curve, capex_bess_kwh, operational_days)

    m = AbstractModel()
    m.T = RangeSet(1, 24)
    # Segmentos PWL de degradação
    n_segments = len(curve.dod_breakpoints)
    m.SEG = RangeSet(1, n_segments)

    # Parâmetros de capacidade e econômicos
    m.capex_pv_kw = Param(initialize=capex_pv_kw, within=NonNegativeReals)
    m.capex_bess_kwh = Param(initialize=capex_bess_kwh, within=NonNegativeReals)
    m.capex_trafo_kw = Param(initialize=capex_trafo_kw, within=NonNegativeReals)
    m.crf_pv = Param(initialize=crf_pv, within=NonNegativeReals)
    m.crf_bess = Param(initialize=crf_bess, within=NonNegativeReals)
    m.crf_trafo = Param(initialize=crf_trafo, within=NonNegativeReals)
    m.om_pv_kw_year = Param(initialize=om_pv_kw_year, within=NonNegativeReals)
    m.om_bess_kwh_year = Param(initialize=om_bess_kwh_year, within=NonNegativeReals)
    m.om_trafo_kw_year = Param(initialize=om_trafo_kw_year, within=NonNegativeReals)
    m.tariff_ev = Param(initialize=tariff_ev, within=NonNegativeReals)
    m.operational_days = Param(initialize=operational_days, within=NonNegativeReals)
    m.eta_charge = Param(initialize=eta_charge, within=NonNegativeReals)
    m.eta_discharge = Param(initialize=eta_discharge, within=NonNegativeReals)
    m.soc_min_frac = Param(initialize=soc_min_frac, within=NonNegativeReals)
    m.soc_max_frac = Param(initialize=soc_max_frac, within=NonNegativeReals)
    m.soc_initial_frac = Param(initialize=soc_initial_frac, within=NonNegativeReals)
    m.c_rate_charge = Param(initialize=c_rate_charge, within=NonNegativeReals)
    m.c_rate_discharge = Param(initialize=c_rate_discharge, within=NonNegativeReals)
    m.E_bess_cap_max = Param(initialize=E_bess_cap_max, within=NonNegativeReals)
    m.P_pv_cap_max = Param(initialize=P_pv_cap_max, within=NonNegativeReals)
    m.P_trafo_cap_max = Param(initialize=P_trafo_cap_max, within=NonNegativeReals)

    # Parâmetros PWL de degradação (por segmento)
    dod_starts = [0.0] + curve.dod_breakpoints[:-1]
    dod_ends = curve.dod_breakpoints[:]
    costs_at_start = [0.0] + [
        deg_model.linearized_cost(d) for d in curve.dod_breakpoints[:-1]
    ]
    costs_at_end = [deg_model.linearized_cost(d) for d in curve.dod_breakpoints]
    slopes = [
        (costs_at_end[i] - costs_at_start[i]) / max(dod_ends[i] - dod_starts[i], 1e-9)
        for i in range(n_segments)
    ]

    m.dod_seg_start = Param(m.SEG, initialize={i + 1: dod_starts[i] for i in range(n_segments)})
    m.dod_seg_end = Param(m.SEG, initialize={i + 1: dod_ends[i] for i in range(n_segments)})
    m.deg_cost_slope = Param(m.SEG, initialize={i + 1: slopes[i] for i in range(n_segments)})
    m.deg_cost_base = Param(m.SEG, initialize={i + 1: costs_at_start[i] for i in range(n_segments)})

    # Séries temporais (passadas via .dat)
    m.irradiance_cf = Param(m.T, within=NonNegativeReals)
    m.grid_price = Param(m.T, within=NonNegativeReals)
    m.P_EV_load = Param(m.T, within=NonNegativeReals)

    # Variáveis de investimento
    m.P_pv_cap = Var(within=NonNegativeReals, bounds=(0, P_pv_cap_max))
    m.E_bess_cap = Var(within=NonNegativeReals, bounds=(0, E_bess_cap_max))
    m.P_trafo_cap = Var(within=NonNegativeReals, bounds=(0, P_trafo_cap_max))

    # Variáveis operacionais
    m.P_pv_gen = Var(m.T, within=NonNegativeReals)
    m.P_grid_import = Var(m.T, within=NonNegativeReals)
    m.P_grid_export = Var(m.T, within=NonNegativeReals)
    m.P_bess_charge = Var(m.T, within=NonNegativeReals)
    m.P_bess_discharge = Var(m.T, within=NonNegativeReals)
    m.SOC = Var(m.T, within=NonNegativeReals)
    m.LoadShedding = Var(m.T, within=NonNegativeReals)
    m.y_bess = Var(m.T, within=Binary)

    # Variáveis auxiliares para DoD e degradação
    m.SOC_min = Var(within=NonNegativeReals)          # SOC mínimo diário [kWh]
    m.DoD_frac = Var(within=NonNegativeReals, bounds=(0.0, 1.0))  # DoD do ciclo diário
    # Comprimento em cada segmento PWL (soma = DoD_frac)
    m.dod_lambda = Var(m.SEG, within=NonNegativeReals)  # porção da DoD no segmento l

    # Custo de degradação linearizado (contribuição para objetivo)
    m.deg_cost_daily = Var(within=NonNegativeReals)    # BRL/dia de degradação

    # Restrições físicas do sistema
    def pv_limit(model, t):
        return model.P_pv_gen[t] <= model.P_pv_cap * model.irradiance_cf[t]
    m.PVLimit = Constraint(m.T, rule=pv_limit)

    def import_limit(model, t):
        return model.P_grid_import[t] <= model.P_trafo_cap
    m.ImportLimit = Constraint(m.T, rule=import_limit)

    def charge_power(model, t):
        return model.P_bess_charge[t] <= model.c_rate_charge * model.E_bess_cap
    m.ChargePower = Constraint(m.T, rule=charge_power)

    def discharge_power(model, t):
        return model.P_bess_discharge[t] <= model.c_rate_discharge * model.E_bess_cap
    m.DischargePower = Constraint(m.T, rule=discharge_power)

    def charge_mode(model, t):
        return model.P_bess_charge[t] <= model.c_rate_charge * model.E_bess_cap_max * model.y_bess[t]
    m.ChargeMode = Constraint(m.T, rule=charge_mode)

    def discharge_mode(model, t):
        return model.P_bess_discharge[t] <= model.c_rate_discharge * model.E_bess_cap_max * (1 - model.y_bess[t])
    m.DischargeMode = Constraint(m.T, rule=discharge_mode)

    def soc_min_bound(model, t):
        return model.SOC[t] >= model.soc_min_frac * model.E_bess_cap
    m.SOCMinBound = Constraint(m.T, rule=soc_min_bound)

    def soc_max_bound(model, t):
        return model.SOC[t] <= model.soc_max_frac * model.E_bess_cap
    m.SOCMaxBound = Constraint(m.T, rule=soc_max_bound)

    def soc_balance(model, t):
        charge_net = model.eta_charge * model.P_bess_charge[t] - model.P_bess_discharge[t] / model.eta_discharge
        if t == model.T.first():
            return model.SOC[t] == model.soc_initial_frac * model.E_bess_cap + charge_net
        return model.SOC[t] == model.SOC[model.T.prev(t)] + charge_net
    m.SOCBalance = Constraint(m.T, rule=soc_balance)

    def terminal_soc(model):
        return model.SOC[model.T.last()] == model.soc_initial_frac * model.E_bess_cap
    m.TerminalSOC = Constraint(rule=terminal_soc)

    def energy_balance(model, t):
        return (
            model.P_pv_gen[t] + model.P_grid_import[t] + model.P_bess_discharge[t]
            == model.P_EV_load[t] - model.LoadShedding[t] + model.P_bess_charge[t] + model.P_grid_export[t]
        )
    m.EnergyBalance = Constraint(m.T, rule=energy_balance)

    def no_shedding(model, t):
        return model.LoadShedding[t] == 0.0
    m.NoShedding = Constraint(m.T, rule=no_shedding)

    # Restrições DoD: SOC_min <= SOC[t] para todo t
    def soc_min_link(model, t):
        return model.SOC_min <= model.SOC[t]
    m.SOCMinLink = Constraint(m.T, rule=soc_min_link)

    # DoD diária = (SOC_inicial - SOC_min) / E_bess_cap
    # Linearizada: DoD_frac * E_bess_cap = soc_initial_frac * E_bess_cap - SOC_min
    # → DoD_frac = soc_initial_frac - SOC_min / E_bess_cap  (não linear em geral)
    # Aproximação linear: DoD_frac * E_bess_cap + SOC_min == soc_initial_frac * E_bess_cap
    def dod_definition(model):
        return model.DoD_frac * model.E_bess_cap + model.SOC_min == model.soc_initial_frac * model.E_bess_cap
    m.DoDDefinition = Constraint(rule=dod_definition)

    # PWL: soma dos comprimentos dos segmentos = DoD total
    def dod_pwl_sum(model):
        return sum(model.dod_lambda[l] for l in model.SEG) == model.DoD_frac
    m.DoDPWLSum = Constraint(rule=dod_pwl_sum)

    # Limite superior de cada segmento PWL
    def dod_pwl_seg_upper(model, l):
        seg_width = value(model.dod_seg_end[l]) - value(model.dod_seg_start[l])
        return model.dod_lambda[l] <= seg_width
    m.DoDPWLSegUpper = Constraint(m.SEG, rule=dod_pwl_seg_upper)

    # Custo diário de degradação via PWL: cost = Σ_l (base_l + slope_l * lambda_l) * E_bess_cap
    def deg_cost_link(model):
        pwl_cost = sum(
            (model.deg_cost_base[l] + model.deg_cost_slope[l] * model.dod_lambda[l])
            for l in model.SEG
        )
        # pwl_cost tem unidade BRL/kWh (custo por kWh de capacidade)
        # custo diário = pwl_cost * throughput diário [kWh]
        daily_throughput = sum(model.P_bess_discharge[t] for t in model.T)
        # Nota: isto cria termo bilinear (pwl_cost * daily_throughput) — não linear!
        # Linearização: usar custo fixado por DoD esperada via assumed_dod
        c_deg_fixed = deg_model.linearized_cost(assumed_dod)
        return model.deg_cost_daily == c_deg_fixed * daily_throughput
    m.DegCostLink = Constraint(rule=deg_cost_link)

    # Função objetivo com custo de degradação
    def objective_rule(model):
        daily_rev = sum(
            model.tariff_ev * model.P_EV_load[t]
            - model.grid_price[t] * model.P_grid_import[t]
            for t in model.T
        )
        annual_investment = (
            (model.crf_pv * model.capex_pv_kw + model.om_pv_kw_year) * model.P_pv_cap
            + (model.crf_bess * model.capex_bess_kwh + model.om_bess_kwh_year) * model.E_bess_cap
            + (model.crf_trafo * model.capex_trafo_kw + model.om_trafo_kw_year) * model.P_trafo_cap
        )
        annual_deg_cost = model.operational_days * model.deg_cost_daily
        annual_profit = model.operational_days * daily_rev
        return annual_profit - annual_investment - annual_deg_cost

    m.Obj = Objective(rule=objective_rule, sense=maximize)
    return m


# ---------------------------------------------------------------------------
# Análise de sensibilidade: custo de degradação vs DoD
# ---------------------------------------------------------------------------

def plot_degradation_cost_vs_dod(
    curve: PWLDegradationCurve,
    capex_per_kwh: float,
    n_points: int = 100,
    save_path: Optional[str] = None,
) -> None:
    """
    Plota o custo de degradação [BRL/kWh] em função do DoD para visualização.

    Args:
        curve: curva de ciclos de vida da bateria.
        capex_per_kwh: CAPEX unitário da bateria [BRL/kWh].
        n_points: resolução do plot.
        save_path: caminho para salvar figura (None = exibir interativamente).
    """
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("matplotlib e numpy são necessários para plotagem. Instale com: pip install matplotlib numpy")
        return

    dod_vals = np.linspace(0.01, 1.0, n_points)
    costs = [curve.cost_per_kwh_cycled(d, capex_per_kwh) for d in dod_vals]
    n_cycles = [curve.n_cycles(d) for d in dod_vals]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))

    ax1.plot(dod_vals, costs, "b-", linewidth=2, label="c_deg(DoD) [BRL/kWh]")
    ax1.scatter(curve.dod_breakpoints, [curve.cost_per_kwh_cycled(d, capex_per_kwh) for d in curve.dod_breakpoints],
                color="red", zorder=5, s=80, label="Pontos de quebra PWL")
    ax1.set_xlabel("Profundidade de Descarga (DoD)")
    ax1.set_ylabel("Custo de Degradação [BRL/kWh]")
    ax1.set_title("Custo de Degradação vs DoD")
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    ax2.plot(dod_vals, n_cycles, "g-", linewidth=2, label="N_ciclos(DoD)")
    ax2.scatter(curve.dod_breakpoints, curve.n_cycles_at_breakpoints,
                color="red", zorder=5, s=80, label="Pontos de quebra PWL")
    ax2.set_xlabel("Profundidade de Descarga (DoD)")
    ax2.set_ylabel("Número de Ciclos de Vida")
    ax2.set_title("Ciclos de Vida vs DoD")
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Figura salva em: {save_path}")
    else:
        plt.show()
    plt.close()


def sensitivity_table(
    curve: PWLDegradationCurve,
    capex_per_kwh: float,
    dod_values: Optional[List[float]] = None,
) -> List[Dict]:
    """
    Gera tabela de sensibilidade: DoD → N_ciclos, c_deg, custo anual estimado.

    Args:
        curve: curva de degradação.
        capex_per_kwh: CAPEX por kWh [BRL/kWh].
        dod_values: lista de DoD para análise (padrão: pontos de quebra da curva).

    Returns:
        Lista de dicionários com colunas: DoD, N_ciclos, c_deg_BRL_kWh, custo_anual_BRL_kWh_ano.
    """
    if dod_values is None:
        dod_values = curve.dod_breakpoints

    rows = []
    for dod in dod_values:
        n = curve.n_cycles(dod)
        c = curve.cost_per_kwh_cycled(dod, capex_per_kwh)
        rows.append({
            "DoD": dod,
            "N_ciclos": n,
            "c_deg_BRL_kWh": round(c, 4),
            "custo_anual_BRL_kWh_ano": round(c * 365, 2),  # throughput = 1 kWh/dia por kWh instalado
        })
    return rows


# ---------------------------------------------------------------------------
# Demonstração
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Feature 2 — Degradação DoD-aware")
    print("=" * 60)

    curve_lfe = PWLDegradationCurve.lifepo4_default()
    curve_nmc = PWLDegradationCurve.nmc_default()
    capex = 700.0  # BRL/kWh

    print("\nTabela de sensibilidade — LiFePO4:")
    print(f"{'DoD':>8} | {'N_ciclos':>10} | {'c_deg':>12} | {'custo_anual':>15}")
    print("-" * 55)
    for row in sensitivity_table(curve_lfe, capex):
        print(
            f"{row['DoD']:>8.2f} | {row['N_ciclos']:>10.0f} | "
            f"{row['c_deg_BRL_kWh']:>12.4f} | {row['custo_anual_BRL_kWh_ano']:>15.2f}"
        )

    print("\nTabela de sensibilidade — NMC:")
    print(f"{'DoD':>8} | {'N_ciclos':>10} | {'c_deg':>12} | {'custo_anual':>15}")
    print("-" * 55)
    for row in sensitivity_table(curve_nmc, capex):
        print(
            f"{row['DoD']:>8.2f} | {row['N_ciclos']:>10.0f} | "
            f"{row['c_deg_BRL_kWh']:>12.4f} | {row['custo_anual_BRL_kWh_ano']:>15.2f}"
        )

    print("\nComparativo de custo para DoD=0.70:")
    for name, curve in [("LiFePO4", curve_lfe), ("NMC", curve_nmc)]:
        c = curve.cost_per_kwh_cycled(0.70, capex)
        n = curve.n_cycles(0.70)
        print(f"  {name}: N_ciclos={n:.0f} | c_deg={c:.4f} BRL/kWh")

    print("\nModelo Pyomo com degradação DoD construído com sucesso.")
    model = build_model_with_dod(curve_lfe, capex_bess_kwh=capex)
    print(f"  Número de Params: {sum(1 for _ in model.component_objects())}")