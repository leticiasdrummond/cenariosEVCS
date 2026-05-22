"""
SUBPROGRAMAS PARA ANÁLISE DE MICRORREDES EM ELETROPOSTOS
=========================================================
Autor: Análise baseada nos códigos de leticiasdrummond
Descrição: Conjunto de subprogramas que implementam diferentes cenários de operação
de microrredes em eletropostos, com visualizações específicas e análise de riscos.
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Rectangle
import pandas as pd
from pyomo.environ import (
    ConcreteModel, AbstractModel, Param, Var, Constraint, Objective, 
    RangeSet, NonNegativeReals, Binary, maximize, value, SolverFactory
)
from pyomo.opt import SolverStatus, TerminationCondition


# =============================================================================
# 1. SUBPROGRAMA: CENÁRIO DE EXPORTAÇÃO PROIBIDA (GRID-PARALLEL APENAS IMPORT)
# =============================================================================
class ExportProibidoSimulator:
    """
    Simula microrrede onde NÃO é permitida a injeção de energia na rede.
    Útil para cenários onde a concessionária não permite geração distribuída
    ou onde a compensação é desfavorável.
    """
    
    def __init__(self, dados: Dict):
        self.dados = dados
        self.modelo = None
        self.resultados = None
        
    def construir_modelo(self) -> ConcreteModel:
        """Constrói o modelo Pyomo com exportação proibida."""
        model = ConcreteModel()
        dados = self.dados
        
        # Conjuntos
        model.T = RangeSet(1, 24)
        
        # Parâmetros
        model.delta_t = Param(initialize=dados['delta_t'], within=NonNegativeReals)
        model.operational_days = Param(initialize=dados.get('operational_days', 365), within=NonNegativeReals)
        model.tariff_ev = Param(initialize=dados['tariff_ev'], within=NonNegativeReals)
        
        # CAPEX
        model.capex_pv_kw = Param(initialize=dados['capex_pv_kw'], within=NonNegativeReals)
        model.capex_bess_kwh = Param(initialize=dados['capex_bess_kwh'], within=NonNegativeReals)
        model.capex_trafo_kw = Param(initialize=dados['capex_trafo_kw'], within=NonNegativeReals)
        
        # Parâmetros técnicos BESS
        model.eta_charge = Param(initialize=dados.get('eta_charge', 0.91), within=NonNegativeReals)
        model.eta_discharge = Param(initialize=dados.get('eta_discharge', 0.91), within=NonNegativeReals)
        model.soc_min_frac = Param(initialize=dados.get('soc_min_frac', 0.05), within=NonNegativeReals)
        model.soc_max_frac = Param(initialize=dados.get('soc_max_frac', 0.95), within=NonNegativeReals)
        model.soc_initial_frac = Param(initialize=dados.get('soc_initial_frac', 0.5), within=NonNegativeReals)
        model.c_rate_charge = Param(initialize=dados.get('c_rate_charge', 1.0), within=NonNegativeReals)
        model.c_rate_discharge = Param(initialize=dados.get('c_rate_discharge', 1.0), within=NonNegativeReals)
        
        # Limites máximos
        model.E_bess_cap_max = Param(initialize=dados.get('E_bess_cap_max', 2000), within=NonNegativeReals)
        model.P_pv_cap_max = Param(initialize=dados.get('P_pv_cap_max', 1000), within=NonNegativeReals)
        model.P_trafo_cap_max = Param(initialize=dados.get('P_trafo_cap_max', 500), within=NonNegativeReals)
        
        # Séries temporais
        model.irradiance_cf = Param(model.T, initialize=dados['irradiance_cf'], within=NonNegativeReals)
        model.grid_price = Param(model.T, initialize=dados['grid_price'], within=NonNegativeReals)
        model.P_ev_load_kw = Param(model.T, initialize=dados['P_ev_load_kw'], within=NonNegativeReals)
        
        # Variáveis de investimento
        model.P_pv_cap = Var(within=NonNegativeReals, bounds=(0, model.P_pv_cap_max))
        model.E_bess_cap = Var(within=NonNegativeReals, bounds=(0, model.E_bess_cap_max))
        model.P_trafo_cap = Var(within=NonNegativeReals, bounds=(0, model.P_trafo_cap_max))
        
        # Variáveis operacionais
        model.P_pv_gen = Var(model.T, within=NonNegativeReals)
        model.P_grid_import = Var(model.T, within=NonNegativeReals)
        model.P_bess_charge = Var(model.T, within=NonNegativeReals)
        model.P_bess_discharge = Var(model.T, within=NonNegativeReals)
        model.SOC = Var(model.T, within=NonNegativeReals)
        model.y_bess = Var(model.T, within=Binary)
        
        # ===== RESTRIÇÕES =====
        
        # 1. Limite de geração PV
        def pv_limit_rule(m, t):
            return m.P_pv_gen[t] <= m.P_pv_cap * m.irradiance_cf[t]
        model.PVLimit = Constraint(model.T, rule=pv_limit_rule)
        
        # 2. Limite do transformador (apenas importação - EXPORTAÇÃO PROIBIDA)
        def trafo_limit_rule(m, t):
            return m.P_grid_import[t] <= m.P_trafo_cap
        model.TrafoLimit = Constraint(model.T, rule=trafo_limit_rule)
        
        # 3. Limites de potência do BESS
        def bess_charge_limit_rule(m, t):
            return m.P_bess_charge[t] <= m.c_rate_charge * m.E_bess_cap
        model.BESSChargeLimit = Constraint(model.T, rule=bess_charge_limit_rule)
        
        def bess_discharge_limit_rule(m, t):
            return m.P_bess_discharge[t] <= m.c_rate_discharge * m.E_bess_cap
        model.BESSDischargeLimit = Constraint(model.T, rule=bess_discharge_limit_rule)
        
        # 4. Não-simultaneidade carga/descarga
        def bess_charge_mode_rule(m, t):
            return m.P_bess_charge[t] <= m.c_rate_charge * m.E_bess_cap_max * m.y_bess[t]
        model.BESSChargeMode = Constraint(model.T, rule=bess_charge_mode_rule)
        
        def bess_discharge_mode_rule(m, t):
            return m.P_bess_discharge[t] <= m.c_rate_discharge * m.E_bess_cap_max * (1 - m.y_bess[t])
        model.BESSDischargeMode = Constraint(model.T, rule=bess_discharge_mode_rule)
        
        # 5. Limites SOC
        def soc_min_rule(m, t):
            return m.SOC[t] >= m.soc_min_frac * m.E_bess_cap
        model.SOCMin = Constraint(model.T, rule=soc_min_rule)
        
        def soc_max_rule(m, t):
            return m.SOC[t] <= m.soc_max_frac * m.E_bess_cap
        model.SOCMax = Constraint(model.T, rule=soc_max_rule)
        
        # 6. Balanço SOC
        def soc_balance_rule(m, t):
            if t == m.T.first():
                return m.SOC[t] == m.soc_initial_frac * m.E_bess_cap + m.delta_t * (
                    m.eta_charge * m.P_bess_charge[t] - m.P_bess_discharge[t] / m.eta_discharge
                )
            t_prev = m.T.prev(t)
            return m.SOC[t] == m.SOC[t_prev] + m.delta_t * (
                m.eta_charge * m.P_bess_charge[t] - m.P_bess_discharge[t] / m.eta_discharge
            )
        model.SOCBalance = Constraint(model.T, rule=soc_balance_rule)
        
        # 7. Condição cíclica
        def terminal_soc_rule(m):
            return m.SOC[m.T.last()] == m.soc_initial_frac * m.E_bess_cap
        model.TerminalSOC = Constraint(rule=terminal_soc_rule)
        
        # 8. Balanço de energia (SEM exportação)
        def energy_balance_rule(m, t):
            return (m.P_pv_gen[t] + m.P_grid_import[t] + m.P_bess_discharge[t] == 
                    m.P_ev_load_kw[t] + m.P_bess_charge[t])
        model.EnergyBalance = Constraint(model.T, rule=energy_balance_rule)
        
        # ===== FUNÇÃO OBJETIVO =====
        def objective_rule(m):
            # Receita diária recarga
            daily_revenue = sum(m.tariff_ev * m.P_ev_load_kw[t] * m.delta_t for t in m.T)
            # Custo diário importação
            daily_cost = sum(m.grid_price[t] * m.P_grid_import[t] * m.delta_t for t in m.T)
            
            annual_profit = m.operational_days * (daily_revenue - daily_cost)
            
            capex = (m.capex_pv_kw * m.P_pv_cap + 
                     m.capex_bess_kwh * m.E_bess_cap + 
                     m.capex_trafo_kw * m.P_trafo_cap)
            
            return annual_profit - capex
        
        model.Obj = Objective(rule=objective_rule, sense=maximize)
        
        self.modelo = model
        return model
    
    def resolver(self, solver_name='gurobi', timeout=60) -> bool:
        """Resolve o modelo de otimização."""
        if self.modelo is None:
            self.construir_modelo()
        
        solver = SolverFactory(solver_name)
        if not solver.available(False):
            raise RuntimeError(f"Solver {solver_name} não disponível")
        
        results = solver.solve(self.modelo, options={'TimeLimit': timeout}, tee=False)
        
        if results.solver.termination_condition not in [TerminationCondition.optimal, 
                                                         TerminationCondition.locallyOptimal]:
            print(f"Atenção: Condição de término = {results.solver.termination_condition}")
            return False
        
        self._extrair_resultados()
        return True
    
    def _extrair_resultados(self):
        """Extrai resultados do modelo para um dicionário."""
        m = self.modelo
        self.resultados = {
            'capacidades': {
                'pv_kw': value(m.P_pv_cap),
                'bess_kwh': value(m.E_bess_cap),
                'trafo_kw': value(m.P_trafo_cap),
            },
            'economicos': self._calcular_metricas_economicas(),
            'operacao': {
                'hora': list(range(1, 25)),
                'pv_gen': [value(m.P_pv_gen[t]) for t in m.T],
                'grid_import': [value(m.P_grid_import[t]) for t in m.T],
                'bess_charge': [value(m.P_bess_charge[t]) for t in m.T],
                'bess_discharge': [value(m.P_bess_discharge[t]) for t in m.T],
                'soc': [value(m.SOC[t]) for t in m.T],
                'ev_load': [value(m.P_ev_load_kw[t]) for t in m.T],
                'grid_price': [value(m.grid_price[t]) for t in m.T],
                'irradiance': [value(m.irradiance_cf[t]) for t in m.T],
            }
        }
    
    def _calcular_metricas_economicas(self) -> Dict:
        """Calcula métricas econômicas detalhadas."""
        m = self.modelo
        daily_revenue = sum(value(m.tariff_ev) * value(m.P_ev_load_kw[t]) * value(m.delta_t) 
                           for t in m.T)
        daily_cost = sum(value(m.grid_price[t]) * value(m.P_grid_import[t]) * value(m.delta_t)
                        for t in m.T)
        
        operational_days = value(m.operational_days)
        annual_revenue = daily_revenue * operational_days
        annual_cost = daily_cost * operational_days
        annual_profit = annual_revenue - annual_cost
        
        capex = (value(m.capex_pv_kw) * value(m.P_pv_cap) +
                 value(m.capex_bess_kwh) * value(m.E_bess_cap) +
                 value(m.capex_trafo_kw) * value(m.P_trafo_cap))
        
        return {
            'receita_anual': annual_revenue,
            'custo_anual_energia': annual_cost,
            'lucro_operacional_anual': annual_profit,
            'capex_total': capex,
            'valor_objetivo': value(m.Obj),
            'payback_simples': capex / annual_profit if annual_profit > 0 else float('inf')
        }
    
    def visualizar(self, diretorio_saida: Path):
        """Gera visualizações específicas para o cenário de exportação proibida."""
        if self.resultados is None:
            print("Execute resolver() primeiro")
            return
        
        diretorio_saida.mkdir(parents=True, exist_ok=True)
        op = self.resultados['operacao']
        cap = self.resultados['capacidades']
        
        # Figura 1: Balanço de potência (estilo Sankey simplificado)
        fig, ax = plt.subplots(figsize=(14, 8))
        
        horas = op['hora']
        pv = np.array(op['pv_gen'])
        grid = np.array(op['grid_import'])
        bess_d = np.array(op['bess_discharge'])
        bess_c = np.array(op['bess_charge'])
        ev = np.array(op['ev_load'])
        
        # Geração total (PV + Grid + BESS descarga)
        geracao_total = pv + grid + bess_d
        
        ax.fill_between(horas, 0, pv, label='Geração PV', alpha=0.7, color='#2ecc71')
        ax.fill_between(horas, pv, pv + grid, label='Importação Rede', alpha=0.7, color='#e74c3c')
        ax.fill_between(horas, pv + grid, geracao_total, label='Descarga BESS', alpha=0.7, color='#f39c12')
        
        # Demanda e carga BESS
        ax.plot(horas, ev, 'o-', label='Demanda EV', color='#3498db', linewidth=2, markersize=4)
        ax.fill_between(horas, 0, -bess_c, label='Carga BESS', alpha=0.5, color='#9b59b6')
        
        ax.set_xlabel('Hora do dia', fontsize=12)
        ax.set_ylabel('Potência (kW)', fontsize=12)
        ax.set_title(f'Balanço de Potência - Exportação Proibida\n'
                    f'PV: {cap["pv_kw"]:.1f} kW | BESS: {cap["bess_kwh"]:.1f} kWh | Trafo: {cap["trafo_kw"]:.1f} kW',
                    fontsize=14)
        ax.grid(True, alpha=0.3)
        ax.legend(loc='upper right')
        ax.set_xlim(1, 24)
        
        plt.tight_layout()
        plt.savefig(diretorio_saida / 'export_proibido_balanco.png', dpi=150, bbox_inches='tight')
        plt.close()
        
        # Figura 2: SOC da bateria com limites
        fig, ax = plt.subplots(figsize=(14, 6))
        
        soc = np.array(op['soc'])
        soc_min = 0.05 * cap['bess_kwh']
        soc_max = 0.95 * cap['bess_kwh']
        
        ax.fill_between(horas, 0, soc, label='SOC (kWh)', alpha=0.7, color='#2ecc71')
        ax.axhline(soc_min, color='red', linestyle='--', label=f'SOC mínimo ({soc_min:.1f} kWh)')
        ax.axhline(soc_max, color='orange', linestyle='--', label=f'SOC máximo ({soc_max:.1f} kWh)')
        
        ax.set_xlabel('Hora do dia', fontsize=12)
        ax.set_ylabel('Estado de Carga (kWh)', fontsize=12)
        ax.set_title('Evolução do Estado de Carga da Bateria (SOC)', fontsize=14)
        ax.grid(True, alpha=0.3)
        ax.legend(loc='best')
        ax.set_xlim(1, 24)
        
        plt.tight_layout()
        plt.savefig(diretorio_saida / 'export_proibido_soc.png', dpi=150, bbox_inches='tight')
        plt.close()
        
        # Figura 3: Indicadores de risco de falha
        fig, ax = plt.subplots(figsize=(14, 6))
        
        # Margem de segurança do transformador
        trafo_utilizacao = np.array(op['grid_import']) / cap['trafo_kw'] if cap['trafo_kw'] > 0 else np.zeros(24)
        
        ax.plot(horas, trafo_utilizacao * 100, 'o-', label='Utilização do Trafo', color='#e74c3c', linewidth=2)
        ax.axhline(100, color='darkred', linestyle='--', label='Limite de segurança (100%)')
        ax.axhline(85, color='orange', linestyle=':', label='Alerta (85%)')
        
        ax.set_xlabel('Hora do dia', fontsize=12)
        ax.set_ylabel('Utilização do Transformador (%)', fontsize=12)
        ax.set_title('Análise de Risco - Nível de Disponibilidade da Rede Elétrica', fontsize=14)
        ax.grid(True, alpha=0.3)
        ax.legend(loc='best')
        ax.set_xlim(1, 24)
        
        # Destacar horas com alta utilização
        for i, util in enumerate(trafo_utilizacao):
            if util > 0.9:  # > 90%
                ax.axvspan(i+0.5, i+1.5, alpha=0.2, color='red')
        
        plt.tight_layout()
        plt.savefig(diretorio_saida / 'export_proibido_risco_trafo.png', dpi=150, bbox_inches='tight')
        plt.close()
        
        # Figura 4: Comparação tarifa vs SOC (estratégia de arbitragem)
        fig, ax1 = plt.subplots(figsize=(14, 6))
        
        tarifa = np.array(op['grid_price'])
        ax1.bar(horas, tarifa, label='Tarifa de Energia (R$/kWh)', alpha=0.6, color='#3498db')
        ax1.set_xlabel('Hora do dia', fontsize=12)
        ax1.set_ylabel('Tarifa (R$/kWh)', color='#3498db', fontsize=12)
        ax1.tick_params(axis='y', labelcolor='#3498db')
        
        ax2 = ax1.twinx()
        ax2.plot(horas, soc, 'o-', label='SOC da Bateria', color='#2ecc71', linewidth=2)
        ax2.set_ylabel('SOC (kWh)', color='#2ecc71', fontsize=12)
        ax2.tick_params(axis='y', labelcolor='#2ecc71')
        
        plt.title('Estratégia de Arbitragem: BESS carrega em tarifa baixa e descarrega em tarifa alta', fontsize=14)
        
        # Adicionar legendas combinadas
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left')
        
        plt.tight_layout()
        plt.savefig(diretorio_saida / 'export_proibido_arbitragem.png', dpi=150, bbox_inches='tight')
        plt.close()
    
    def gerar_relatorio(self) -> str:
        """Gera relatório textual detalhado."""
        if self.resultados is None:
            return "Execute resolver() primeiro"
        
        eco = self.resultados['economicos']
        cap = self.resultados['capacidades']
        
        relatorio = f"""
{'='*70}
RELATÓRIO: CENÁRIO DE EXPORTAÇÃO PROIBIDA
{'='*70}

1. CONFIGURAÇÃO DO CENÁRIO
   - Tipo: Grid-parallel apenas com importação (exportação NÃO permitida)
   - Objetivo: Maximizar (Lucro Operacional Anual - CAPEX total)

2. CAPACIDADES ÓTIMAS INSTALADAS
   - Geração Fotovoltaica (PV): {cap['pv_kw']:.2f} kW
   - Armazenamento (BESS):    {cap['bess_kwh']:.2f} kWh
   - Transformador:           {cap['trafo_kw']:.2f} kW

3. INDICADORES ECONÔMICOS
   - Receita anual com recarga EV:    R$ {eco['receita_anual']:,.2f}
   - Custo anual com importação:      R$ {eco['custo_anual_energia']:,.2f}
   - Lucro operacional anual:         R$ {eco['lucro_operacional_anual']:,.2f}
   - CAPEX total:                     R$ {eco['capex_total']:,.2f}
   - Função objetivo (lucro - CAPEX): R$ {eco['valor_objetivo']:,.2f}
   - Payback simples:                 {eco['payback_simples']:.1f} anos

4. ANÁLISE DE RISCOS (Disponibilidade da Rede Elétrica)
   - Sem exportação, o eletroposto depende exclusivamente da rede para suprir
     déficits quando PV + BESS são insuficientes.
   - Risco de falha: Horário de pico (18-21h) demanda máxima e tarifa alta.
   - Recomendação: Dimensionar BESS para cobrir janela crítica de 3 horas.

5. COMPARAÇÃO COM CENÁRIO DE EXPORTAÇÃO PERMITIDA
   - Este cenário é mais conservador, priorizando autossuficiência.
   - Perde-se receita potencial com excedentes de geração.
   - Indicado para locais com restrições regulatórias ou rede frágil.
{'='*70}
"""
        return relatorio


# =============================================================================
# 2. SUBPROGRAMA: CENÁRIO DE CAPEX SIMPLES (SEM ANUALIZAÇÃO)
# =============================================================================
class CapexSimplesSimulator:
    """
    Simula microrrede considerando CAPEX como custo único, sem anualização.
    Baseado diretamente no código rgridCAPEXSimple.py fornecido.
    """
    
    def __init__(self, dados: Dict, permitir_exportacao: bool = False):
        self.dados = dados
        self.permitir_exportacao = permitir_exportacao
        self.modelo = None
        self.resultados = None
        
    def construir_modelo(self) -> ConcreteModel:
        """Constrói o modelo com CAPEX simples."""
        model = ConcreteModel()
        dados = self.dados
        
        model.T = RangeSet(1, 24)
        
        # Parâmetros escalares
        model.delta_t = Param(initialize=dados.get('delta_t', 1), within=NonNegativeReals)
        model.operational_days = Param(initialize=dados.get('operational_days', 365), within=NonNegativeReals)
        model.tariff_ev = Param(initialize=dados['tariff_ev'], within=NonNegativeReals)
        model.export_price_factor = Param(initialize=dados.get('export_price_factor', 0.7), within=NonNegativeReals)
        
        # CAPEX (simples - único)
        model.capex_pv_kw = Param(initialize=dados['capex_pv_kw'], within=NonNegativeReals)
        model.capex_bess_kwh = Param(initialize=dados['capex_bess_kwh'], within=NonNegativeReals)
        model.capex_trafo_kw = Param(initialize=dados['capex_trafo_kw'], within=NonNegativeReals)
        
        # Técnicos BESS
        model.eta_charge = Param(initialize=dados.get('eta_charge', 0.91), within=NonNegativeReals)
        model.eta_discharge = Param(initialize=dados.get('eta_discharge', 0.91), within=NonNegativeReals)
        model.soc_min_frac = Param(initialize=dados.get('soc_min_frac', 0.05), within=NonNegativeReals)
        model.soc_max_frac = Param(initialize=dados.get('soc_max_frac', 0.95), within=NonNegativeReals)
        model.soc_initial_frac = Param(initialize=dados.get('soc_initial_frac', 0.5), within=NonNegativeReals)
        model.c_rate_charge = Param(initialize=dados.get('c_rate_charge', 1.0), within=NonNegativeReals)
        model.c_rate_discharge = Param(initialize=dados.get('c_rate_discharge', 1.0), within=NonNegativeReals)
        
        # Limites
        model.E_bess_cap_max = Param(initialize=dados.get('E_bess_cap_max', 2000), within=NonNegativeReals)
        model.P_pv_cap_max = Param(initialize=dados.get('P_pv_cap_max', 1000), within=NonNegativeReals)
        model.P_trafo_cap_max = Param(initialize=dados.get('P_trafo_cap_max', 500), within=NonNegativeReals)
        
        # Séries
        model.irradiance_cf = Param(model.T, initialize=dados['irradiance_cf'], within=NonNegativeReals)
        model.grid_price = Param(model.T, initialize=dados['grid_price'], within=NonNegativeReals)
        model.P_ev_load_kw = Param(model.T, initialize=dados['P_ev_load_kw'], within=NonNegativeReals)
        
        # Variáveis
        model.P_pv_cap = Var(within=NonNegativeReals, bounds=(0, model.P_pv_cap_max))
        model.E_bess_cap = Var(within=NonNegativeReals, bounds=(0, model.E_bess_cap_max))
        model.P_trafo_cap = Var(within=NonNegativeReals, bounds=(0, model.P_trafo_cap_max))
        
        model.P_pv_gen = Var(model.T, within=NonNegativeReals)
        model.P_grid_import = Var(model.T, within=NonNegativeReals)
        model.P_grid_export = Var(model.T, within=NonNegativeReals) if self.permitir_exportacao else None
        model.P_bess_charge = Var(model.T, within=NonNegativeReals)
        model.P_bess_discharge = Var(model.T, within=NonNegativeReals)
        model.SOC = Var(model.T, within=NonNegativeReals)
        model.y_bess = Var(model.T, within=Binary)
        
        # ===== RESTRIÇÕES =====
        
        # PV
        def pv_limit_rule(m, t):
            return m.P_pv_gen[t] <= m.P_pv_cap * m.irradiance_cf[t]
        model.PVLimit = Constraint(model.T, rule=pv_limit_rule)
        
        # Transformador (com ou sem exportação)
        def trafo_import_rule(m, t):
            return m.P_grid_import[t] <= m.P_trafo_cap
        model.TrafoImport = Constraint(model.T, rule=trafo_import_rule)
        
        if self.permitir_exportacao:
            def trafo_export_rule(m, t):
                return m.P_grid_export[t] <= m.P_trafo_cap
            model.TrafoExport = Constraint(model.T, rule=trafo_export_rule)
        
        # BESS
        def bess_charge_limit_rule(m, t):
            return m.P_bess_charge[t] <= m.c_rate_charge * m.E_bess_cap
        model.BESSChargeLimit = Constraint(model.T, rule=bess_charge_limit_rule)
        
        def bess_discharge_limit_rule(m, t):
            return m.P_bess_discharge[t] <= m.c_rate_discharge * m.E_bess_cap
        model.BESSDischargeLimit = Constraint(model.T, rule=bess_discharge_limit_rule)
        
        def bess_charge_mode_rule(m, t):
            return m.P_bess_charge[t] <= m.c_rate_charge * m.E_bess_cap_max * m.y_bess[t]
        model.BESSChargeMode = Constraint(model.T, rule=bess_charge_mode_rule)
        
        def bess_discharge_mode_rule(m, t):
            return m.P_bess_discharge[t] <= m.c_rate_discharge * m.E_bess_cap_max * (1 - m.y_bess[t])
        model.BESSDischargeMode = Constraint(model.T, rule=bess_discharge_mode_rule)
        
        # SOC
        def soc_min_rule(m, t):
            return m.SOC[t] >= m.soc_min_frac * m.E_bess_cap
        model.SOCMin = Constraint(model.T, rule=soc_min_rule)
        
        def soc_max_rule(m, t):
            return m.SOC[t] <= m.soc_max_frac * m.E_bess_cap
        model.SOCMax = Constraint(model.T, rule=soc_max_rule)
        
        # Dinâmica SOC
        def soc_balance_rule(m, t):
            if t == m.T.first():
                return m.SOC[t] == m.soc_initial_frac * m.E_bess_cap + m.delta_t * (
                    m.eta_charge * m.P_bess_charge[t] - m.P_bess_discharge[t] / m.eta_discharge
                )
            t_prev = m.T.prev(t)
            return m.SOC[t] == m.SOC[t_prev] + m.delta_t * (
                m.eta_charge * m.P_bess_charge[t] - m.P_bess_discharge[t] / m.eta_discharge
            )
        model.SOCBalance = Constraint(model.T, rule=soc_balance_rule)
        
        def terminal_soc_rule(m):
            return m.SOC[m.T.last()] == m.soc_initial_frac * m.E_bess_cap
        model.TerminalSOC = Constraint(rule=terminal_soc_rule)
        
        # Balanço de energia
        def energy_balance_rule(m, t):
            if self.permitir_exportacao:
                return (m.P_pv_gen[t] + m.P_grid_import[t] + m.P_bess_discharge[t] == 
                        m.P_ev_load_kw[t] + m.P_bess_charge[t] + m.P_grid_export[t])
            else:
                return (m.P_pv_gen[t] + m.P_grid_import[t] + m.P_bess_discharge[t] == 
                        m.P_ev_load_kw[t] + m.P_bess_charge[t])
        model.EnergyBalance = Constraint(model.T, rule=energy_balance_rule)
        
        # Objetivo (CAPEX simples)
        def objective_rule(m):
            daily_revenue_ev = sum(m.tariff_ev * m.P_ev_load_kw[t] * m.delta_t for t in m.T)
            daily_revenue_export = (sum(m.export_price_factor * m.grid_price[t] * m.P_grid_export[t] * m.delta_t 
                                       for t in m.T) if self.permitir_exportacao else 0)
            daily_cost_import = sum(m.grid_price[t] * m.P_grid_import[t] * m.delta_t for t in m.T)
            
            annual_profit = m.operational_days * (daily_revenue_ev + daily_revenue_export - daily_cost_import)
            
            capex = (m.capex_pv_kw * m.P_pv_cap + 
                     m.capex_bess_kwh * m.E_bess_cap + 
                     m.capex_trafo_kw * m.P_trafo_cap)
            
            return annual_profit - capex
        
        model.Obj = Objective(rule=objective_rule, sense=maximize)
        
        self.modelo = model
        return model
    
    def resolver(self, solver_name='gurobi', timeout=60) -> bool:
        if self.modelo is None:
            self.construir_modelo()
        
        solver = SolverFactory(solver_name)
        if not solver.available(False):
            raise RuntimeError(f"Solver {solver_name} não disponível")
        
        results = solver.solve(self.modelo, options={'TimeLimit': timeout}, tee=False)
        
        if results.solver.termination_condition not in [TerminationCondition.optimal, 
                                                         TerminationCondition.locallyOptimal]:
            print(f"Atenção: Condição de término = {results.solver.termination_condition}")
            return False
        
        self._extrair_resultados()
        return True
    
    def _extrair_resultados(self):
        m = self.modelo
        self.resultados = {
            'capacidades': {
                'pv_kw': value(m.P_pv_cap),
                'bess_kwh': value(m.E_bess_cap),
                'trafo_kw': value(m.P_trafo_cap),
            },
            'operacao': {
                'hora': list(range(1, 25)),
                'pv_gen': [value(m.P_pv_gen[t]) for t in m.T],
                'grid_import': [value(m.P_grid_import[t]) for t in m.T],
                'grid_export': [value(m.P_grid_export[t]) for t in m.T] if self.permitir_exportacao else [0]*24,
                'bess_charge': [value(m.P_bess_charge[t]) for t in m.T],
                'bess_discharge': [value(m.P_bess_discharge[t]) for t in m.T],
                'soc': [value(m.SOC[t]) for t in m.T],
                'ev_load': [value(m.P_ev_load_kw[t]) for t in m.T],
                'grid_price': [value(m.grid_price[t]) for t in m.T],
            }
        }
    
    def visualizar(self, diretorio_saida: Path):
        """Gera visualização comparativa CAPEX simples."""
        if self.resultados is None:
            print("Execute resolver() primeiro")
            return
        
        diretorio_saida.mkdir(parents=True, exist_ok=True)
        op = self.resultados['operacao']
        cap = self.resultados['capacidades']
        
        # Gráfico de barras empilhadas para balanço de potência
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
        
        horas = op['hora']
        pv = op['pv_gen']
        grid_imp = op['grid_import']
        grid_exp = op['grid_export']
        bess_c = op['bess_charge']
        bess_d = op['bess_discharge']
        ev = op['ev_load']
        
        # Subplot 1: Geração vs Demanda
        ax1.fill_between(horas, 0, pv, label='PV Gerado', alpha=0.7, color='#2ecc71')
        ax1.fill_between(horas, pv, [pv[i] + grid_imp[i] for i in range(24)], 
                         label='Importação Rede', alpha=0.7, color='#e74c3c')
        ax1.plot(horas, ev, 'o-', label='Demanda EV', color='#3498db', linewidth=2)
        ax1.plot(horas, bess_d, 's-', label='Descarga BESS', color='#f39c12', linewidth=1.5)
        
        ax1.set_ylabel('Potência (kW)', fontsize=12)
        ax1.set_title(f'CAPEX Simples - {"Com" if self.permitir_exportacao else "Sem"} Exportação\n'
                     f'PV: {cap["pv_kw"]:.1f} kW | BESS: {cap["bess_kwh"]:.1f} kWh | Trafo: {cap["trafo_kw"]:.1f} kW',
                     fontsize=14)
        ax1.grid(True, alpha=0.3)
        ax1.legend(loc='upper left')
        
        # Subplot 2: SOC e Carga/Descarga BESS
        ax2.fill_between(horas, 0, bess_c, label='Carga BESS', alpha=0.5, color='#9b59b6')
        ax2.fill_between(horas, 0, [-x for x in bess_d], label='Descarga BESS', alpha=0.5, color='#f39c12')
        
        ax2_twin = ax2.twinx()
        ax2_twin.plot(horas, op['soc'], 'o-', label='SOC (kWh)', color='#1abc9c', linewidth=2)
        ax2_twin.set_ylabel('SOC (kWh)', color='#1abc9c', fontsize=12)
        
        ax2.set_xlabel('Hora do dia', fontsize=12)
        ax2.set_ylabel('Potência BESS (kW)', fontsize=12)
        ax2.set_title('Operação da Bateria', fontsize=12)
        ax2.grid(True, alpha=0.3)
        
        lines1, labels1 = ax2.get_legend_handles_labels()
        lines2, labels2 = ax2_twin.get_legend_handles_labels()
        ax2.legend(lines1 + lines2, labels1 + labels2, loc='upper left')
        
        plt.tight_layout()
        plt.savefig(diretorio_saida / f'capex_simples_{"com" if self.permitir_exportacao else "sem"}_export.png', 
                    dpi=150, bbox_inches='tight')
        plt.close()
        
        # Gráfico de barras para tarifa vs SOC (estratégia)
        fig, ax = plt.subplots(figsize=(14, 6))
        
        tarifa = op['grid_price']
        soc = op['soc']
        
        # Normalizar para visualização comparativa
        tarifa_norm = np.array(tarifa) / max(tarifa) if max(tarifa) > 0 else tarifa
        soc_norm = np.array(soc) / max(soc) if max(soc) > 0 else soc
        
        x = np.arange(24)
        width = 0.35
        
        ax.bar(x - width/2, tarifa_norm, width, label='Tarifa (normalizada)', alpha=0.7, color='#3498db')
        ax.bar(x + width/2, soc_norm, width, label='SOC (normalizado)', alpha=0.7, color='#2ecc71')
        
        ax.set_xlabel('Hora do dia', fontsize=12)
        ax.set_ylabel('Valor normalizado', fontsize=12)
        ax.set_title('Estratégia de Carregamento: BESS segue inversamente a tarifa', fontsize=14)
        ax.set_xticks(x)
        ax.set_xticklabels(horas)
        ax.grid(True, alpha=0.3, axis='y')
        ax.legend()
        
        plt.tight_layout()
        plt.savefig(diretorio_saida / f'capex_simples_estrategia_{"com" if self.permitir_exportacao else "sem"}.png',
                    dpi=150, bbox_inches='tight')
        plt.close()
    
    def gerar_relatorio(self) -> str:
        """Gera relatório do cenário CAPEX simples."""
        if self.resultados is None:
            return "Execute resolver() primeiro"
        
        cap = self.resultados['capacidades']
        eco = self._calcular_metricas_economicas()
        
        relatorio = f"""
{'='*70}
RELATÓRIO: CENÁRIO CAPEX SIMPLES
{'='*70}

1. CONFIGURAÇÃO
   - Tipo de CAPEX: Simples (investimento único)
   - Exportação permitida: {'SIM' if self.permitir_exportacao else 'NÃO'}
   - Objetivo: Maximizar (Lucro Operacional Anual - CAPEX)

2. CAPACIDADES ÓTIMAS
   - PV: {cap['pv_kw']:.2f} kW
   - BESS: {cap['bess_kwh']:.2f} kWh
   - Transformador: {cap['trafo_kw']:.2f} kW

3. INDICADORES ECONÔMICOS
   - Receita anual recarga: R$ {eco['receita_recarga']:,.2f}
   - Receita anual exportação: R$ {eco['receita_exportacao']:,.2f}
   - Custo anual importação: R$ {eco['custo_importacao']:,.2f}
   - Lucro operacional anual: R$ {eco['lucro_operacional']:,.2f}
   - CAPEX total: R$ {eco['capex_total']:,.2f}
   - Payback: {eco['payback']:.1f} anos

4. VANTAGENS DO CAPEX SIMPLES
   - Mais fácil de interpretar para investidores diretos
   - Adequado para projetos com vida útil e taxa de desconto predefinidas
   - Comparação direta entre diferentes configurações

5. LIMITAÇÕES
   - Não considera valor do dinheiro no tempo
   - Pode superestimar retorno de projetos longos
   - Não inclui custos de O&M e substituição
{'='*70}
"""
        return relatorio
    
    def _calcular_metricas_economicas(self) -> Dict:
        """Calcula métricas econômicas detalhadas."""
        m = self.modelo
        op = self.resultados['operacao']
        cap = self.resultados['capacidades']
        
        daily_receita_recarga = sum(value(m.tariff_ev) * op['ev_load'][t-1] * value(m.delta_t) 
                                   for t in m.T)
        daily_receita_export = sum(value(m.export_price_factor) * op['grid_price'][t-1] * op['grid_export'][t-1] * value(m.delta_t)
                                  for t in m.T) if self.permitir_exportacao else 0
        daily_custo_import = sum(op['grid_price'][t-1] * op['grid_import'][t-1] * value(m.delta_t)
                                for t in m.T)
        
        operational_days = value(m.operational_days)
        lucro_operacional = operational_days * (daily_receita_recarga + daily_receita_export - daily_custo_import)
        
        capex = (value(m.capex_pv_kw) * cap['pv_kw'] +
                 value(m.capex_bess_kwh) * cap['bess_kwh'] +
                 value(m.capex_trafo_kw) * cap['trafo_kw'])
        
        return {
            'receita_recarga': daily_receita_recarga * operational_days,
            'receita_exportacao': daily_receita_export * operational_days,
            'custo_importacao': daily_custo_import * operational_days,
            'lucro_operacional': lucro_operacional,
            'capex_total': capex,
            'payback': capex / lucro_operacional if lucro_operacional > 0 else float('inf')
        }


# =============================================================================
# 3. SUBPROGRAMA: ANÁLISE DE RISCO DE FALHA NA REDE
# =============================================================================
class RiscoRedeSimulator:
    """
    Simula níveis críticos de disponibilidade da rede elétrica,
    avaliando o impacto de falhas parciais ou totais.
    """
    
    def __init__(self, dados: Dict, niveis_disponibilidade: List[float] = [1.0, 0.8, 0.5, 0.2, 0.0]):
        """
        Parâmetros:
        - niveis_disponibilidade: fração da capacidade do trafo disponível (1.0 = 100%)
        """
        self.dados = dados
        self.niveis = niveis_disponibilidade
        self.resultados_por_nivel = {}
        
    def executar_analise(self, solver_name='gurobi', timeout=60) -> Dict:
        """Executa simulação para cada nível de disponibilidade."""
        for nivel in self.niveis:
            print(f"Simulando nível de disponibilidade: {nivel*100:.0f}%")
            dados_mod = self.dados.copy()
            # Reduz a capacidade efetiva do transformador
            dados_mod['P_trafo_cap_max'] = self.dados['P_trafo_cap_max'] * nivel
            
            simulador = ExportProibidoSimulator(dados_mod)
            simulador.construir_modelo()
            sucesso = simulador.resolver(solver_name, timeout)
            
            if sucesso:
                self.resultados_por_nivel[nivel] = simulador.resultados
            else:
                print(f"Falha na convergência para nível {nivel}")
                self.resultados_por_nivel[nivel] = None
        
        return self.resultados_por_nivel
    
    def visualizar_analise_risco(self, diretorio_saida: Path):
        """Gera visualização da análise de risco multi-nível."""
        if not self.resultados_por_nivel:
            print("Execute executar_analise() primeiro")
            return
        
        diretorio_saida.mkdir(parents=True, exist_ok=True)
        
        # Gráfico 1: Evolução do lucro vs disponibilidade
        fig, ax = plt.subplots(figsize=(12, 7))
        
        niveis = []
        lucros = []
        capex_total = []
        
        for nivel, resultados in self.resultados_por_nivel.items():
            if resultados is not None:
                niveis.append(nivel * 100)
                lucros.append(resultados['economicos']['lucro_operacional_anual'])
                capex_total.append(resultados['economicos']['capex_total'])
        
        ax.plot(niveis, lucros, 'o-', label='Lucro Operacional Anual', color='#2ecc71', linewidth=2, markersize=8)
        ax.plot(niveis, capex_total, 's-', label='CAPEX Total', color='#e74c3c', linewidth=2, markersize=8)
        ax.fill_between(niveis, 0, lucros, alpha=0.2, color='#2ecc71')
        
        ax.set_xlabel('Disponibilidade da Rede Elétrica (%)', fontsize=12)
        ax.set_ylabel('Valor (R$)', fontsize=12)
        ax.set_title('Impacto da Disponibilidade da Rede na Viabilidade Econômica', fontsize=14)
        ax.grid(True, alpha=0.3)
        ax.legend()
        
        # Destacar ponto crítico
        if len(lucros) > 0 and len(niveis) > 0:
            idx_min = np.argmin(lucros) if len(lucros) > 1 else 0
            ax.plot(niveis[idx_min], lucros[idx_min], 'r*', markersize=20, label='Ponto de inflexão')
        
        plt.tight_layout()
        plt.savefig(diretorio_saida / 'risco_rede_impacto_economico.png', dpi=150, bbox_inches='tight')
        plt.close()
        
        # Gráfico 2: Mapa de calor de capacidades instaladas
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
        
        niveis_plot = [n*100 for n, r in self.resultados_por_nivel.items() if r is not None]
        pv_caps = [r['capacidades']['pv_kw'] for r in self.resultados_por_nivel.values() if r is not None]
        bess_caps = [r['capacidades']['bess_kwh'] for r in self.resultados_por_nivel.values() if r is not None]
        
        ax1.bar(niveis_plot, pv_caps, width=8, label='PV (kW)', color='#2ecc71', alpha=0.8)
        ax1.set_xlabel('Disponibilidade da Rede (%)', fontsize=12)
        ax1.set_ylabel('Capacidade PV (kW)', fontsize=12)
        ax1.set_title('Estratégia de Dimensionamento PV', fontsize=12)
        ax1.grid(True, alpha=0.3, axis='y')
        
        ax2.bar(niveis_plot, bess_caps, width=8, label='BESS (kWh)', color='#3498db', alpha=0.8)
        ax2.set_xlabel('Disponibilidade da Rede (%)', fontsize=12)
        ax2.set_ylabel('Capacidade BESS (kWh)', fontsize=12)
        ax2.set_title('Estratégia de Dimensionamento BESS', fontsize=12)
        ax2.grid(True, alpha=0.3, axis='y')
        
        fig.suptitle('Estratégias de Dimensionamento vs Disponibilidade da Rede', fontsize=14)
        plt.tight_layout()
        plt.savefig(diretorio_saida / 'risco_rede_dimensionamento.png', dpi=150, bbox_inches='tight')
        plt.close()
        
        # Gráfico 3: Perfil de operação no pior caso (menor disponibilidade)
        niveis_validos = [(n, r) for n, r in self.resultados_por_nivel.items() if r is not None]
        if niveis_validos:
            pior_nivel, pior_resultado = min(niveis_validos, key=lambda x: x[0])
            
            fig, ax = plt.subplots(figsize=(14, 6))
            
            op = pior_resultado['operacao']
            horas = op['hora']
            pv = np.array(op['pv_gen'])
            grid = np.array(op['grid_import'])
            bess_d = np.array(op['bess_discharge'])
            ev = np.array(op['ev_load'])
            
            # Stack plot para geração
            ax.fill_between(horas, 0, pv, label='PV', alpha=0.7, color='#2ecc71')
            ax.fill_between(horas, pv, pv + grid, label='Rede (limitada)', alpha=0.7, color='#e74c3c')
            ax.fill_between(horas, pv + grid, pv + grid + bess_d, label='BESS Descarga', alpha=0.7, color='#f39c12')
            
            ax.plot(horas, ev, 'o-', label='Demanda EV', color='#3498db', linewidth=2)
            
            ax.set_xlabel('Hora', fontsize=12)
            ax.set_ylabel('Potência (kW)', fontsize=12)
            ax.set_title(f'Operação no Pior Cenário ({pior_nivel*100:.0f}% de disponibilidade)', fontsize=14)
            ax.grid(True, alpha=0.3)
            ax.legend(loc='upper left')
            ax.set_xlim(1, 24)
            
            plt.tight_layout()
            plt.savefig(diretorio_saida / 'risco_rede_pior_cenario_operacao.png', dpi=150, bbox_inches='tight')
            plt.close()
    
    def gerar_relatorio_risco(self) -> str:
        """Gera relatório consolidado da análise de risco."""
        relatorio = f"""
{'='*70}
RELATÓRIO: ANÁLISE DE RISCO DE FALHA NA REDE ELÉTRICA
{'='*70}

1. METODOLOGIA
   - Simulação para diferentes níveis de disponibilidade da rede
   - Níveis analisados: {[f'{n*100:.0f}%' for n in self.niveis]}
   - Cenário base: Exportação proibida, CAPEX simples

2. RESULTADOS POR NÍVEL DE DISPONIBILIDADE
"""
        for nivel, resultados in sorted(self.resultados_por_nivel.items(), reverse=True):
            if resultados is not None:
                relatorio += f"""
   Disponibilidade {nivel*100:.0f}%:
      - PV: {resultados['capacidades']['pv_kw']:.1f} kW
      - BESS: {resultados['capacidades']['bess_kwh']:.1f} kWh
      - Lucro Operacional Anual: R$ {resultados['economicos']['lucro_operacional_anual']:,.2f}
      - Payback: {resultados['economicos']['payback_simples']:.1f} anos
"""
            else:
                relatorio += f"\n   Disponibilidade {nivel*100:.0f}%: SEM SOLUÇÃO VIÁVEL\n"
        
        relatorio += """
3. RECOMENDAÇÕES DE MITIGAÇÃO DE RISCOS
   - Para disponibilidade < 50%: Dimensionar BESS para cobertura mínima de 8 horas
   - Implementar sistema de gerenciamento de demanda (Demand Response)
   - Considerar fonte de geração complementar (diesel ou biogás)
   - Estabelecer contrato com concessionária para garantia de potência mínima

4. ANÁLISE DE SENSIBILIDADE
   - O investimento em BESS torna-se mais crítico à medida que a disponibilidade cai
   - Payback aumenta exponencialmente abaixo de 40% de disponibilidade
   - Recomenda-se seguro de interrupção de fornecimento para casos críticos
{'='*70}
"""
        return relatorio


# =============================================================================
# 4. SUBPROGRAMA: COMPARAÇÃO ENTRE RECURSOS E RISCOS
# =============================================================================
class ComparacaoRecursosRiscos:
    """
    Compara diferentes estratégias de investimento (PV-only, BESS-only, PV+BESS)
    sob diferentes condições de risco (disponibilidade de rede).
    """
    
    def __init__(self, dados_base: Dict):
        self.dados_base = dados_base
        self.resultados_estrategias = {}
        
    def executar_comparacao(self, niveis_disponibilidade: List[float] = [1.0, 0.7, 0.4, 0.1],
                           solver_name='gurobi', timeout=60) -> Dict:
        """
        Compara três estratégias:
        1. Apenas PV (sem BESS)
        2. Apenas BESS (sem PV)
        3. PV + BESS combinado
        """
        estrategias = {
            'Apenas PV': {'pv': True, 'bess': False},
            'Apenas BESS': {'pv': False, 'bess': True},
            'PV + BESS': {'pv': True, 'bess': True}
        }
        
        for nivel in niveis_disponibilidade:
            print(f"\n--- Disponibilidade: {nivel*100:.0f}% ---")
            dados_mod = self.dados_base.copy()
            dados_mod['P_trafo_cap_max'] = self.dados_base['P_trafo_cap_max'] * nivel
            
            for nome, config in estrategias.items():
                print(f"  Simulando: {nome}")
                
                # Ajustar dados conforme estratégia
                dados_estrategia = dados_mod.copy()
                if not config['pv']:
                    dados_estrategia['P_pv_cap_max'] = 0
                if not config['bess']:
                    dados_estrategia['E_bess_cap_max'] = 0
                
                simulador = ExportProibidoSimulator(dados_estrategia)
                simulador.construir_modelo()
                sucesso = simulador.resolver(solver_name, timeout)
                
                if sucesso:
                    resultados = simulador.resultados
                    self.resultados_estrategias[(nivel, nome)] = {
                        'sucesso': True,
                        'capacidades': resultados['capacidades'],
                        'economicos': resultados['economicos'],
                        'soc_horario': resultados['operacao']['soc'] if 'soc' in resultados['operacao'] else None
                    }
                else:
                    self.resultados_estrategias[(nivel, nome)] = {'sucesso': False}
        
        return self.resultados_estrategias
    
    def visualizar_comparacao(self, diretorio_saida: Path):
        """Gera visualização comparativa entre estratégias."""
        if not self.resultados_estrategias:
            print("Execute executar_comparacao() primeiro")
            return
        
        diretorio_saida.mkdir(parents=True, exist_ok=True)
        
        # Extrair dados para visualização
        niveis = sorted(set(n for n, _ in self.resultados_estrategias.keys()))
        estrategias = ['Apenas PV', 'Apenas BESS', 'PV + BESS']
        
        # Gráfico 1: Comparação de lucro vs disponibilidade
        fig, ax = plt.subplots(figsize=(12, 7))
        
        cores = {'Apenas PV': '#3498db', 'Apenas BESS': '#e74c3c', 'PV + BESS': '#2ecc71'}
        marcadores = {'Apenas PV': 'o', 'Apenas BESS': 's', 'PV + BESS': '^'}
        
        for estrategia in estrategias:
            lucros = []
            niveis_validos = []
            for nivel in niveis:
                key = (nivel, estrategia)
                if key in self.resultados_estrategias and self.resultados_estrategias[key]['sucesso']:
                    lucros.append(self.resultados_estrategias[key]['economicos']['lucro_operacional_anual'])
                    niveis_validos.append(nivel * 100)
            
            if niveis_validos:
                ax.plot(niveis_validos, lucros, marker=marcadores[estrategia], 
                       label=estrategia, color=cores[estrategia], linewidth=2, markersize=8)
        
        ax.set_xlabel('Disponibilidade da Rede (%)', fontsize=12)
        ax.set_ylabel('Lucro Operacional Anual (R$)', fontsize=12)
        ax.set_title('Comparação de Estratégias de Investimento vs Risco de Rede', fontsize=14)
        ax.grid(True, alpha=0.3)
        ax.legend()
        
        plt.tight_layout()
        plt.savefig(diretorio_saida / 'comparacao_estrategias_lucro.png', dpi=150, bbox_inches='tight')
        plt.close()
        
        # Gráfico 2: Payback vs disponibilidade
        fig, ax = plt.subplots(figsize=(12, 7))
        
        for estrategia in estrategias:
            paybacks = []
            niveis_validos = []
            for nivel in niveis:
                key = (nivel, estrategia)
                if key in self.resultados_estrategias and self.resultados_estrategias[key]['sucesso']:
                    payback = self.resultados_estrategias[key]['economicos']['payback_simples']
                    if payback != float('inf'):
                        paybacks.append(payback)
                        niveis_validos.append(nivel * 100)
            
            if niveis_validos:
                ax.plot(niveis_validos, paybacks, marker=marcadores[estrategia], 
                       label=estrategia, color=cores[estrategia], linewidth=2, markersize=8)
        
        ax.axhline(5, color='red', linestyle='--', label='Payback desejável (5 anos)', alpha=0.7)
        ax.axhline(10, color='orange', linestyle='--', label='Payback limite (10 anos)', alpha=0.7)
        
        ax.set_xlabel('Disponibilidade da Rede (%)', fontsize=12)
        ax.set_ylabel('Payback (anos)', fontsize=12)
        ax.set_title('Payback das Estratégias vs Risco de Rede', fontsize=14)
        ax.grid(True, alpha=0.3)
        ax.legend()
        
        plt.tight_layout()
        plt.savefig(diretorio_saida / 'comparacao_estrategias_payback.png', dpi=150, bbox_inches='tight')
        plt.close()
        
        # Gráfico 3: Mapa de calor de vantagem relativa (PV+BESS vs alternativas)
        fig, ax = plt.subplots(figsize=(10, 6))
        
        vantagens = []
        for nivel in niveis:
            key_full = (nivel, 'PV + BESS')
            key_pv = (nivel, 'Apenas PV')
            key_bess = (nivel, 'Apenas BESS')
            
            vantagem_pv = None
            vantagem_bess = None
            
            if (key_full in self.resultados_estrategias and self.resultados_estrategias[key_full]['sucesso'] and
                key_pv in self.resultados_estrategias and self.resultados_estrategias[key_pv]['sucesso']):
                lucro_full = self.resultados_estrategias[key_full]['economicos']['lucro_operacional_anual']
                lucro_pv = self.resultados_estrategias[key_pv]['economicos']['lucro_operacional_anual']
                vantagem_pv = ((lucro_full - lucro_pv) / lucro_pv * 100) if lucro_pv > 0 else 0
            
            if (key_full in self.resultados_estrategias and self.resultados_estrategias[key_full]['sucesso'] and
                key_bess in self.resultados_estrategias and self.resultados_estrategias[key_bess]['sucesso']):
                lucro_full = self.resultados_estrategias[key_full]['economicos']['lucro_operacional_anual']
                lucro_bess = self.resultados_estrategias[key_bess]['economicos']['lucro_operacional_anual']
                vantagem_bess = ((lucro_full - lucro_bess) / lucro_bess * 100) if lucro_bess > 0 else 0
            
            vantagens.append([vantagem_pv if vantagem_pv is not None else 0, 
                              vantagem_bess if vantagem_bess is not None else 0])
        
        vantagens = np.array(vantagens)
        
        im = ax.imshow(vantagens.T, cmap='RdYlGn', aspect='auto', origin='lower',
                       extent=[0, len(niveis)-1, -0.5, 1.5])
        
        ax.set_xticks(range(len(niveis)))
        ax.set_xticklabels([f'{n*100:.0f}%' for n in niveis])
        ax.set_yticks([0, 1])
        ax.set_yticklabels(['vs Apenas PV', 'vs Apenas BESS'])
        ax.set_xlabel('Disponibilidade da Rede', fontsize=12)
        ax.set_title('Vantagem Relativa do PV+BESS (%)', fontsize=14)
        
        # Adicionar valores nas células
        for i in range(len(niveis)):
            for j in range(2):
                if vantagens[i, j] != 0:
                    text = ax.text(i, j, f'{vantagens[i, j]:.0f}%', ha="center", va="center", color="black")
        
        plt.colorbar(im, ax=ax, label='Vantagem (%)')
        plt.tight_layout()
        plt.savefig(diretorio_saida / 'comparacao_estrategias_vantagem_relativa.png', dpi=150, bbox_inches='tight')
        plt.close()
    
    def gerar_relatorio_comparativo(self) -> str:
        """Gera relatório comparativo entre estratégias."""
        relatorio = f"""
{'='*70}
RELATÓRIO: COMPARAÇÃO ENTRE RECURSOS E RISCOS
{'='*70}

1. OBJETIVO DA COMPARAÇÃO
   Avaliar diferentes estratégias de investimento em microrrede para eletroposto:
   - Apenas PV
   - Apenas BESS
   - PV + BESS (combinado)

2. METODOLOGIA
   - Simulação para diferentes níveis de disponibilidade da rede
   - Indicadores: Lucro operacional anual, Payback, CAPEX
   - Cenário base: Exportação proibida

3. RESULTADOS CONSOLIDADOS
"""
        # Organizar resultados por nível de disponibilidade
        niveis = sorted(set(n for n, _ in self.resultados_estrategias.keys()))
        
        for nivel in niveis:
            relatorio += f"\n   Disponibilidade: {nivel*100:.0f}%\n"
            relatorio += "   " + "-" * 50 + "\n"
            
            for estrategia in ['Apenas PV', 'Apenas BESS', 'PV + BESS']:
                key = (nivel, estrategia)
                if key in self.resultados_estrategias and self.resultados_estrategias[key]['sucesso']:
                    r = self.resultados_estrategias[key]
                    relatorio += f"""
      {estrategia}:
         - PV: {r['capacidades']['pv_kw']:.1f} kW | BESS: {r['capacidades']['bess_kwh']:.1f} kWh
         - Lucro anual: R$ {r['economicos']['lucro_operacional_anual']:,.2f}
         - CAPEX: R$ {r['economicos']['capex_total']:,.2f}
         - Payback: {r['economicos']['payback_simples']:.1f} anos
"""
                else:
                    relatorio += f"\n      {estrategia}: SEM SOLUÇÃO VIÁVEL\n"

        relatorio += """
4. RECOMENDAÇÕES ESTRATÉGICAS
   - Alta disponibilidade (>70%): Apenas PV é suficiente e mais barato
   - Média disponibilidade (40-70%): PV+BESS oferece melhor relação custo-benefício
   - Baixa disponibilidade (<40%): BESS é crítico, PV+BESS é recomendado
   - Disponibilidade muito baixa (<20%): Necessário considerar geração complementar (diesel)

5. ANÁLISE DE RISCO-RETORNO
   - A estratégia PV+BESS apresenta maior resiliência a variações de disponibilidade
   - Payback mais alto que PV-only em cenários favoráveis, mas protege contra quedas
   - Recomendação: Implementar PV+BESS dimensionado para pior cenário esperado
{'='*70}
"""
        return relatorio


# =============================================================================
# FUNÇÃO PRINCIPAL: EXEMPLO DE USO INTEGRADO
# =============================================================================

def exemplo_dados() -> Dict:
    """Retorna dados de exemplo para as simulações."""
    return {
        'delta_t': 1,
        'operational_days': 365,
        'tariff_ev': 1.60,  # BRL/kWh
        'export_price_factor': 0.70,  # Fator de desconto para exportação (70% do preço de venda)
        
        # CAPEX
        'capex_pv_kw': 1200,  # BRL/kW
        'capex_bess_kwh': 700,  # BRL/kWh
        'capex_trafo_kw': 1000,  # BRL/kW
        
        # Técnicos BESS
        'eta_charge': 0.91,
        'eta_discharge': 0.91,
        'soc_min_frac': 0.05,
        'soc_max_frac': 0.95,
        'soc_initial_frac': 0.50,
        'c_rate_charge': 1.0,
        'c_rate_discharge': 1.0,
        
        # Limites máximos
        'E_bess_cap_max': 2000,
        'P_pv_cap_max': 1000,
        'P_trafo_cap_max': 500,
        
        # Séries horárias (exemplo)
        'irradiance_cf': {
            1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.1, 6: 0.3, 7: 0.5, 8: 0.7,
            9: 0.9, 10: 1.0, 11: 0.95, 12: 0.9, 13: 0.85, 14: 0.8, 15: 0.7, 16: 0.5,
            17: 0.3, 18: 0.1, 19: 0.0, 20: 0.0, 21: 0.0, 22: 0.0, 23: 0.0, 24: 0.0
        },
        'grid_price': {
            1: 0.25, 2: 0.25, 3: 0.25, 4: 0.25, 5: 0.25, 6: 0.25, 7: 0.54, 8: 0.88,
            9: 0.88, 10: 0.88, 11: 0.88, 12: 0.54, 13: 0.54, 14: 0.54, 15: 0.54, 16: 0.54,
            17: 0.88, 18: 1.10, 19: 1.10, 20: 1.10, 21: 0.88, 22: 0.54, 23: 0.25, 24: 0.25
        },
        'P_ev_load_kw': {
            1: 35, 2: 28, 3: 22, 4: 20, 5: 25, 6: 48, 7: 72, 8: 98, 9: 105, 10: 115,
            11: 110, 12: 100, 13: 95, 14: 90, 15: 98, 16: 112, 17: 126, 18: 135, 19: 128,
            20: 116, 21: 94, 22: 72, 23: 54, 24: 42
        }
    }


def main():
    """Função principal demonstrando uso dos subprogramas."""
    print("="*70)
    print("SUBPROGRAMAS PARA ANÁLISE DE MICRORREDES EM ELETROPOSTOS")
    print("="*70)
    
    dados = exemplo_dados()
    diretorio_saida = Path("./resultados_microrredes")
    diretorio_saida.mkdir(exist_ok=True)
    
    # =========================================================
    # 1. CENÁRIO: EXPORTAÇÃO PROIBIDA
    # =========================================================
    print("\n1. Executando Cenário: Exportação Proibida...")
    export_proibido = ExportProibidoSimulator(dados)
    export_proibido.construir_modelo()
    if export_proibido.resolver():
        export_proibido.visualizar(diretorio_saida / "export_proibido")
        print(export_proibido.gerar_relatorio())
        with open(diretorio_saida / "relatorio_export_proibido.txt", 'w', encoding='utf-8') as f:
            f.write(export_proibido.gerar_relatorio())
    
    # =========================================================
    # 2. CENÁRIO: CAPEX SIMPLES COM EXPORTAÇÃO PERMITIDA
    # =========================================================
    print("\n2. Executando Cenário: CAPEX Simples com Exportação...")
    capex_com_export = CapexSimplesSimulator(dados, permitir_exportacao=True)
    capex_com_export.construir_modelo()
    if capex_com_export.resolver():
        capex_com_export.visualizar(diretorio_saida / "capex_com_export")
        print(capex_com_export.gerar_relatorio())
        with open(diretorio_saida / "relatorio_capex_com_export.txt", 'w', encoding='utf-8') as f:
            f.write(capex_com_export.gerar_relatorio())
    
    # =========================================================
    # 3. ANÁLISE DE RISCO DE FALHA NA REDE
    # =========================================================
    print("\n3. Executando Análise de Risco de Falha na Rede...")
    risco_analise = RiscoRedeSimulator(dados, niveis_disponibilidade=[1.0, 0.7, 0.4, 0.2, 0.1])
    risco_analise.executar_analise()
    risco_analise.visualizar_analise_risco(diretorio_saida / "analise_risco")
    print(risco_analise.gerar_relatorio_risco())
    with open(diretorio_saida / "relatorio_analise_risco.txt", 'w', encoding='utf-8') as f:
        f.write(risco_analise.gerar_relatorio_risco())
    
    # =========================================================
    # 4. COMPARAÇÃO ENTRE RECURSOS E RISCOS
    # =========================================================
    print("\n4. Executando Comparação entre Recursos e Riscos...")
    comparacao = ComparacaoRecursosRiscos(dados)
    comparacao.executar_comparacao(niveis_disponibilidade=[1.0, 0.7, 0.4, 0.2])
    comparacao.visualizar_comparacao(diretorio_saida / "comparacao_estrategias")
    print(comparacao.gerar_relatorio_comparativo())
    with open(diretorio_saida / "relatorio_comparativo_estrategias.txt", 'w', encoding='utf-8') as f:
        f.write(comparacao.gerar_relatorio_comparativo())
    
    print(f"\n{'='*70}")
    print(f"Todos os resultados foram salvos em: {diretorio_saida.absolute()}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
