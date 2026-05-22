# DOCUMENTAÇÃO ESTRUTURANTE - SUBPROGRAMAS PARA MICRORREDES EM ELETROPOSTOS

**Data da compilação:** 2026-05-21  
**Versão da documentação:** 1.0  
**Base metodológica:** Mapeamento_Estrutura_Modelo.md  
**Repositórios base referenciados:**  
- `EVCS-PV-BESS/main.py` (modelo abstrato completo)  
- `cenariosEVCS/rgridCAPEXSimple.py` (modelo concreto CAPEX simples)  

**Arquivos derivados:**  
- Subprograma 1: ExportProibidoSimulator  
- Subprograma 2: CapexSimplesSimulator  
- Subprograma 3: RiscoRedeSimulator  
- Subprograma 4: ComparacaoRecursosRiscos  

---

## SUMÁRIO DA DOCUMENTAÇÃO ESTRUTURANTE POR SUBPROGRAMA

Para cada subprograma, os seguintes itens são mapeados:
1. Caracterização do Sistema Energético
2. Horizonte Temporal e Tipo de Operação
3. Natureza da Função Objetivo
4. Modelagem do BESS (Battery Energy Storage System)
5. Modelagem da Demanda EV
6. Restrição Contratual de Potência / Disponibilidade da Rede
7. Tipo de Formulação Matemática
8. Adaptações em relação ao modelo base (justificativas)
9. Lacunas para evolução (versionamento futuro)
10. Correspondência código ↔ itens científicos

---

## SUBPROGRAMA 1: ExportProibidoSimulator

### 1. Caracterização do Sistema Energético
**Tipo de sistema:** Microgrid conectada à rede **SOMENTE com importação** (exportação proibida)

**Evidência no código:**
```python
# Ausência da variável P_grid_export no modelo
# Restrição do transformador apenas para importação
def trafo_limit_rule(m, t):
    return m.P_grid_import[t] <= m.P_trafo_cap
model.TrafoLimit = Constraint(model.T, rule=trafo_limit_rule)

# Balanço de energia SEM termo de exportação
def energy_balance_rule(m, t):
    return (m.P_pv_gen[t] + m.P_grid_import[t] + m.P_bess_discharge[t] == 
            m.P_ev_load_kw[t] + m.P_bess_charge[t])
```

**Adaptação em relação ao modelo base:**  
O modelo base `main.py` permite exportação via parâmetro `allow_grid_export`. Este subprograma **elimina** a variável de exportação e a restrição correspondente, adequando-se a cenários regulatórios ou contratuais que proíbem injeção na rede.

### 2. Horizonte Temporal e Tipo de Operação
**Evidência no código:**
```python
model.T = RangeSet(1, 24)  # 24 horas
model.delta_t = Param(initialize=dados['delta_t'], within=NonNegativeReals)  # resolução horária
```
**Caracterização científica:**
- Operação diária representativa (24h)
- Modelo determinístico
- Resolução temporal horária (delta_t = 1 hora por padrão)
- Anualização via fator `operational_days` (365 dias)

### 3. Natureza da Função Objetivo
**Evidência no código:**
```python
def objective_rule(m):
    daily_revenue = sum(m.tariff_ev * m.P_ev_load_kw[t] * m.delta_t for t in m.T)
    daily_cost = sum(m.grid_price[t] * m.P_grid_import[t] * m.delta_t for t in m.T)
    annual_profit = m.operational_days * (daily_revenue - daily_cost)
    capex = (m.capex_pv_kw * m.P_pv_cap + 
             m.capex_bess_kwh * m.E_bess_cap + 
             m.capex_trafo_kw * m.P_trafo_cap)
    return annual_profit - capex
model.Obj = Objective(rule=objective_rule, sense=maximize)
```
**Critério de otimização:** Maximização do Lucro Operacional Anual menos CAPEX total.  
**Interpretação econômica:** Projeto prioriza retorno sobre investimento sem considerar valor do dinheiro no tempo.

**Adaptação em relação ao modelo base:**  
O `main.py` possui dois modos de objetivo (simplificado e anualizado com CRF+O&M). Este subprograma adota **apenas o modo simplificado** (lucro operacional anual - CAPEX), compatível com o `rgridCAPEXSimple.py`.

### 4. Modelagem do BESS
**4.1 Dinâmica do SOC:**
```python
def soc_balance_rule(m, t):
    if t == m.T.first():
        return m.SOC[t] == m.soc_initial_frac * m.E_bess_cap + m.delta_t * (
            m.eta_charge * m.P_bess_charge[t] - m.P_bess_discharge[t] / m.eta_discharge)
    t_prev = m.T.prev(t)
    return m.SOC[t] == m.SOC[t_prev] + m.delta_t * (
        m.eta_charge * m.P_bess_charge[t] - m.P_bess_discharge[t] / m.eta_discharge)
```
**4.2 Limites operacionais:**
```python
def soc_min_rule(m, t):
    return m.SOC[t] >= m.soc_min_frac * m.E_bess_cap
def soc_max_rule(m, t):
    return m.SOC[t] <= m.soc_max_frac * m.E_bess_cap
```
**4.3 Exclusividade carga/descarga (MILP):**
```python
def bess_charge_mode_rule(m, t):
    return m.P_bess_charge[t] <= m.c_rate_charge * m.E_bess_cap_max * m.y_bess[t]
def bess_discharge_mode_rule(m, t):
    return m.P_bess_discharge[t] <= m.c_rate_discharge * m.E_bess_cap_max * (1 - m.y_bess[t])
```
**4.4 Condição cíclica:**
```python
def terminal_soc_rule(m):
    return m.SOC[m.T.last()] == m.soc_initial_frac * m.E_bess_cap
```
**Classificação científica:** Modelo linear de balanço energético com eficiências constantes, sem degradação, sem modelagem eletroquímica. Capacidade de potência limitada por c-rate.

### 5. Modelagem da Demanda EV
**Evidência no código:**
```python
model.P_ev_load_kw = Param(model.T, initialize=dados['P_ev_load_kw'], within=NonNegativeReals)
```
**Características:**
- Perfil determinístico agregado por hora
- 24 valores fixos representando um dia típico
- **Não modela:** estocasticidade de chegada, filas de espera, controle individual de carregadores

**Adaptação:** Mantém a mesma abordagem dos modelos base, adequada para estudos de dimensionamento e despacho diário.

### 6. Restrição Contratual de Potência / Disponibilidade da Rede
**Evidência no código:**
```python
def trafo_limit_rule(m, t):
    return m.P_grid_import[t] <= m.P_trafo_cap
model.TrafoLimit = Constraint(model.T, rule=trafo_limit_rule)
```
**Interpretação:** A capacidade do transformador (`P_trafo_cap`) é uma variável de decisão, mas limitada superiormente por `P_trafo_cap_max`. Representa o limite contratual de demanda junto à concessionária.

### 7. Tipo de Formulação Matemática
**Evidências:**
- Variáveis contínuas não negativas: `Var(within=NonNegativeReals)`
- Variável binária: `y_bess = Var(model.T, within=Binary)`
- Restrições lineares
- Função objetivo linear

**Classificação:** MILP (Mixed Integer Linear Programming)

**Solver sugerido:** Gurobi (padrão) ou CBC (alternativa open-source)

### 8. Adaptações em relação ao modelo base (`main.py` e `rgridCAPEXSimple.py`)

| Aspecto | Modelo base (main.py) | ExportProibidoSimulator | Justificativa |
|---------|----------------------|--------------------------|----------------|
| Exportação | Permitida via `allow_grid_export` | **Eliminada** | Cenários com restrição regulatória ou contratual |
| Objetivo | Duplo (simplificado/anualizado) | **Apenas simplificado** | Alinhamento com CAPEX simples, maior clareza para investidores |
| Variáveis | `P_grid_export` presente | **Removida** | Redução de complexidade, foco em autossuficiência |
| Relatório | Inclui comparação entre modos | **Focado em indicadores de risco** | Ênfase em análise de disponibilidade da rede |

### 9. Lacunas para Evolução (Versionamento futuro)

| Lacuna identificada | Prioridade | Direção de evolução |
|--------------------|------------|---------------------|
| Degradação do BESS por ciclo | Alta | Incluir custo de degradação proporcional à energia processada (modelo v1.1) |
| Demanda EV estocástica | Média | Substituir perfil determinístico por cenários probabilisticos (v2.0) |
| Tarifa por demanda máxima mensal | Média | Adicionar termo de penalização por pico de importação (v1.2) |
| CAPEX anualizado com CRF | Baixa | Para compatibilidade com horizonte multianual (v3.0) |
| Emissões de CO2 | Baixa | Incluir fator de emissão na função objetivo multiobjetivo (v2.0) |

### 10. Correspondência Código ↔ Itens Científicos

| Item Necessário | Evidência no Código |
|-----------------|---------------------|
| Tipo de sistema | Ausência de `P_grid_export` + restrição `TrafoLimit` |
| Horizonte temporal | `RangeSet(1, 24)` + `operational_days` |
| Natureza da operação | Modelo determinístico, dados fixos |
| Objetivo econômico | `annual_profit - capex` |
| BESS simplificado | `SOCBalance` + `SOCMin`/`Max` + `y_bess` |
| Demanda EV agregada | `Param(model.T, initialize=P_ev_load_kw)` |
| Restrição de rede | `trafo_limit_rule` |
| Tipo matemático | MILP (`Binary` + linear) |
| Capacidade de investimento | `P_pv_cap`, `E_bess_cap`, `P_trafo_cap` |

---

## SUBPROGRAMA 2: CapexSimplesSimulator

### 1. Caracterização do Sistema Energético
**Tipo de sistema:** Microgrid conectada à rede **com ou sem exportação** (configurável)

**Evidência no código:**
```python
def __init__(self, dados: Dict, permitir_exportacao: bool = False):
    self.permitir_exportacao = permitir_exportacao
    # ...
    if self.permitir_exportacao:
        model.P_grid_export = Var(model.T, within=NonNegativeReals)
```

**Adaptação:** Diferente do `ExportProibidoSimulator`, este subprograma **flexibiliza** a exportação via parâmetro de inicialização, permitindo comparação direta entre cenários.

### 2. Horizonte Temporal e Tipo de Operação
Idêntico ao Subprograma 1: 24h horárias, determinístico, anualização por fator.

### 3. Natureza da Função Objetivo
**Evidência no código:**
```python
def objective_rule(m):
    daily_revenue_ev = sum(m.tariff_ev * m.P_ev_load_kw[t] * m.delta_t for t in m.T)
    daily_revenue_export = (sum(m.export_price_factor * m.grid_price[t] * m.P_grid_export[t] * m.delta_t 
                               for t in m.T) if self.permitir_exportacao else 0)
    daily_cost_import = sum(m.grid_price[t] * m.P_grid_import[t] * m.delta_t for t in m.T)
    annual_profit = m.operational_days * (daily_revenue_ev + daily_revenue_export - daily_cost_import)
    capex = (m.capex_pv_kw * m.P_pv_cap + m.capex_bess_kwh * m.E_bess_cap + m.capex_trafo_kw * m.P_trafo_cap)
    return annual_profit - capex
```

**Característica distintiva:** Inclui termo de receita de exportação quando habilitado, com fator redutor `export_price_factor` (padrão 0.7), capturando compensação menos favorável que a tarifa de compra.

### 4. Modelagem do BESS
**Idêntica ao Subprograma 1**, com a mesma dinâmica de SOC, limites e exclusividade carga/descarga.

**Observação:** A implementação original do `rgridCAPEXSimple.py` possui variáveis auxiliares `E_bess_cap_charge` e `E_bess_cap_discharge` para linearização Big-M. Este subprograma **simplificou** para as restrições diretas de potência, mantendo a mesma capacidade expressiva.

### 5. Modelagem da Demanda EV
Idêntica ao Subprograma 1.

### 6. Restrição Contratual de Potência
**Evidência no código:**
```python
def trafo_import_rule(m, t):
    return m.P_grid_import[t] <= m.P_trafo_cap
if self.permitir_exportacao:
    def trafo_export_rule(m, t):
        return m.P_grid_export[t] <= m.P_trafo_cap
```
**Diferencial:** O mesmo limite de transformador se aplica tanto à importação quanto à exportação (simetria de capacidade).

### 7. Tipo de Formulação Matemática
MILP, mesma classificação do Subprograma 1.

### 8. Adaptações em relação ao modelo base (`rgridCAPEXSimple.py`)

| Aspecto | rgridCAPEXSimple.py | CapexSimplesSimulator | Justificativa |
|---------|--------------------|------------------------|----------------|
| Estrutura | Modelo concreto fixo | **Classe parametrizável** | Permitir reuso para diferentes cenários |
| Variáveis auxiliares BESS | `E_bess_cap_charge/discharge` | **Removidas** | Simplificação sem perda de modelagem (Big-M direto) |
| Exportação | Fixa (proibida no arquivo) | **Configurável** | Comparação lado a lado entre cenários |
| Visualização | 7 figuras fixas | **2 figuras adaptativas** | Foco em comparabilidade entre configurações |

### 9. Lacunas para Evolução
As mesmas do Subprograma 1, com ênfase adicional em:
- Modelagem de **tarifa branca** com três patamares horários (já parcialmente presente nos dados de exemplo)
- **Incerteza na geração PV** (irradiância determinística)

### 10. Correspondência Código ↔ Itens Científicos

| Item | Evidência |
|------|-----------|
| Sistema com/sem exportação | Parâmetro `permitir_exportacao` + criação condicional de `P_grid_export` |
| Objetivo com exportação | Inclusão condicional de `daily_revenue_export` |
| Limite simétrico do trafo | Mesmo `P_trafo_cap` para importação e exportação |
| Fator de compensação | `export_price_factor` aplicado à receita de exportação |

---

## SUBPROGRAMA 3: RiscoRedeSimulator

### 1. Caracterização do Sistema Energético
**Tipo de sistema:** Microgrid conectada à rede com **disponibilidade variável** do transformador

**Evidência no código:**
```python
def executar_analise(self, solver_name='gurobi', timeout=60) -> Dict:
    for nivel in self.niveis:
        dados_mod = self.dados.copy()
        dados_mod['P_trafo_cap_max'] = self.dados['P_trafo_cap_max'] * nivel
        simulador = ExportProibidoSimulator(dados_mod)
```

**Inovação científica:** Introduz o conceito de **nível de disponibilidade da rede** como um fator redutor da capacidade do transformador, simulando cenários de falha parcial ou restrição de fornecimento.

### 2. Horizonte Temporal e Tipo de Operação
Mantém o horizonte de 24h determinístico, mas realiza **múltiplas execuções** (varrendo diferentes níveis de disponibilidade). Cada execução é independente e representa um cenário de infraestrutura de rede diferente.

### 3. Natureza da Função Objetivo
Idêntica ao Subprograma 1 (Maximização Lucro - CAPEX), aplicada a cada nível de disponibilidade.

**Diferencial metodológico:** A função objetivo não é alterada pelo nível de disponibilidade; o impacto é capturado indiretamente via restrição de capacidade máxima do transformador.

### 4. Modelagem do BESS
Idêntica ao Subprograma 1. O BESS atua como **elemento de mitigação** da indisponibilidade da rede.

### 5. Modelagem da Demanda EV
Idêntica.

### 6. Restrição Contratual de Potência com Disponibilidade Variável
**Evidência no código:**
```python
dados_mod['P_trafo_cap_max'] = self.dados['P_trafo_cap_max'] * nivel
```
**Interpretação científica:** O parâmetro `P_trafo_cap_max` deixa de ser um limite físico fixo e passa a representar a **capacidade contratada efetivamente disponível** após contingências. Isso modela:

- Redução de fornecimento por racionamento
- Falhas parciais em equipamentos de proteção
- Estratégias de Demand-Side Management da concessionária

### 7. Tipo de Formulação Matemática
Múltiplas execuções de MILP independentes (não é um problema multi-cenário acoplado).

**Limitação metodológica atual:** Os cenários são resolvidos separadamente, sem considerar a probabilidade de ocorrência ou otimização robusta.

### 8. Adaptações em relação aos modelos base

| Aspecto | Modelos base | RiscoRedeSimulator | Justificativa |
|---------|-------------|--------------------|----------------|
| Tratamento da rede | Capacidade fixa | **Capacidade variável (paramétrica)** | Avaliar sensibilidade a contingências |
| Análise | Determinística | **Análise de cenários** | Quantificar impacto da indisponibilidade |
| Saída | Um resultado ótimo | **Curva de resposta** vs disponibilidade | Visualizar ponto de inflexão do projeto |
| Inovação | — | **Conceito de "nível de disponibilidade"** | Metodologia original para análise de risco |

### 9. Lacunas para Evolução

| Lacuna | Direção de evolução |
|--------|---------------------|
| Cenários independentes | Implementar otimização robusta ou estocástica com árvore de cenários (v2.0) |
| Sem probabilidades | Associar cada nível de disponibilidade a uma probabilidade de ocorrência (v1.1) |
| Resposta apenas do trafo | Incluir indisponibilidade também na geração PV (falha de inversores) (v1.2) |
| Horizonte curto | Estender para análise de planejamento multi-anual com diferentes perfis de falha (v3.0) |

### 10. Correspondência Código ↔ Itens Científicos

| Item | Evidência |
|------|-----------|
| Disponibilidade variável | `P_trafo_cap_max = original * nivel` |
| Análise multi-cenário | Loop `for nivel in self.niveis` |
| Indicador de risco | Geração de gráfico "lucro vs disponibilidade" |
| Ponto de inflexão | Identificação visual no gráfico + relatório textual |

---

## SUBPROGRAMA 4: ComparacaoRecursosRiscos

### 1. Caracterização do Sistema Energético
**Tipo de sistema:** Microgrid conectada à rede com **diferentes composições de ativos** (PV-only, BESS-only, PV+BESS)

**Evidência no código:**
```python
estrategias = {
    'Apenas PV': {'pv': True, 'bess': False},
    'Apenas BESS': {'pv': False, 'bess': True},
    'PV + BESS': {'pv': True, 'bess': True}
}
# Aplicação
if not config['pv']:
    dados_estrategia['P_pv_cap_max'] = 0
if not config['bess']:
    dados_estrategia['E_bess_cap_max'] = 0
```

**Inovação científica:** Permite isolar o **benefício marginal** de cada tecnologia ao forçar a capacidade máxima de uma delas a zero, gerando cenários contrafactuais.

### 2. Horizonte Temporal e Tipo de Operação
Mantém 24h determinístico, com múltiplas execuções varrendo:
- Estratégias de composição (3 níveis)
- Níveis de disponibilidade da rede (configurável, padrão 4 níveis)

Total: 12 execuções do modelo MILP.

### 3. Natureza da Função Objetivo
Idêntica ao Subprograma 1 para todas as execuções.

**Diferencial de análise:** A mesma função objetivo é aplicada a diferentes conjuntos de variáveis de decisão (quando `P_pv_cap_max=0`, a variável `P_pv_cap` existe mas fixa em zero, reduzindo o espaço de busca).

### 4. Modelagem do BESS
Idêntica, mas condicional à estratégia. Na estratégia "Apenas PV", as restrições do BESS são mantidas no modelo, mas a capacidade máxima é zero, efetivamente desligando o armazenamento.

### 5. Modelagem da Demanda EV
Idêntica.

### 6. Restrição Contratual com Disponibilidade Variável
Combina a abordagem do Subprograma 3 (varredura de disponibilidade) com a do Subprograma 4 (varredura de estratégias).

### 7. Tipo de Formulação Matemática
Múltiplas execuções MILP (12 execuções no cenário padrão).

### 8. Adaptações em relação aos modelos base

| Aspecto | Modelos base | ComparacaoRecursosRiscos | Justificativa |
|---------|-------------|--------------------------|----------------|
| Escopo de análise | Uma configuração | **Múltiplas configurações sistemáticas** | Avaliar qual tecnologia é mais crítica |
| Isolamento de efeitos | Não há | **Forçar capacidades a zero** | Criar baseline contrafactual |
| Saída | Resultado ótimo | **Comparação entre estratégias** | Apoiar decisão de investimento |
| Métrica de vantagem | — | **Vantagem relativa (%)** | Quantificar ganho marginal do PV+BESS |

### 9. Lacunas para Evolução

| Lacuna | Direção de evolução |
|--------|---------------------|
| Estratégias fixas | Permitir combinações parciais (ex: 50% PV + 50% BESS do ótimo) (v2.0) |
| Sem análise estatística | Adicionar bootstrap ou teste de significância entre estratégias (v1.1) |
| Custos independentes | Incluir economia de escala (CAPEX decrescente com capacidade) (v2.0) |

### 10. Correspondência Código ↔ Itens Científicos

| Item | Evidência |
|------|-----------|
| Comparação controlada | `P_pv_cap_max=0` ou `E_bess_cap_max=0` |
| Matriz de experimentos | Loop aninhado: `niveis_disponibilidade` x `estrategias` |
| Métrica de vantagem | `(lucro_full - lucro_parcial) / lucro_parcial * 100` |
| Visualização comparativa | Mapa de calor + gráficos de linha por estratégia |

---

## DOCUMENTAÇÃO DO CONJUNTO INTEGRADO

### A. Estrutura de Versionamento Científico dos Quatro Subprogramas

| Versão | Subprograma | Principais características científicas | Lacunas endereçadas |
|--------|-------------|----------------------------------------|---------------------|
| **v1.0** | ExportProibidoSimulator | Exportação proibida, CAPEX simples, MILP determinístico | Baseline para cenários regulados |
| **v1.1** | CapexSimplesSimulator | Exportação configurável, comparação direta | Adiciona flexibilidade de cenário |
| **v1.2** | RiscoRedeSimulator | Disponibilidade paramétrica da rede, análise de sensibilidade | Endereça risco de falha |
| **v2.0** | ComparacaoRecursosRiscos | Análise sistemática de estratégias, métricas de vantagem relativa | Endereça decisão de composição de ativos |
| **v3.0 (futuro)** | Integração multi-objetivo | Inclusão de degradação, emissões, incerteza estocástica | Lacunas identificadas nos subprogramas |

### B. Correspondência entre Subprogramas e Exigências Metodológicas

| Exigência Metodológica | ExportProibido | CapexSimples | RiscoRede | Comparacao |
|------------------------|:--------------:|:------------:|:---------:|:----------:|
| Registro formal rastreável | ✅ | ✅ | ✅ | ✅ |
| Versionamento científico | ✅ (v1.0) | ✅ (v1.1) | ✅ (v1.2) | ✅ (v2.0) |
| Fundamentação para publicações | ✅ | ✅ | ✅ | ✅ |
| Comunicação interdisciplinar | ✅ | ✅ | ✅ | ✅ |
| Integração com repositório base | ✅ | ✅ | ✅ (inovação) | ✅ (inovação) |
| Elo documental com modelos base | ✅ | ✅ | ✅ | ✅ |

### C. Mapeamento Integrado: Código ↔ Itens Científicos (Todos Subprogramas)

| Item Científico | ExportProibido | CapexSimples | RiscoRede | Comparacao |
|----------------|----------------|--------------|-----------|------------|
| Sistema conectado | Sim (só importa) | Sim (import/export) | Sim (variável) | Sim (por estratégia) |
| Horizonte 24h | Sim | Sim | Sim | Sim (múltiplas execuções) |
| Determinístico | Sim | Sim | Multi-cenário | Multi-cenário |
| Objetivo: Max(Lucro - CAPEX) | Sim | Sim (+exportação) | Sim (por nível) | Sim (por estratégia) |
| BESS: SOC linear + binárias | Sim | Sim | Sim | Sim (condicional) |
| Demanda EV fixa | Sim | Sim | Sim | Sim |
| Restrição trafo | Import ≤ cap | Imp/Exp ≤ cap | Cap variável | Cap variável + estratégia |
| Tipo MILP | Sim | Sim | Múltiplos MILP | Múltiplos MILP |

### D. Conclusão Técnica Integrada

**Os quatro subprogramas implementados atendem aos critérios de registro formal e versionamento científico conforme o modelo `Mapeamento_Estrutura_Modelo.md`:**

1. **Rastreabilidade explícita:** Cada subprograma documenta, no próprio código e nesta documentação, a correspondência entre implementação e exigências metodológicas.

2. **Versionamento hierárquico:** Os subprogramas representam uma evolução natural do modelo base:
   - v1.0 → Exportação proibida (aderência ao `rgridCAPEXSimple.py`)
   - v1.1 → Exportação configurável (flexibilidade)
   - v1.2 → Risco paramétrico (inovação metodológica)
   - v2.0 → Comparação sistemática de estratégias (análise de decisão)

3. **Lacunas identificadas:** Cada subprograma explicita suas limitações atuais (degradação, estocasticidade, emissões) e direciona futuras versões.

4. **Integração com repositório base:** O elo documental com `EVCS-PV-BESS/main.py` e `cenariosEVCS/rgridCAPEXSimple.py` é mantido via:
   - Referência explícita nos comentários iniciais
   - Uso das mesmas convenções de nomenclatura
   - Preservação das estruturas de dados de entrada

5. **Reprodutibilidade:** A combinação de código fonte, dados de exemplo (função `exemplo_dados()`) e esta documentação estruturante permite que terceiros reproduzam, auditam e estendam o trabalho.

**Recomendação para uso acadêmico:**  
Inclua este arquivo (`Mapeamento_Estrutura_Subprogramas.md`) na raiz do repositório dos subprogramas, ao lado do código-fonte. Em projetos derivados ou adaptações, utilize-o para explicitar justificativas de mudanças e manter o vínculo metodológico com os modelos base originais, exatamente como preconizado no documento de referência fornecido.

---

**Data da compilação:** 2026-05-21  
**Responsável técnico:** Análise baseada nos códigos de leticiasdrummond  
**Próxima revisão:** Após implementação das lacunas de v2.0 (degradação, estocasticidade)
