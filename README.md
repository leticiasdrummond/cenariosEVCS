
# 📁 DOCUMENTAÇÃO ESTRUTURANTE – MODELOS DE MICRORREDES PARA ELETROPOSTOS

**Data da compilação:** 2026-05-21  
**Versão da documentação:** 1.1 (baseada em https://github.com/leticiasdrummond/Modelos-Base/blob/895d3838b742abc53a966be746127c8b0678af34/Mapa%20Mental%20Otimiza%C3%A7%C3%A3o.html)  
**Base metodológica:** `Mapeamento_Estrutura_Modelo.md`  
**Repositórios base referenciados:**  
- `EVCS-PV-BESS/main.py` (modelo abstrato completo)  
- `cenariosEVCS/rgridCAPEXSimple.py` (modelo concreto CAPEX simples)  

**Subprogramas documentados:**  
1. `ExportProibidoSimulator`  
2. `CapexSimplesSimulator`  
3. `RiscoRedeSimulator`  
4. `ComparacaoRecursosRiscos`  

---

## 1. SUBPROGRAMA: `ExportProibidoSimulator`

### 1.1 Caracterização do Sistema Energético
- **Tipo:** Microrrede conectada à rede **apenas com importação** (exportação proibida).  
- **Justificativa:** Cenário regulatório restritivo ou rede frágil.  

### 1.2 Horizonte Temporal e Tipo de Operação
- **Horizonte:** 24 horas (um dia típico), com repetição anual.  
- **Operação:** Determinística, horária.  

### 1.3 Natureza da Função Objetivo
- **Objetivo:** Maximizar `(Lucro Operacional Anual - CAPEX total)`.  
- **Código:**  
  ```python
  def objective_rule(m):
      daily_revenue = sum(m.tariff_ev * m.P_ev_load_kw[t] * m.delta_t for t in m.T)
      daily_cost = sum(m.grid_price[t] * m.P_grid_import[t] * m.delta_t for t in m.T)
      annual_profit = m.operational_days * (daily_revenue - daily_cost)
      capex = (m.capex_pv_kw * m.P_pv_cap + m.capex_bess_kwh * m.E_bess_cap + m.capex_trafo_kw * m.P_trafo_cap)
      return annual_profit - capex
  ```

### 1.4 Modelagem do BESS (Battery Energy Storage System)
- **Variáveis:** `P_bess_charge`, `P_bess_discharge`, `SOC`, `y_bess` (binária para evitar simultaneidade).  
- **Restrições:** Limites de potência (C-rate), limites de SOC, balanço energético e condição cíclica (SOC final = SOC inicial).  

### 1.5 Modelagem da Demanda EV
- **Entrada:** Série temporal `P_ev_load_kw` (24 horas).  
- **Tratamento:** Demanda inelástica (deve ser atendida integralmente).  

### 1.6 Restrição Contratual de Potência / Disponibilidade da Rede
- **Evidência:**  
  ```python
  def trafo_limit_rule(m, t):
      return m.P_grid_import[t] <= m.P_trafo_cap
  ```
- **Disponibilidade implícita:** Capacidade do transformador é fixa (não varia com falhas neste subprograma).

### 1.7 Tipo de Formulação Matemática
- **Modelo:** Pyomo `ConcreteModel` (formulação explícita).  
- **Tipo:** MILP (variáveis contínuas + binária `y_bess`).

### 1.8 Adaptações em Relação ao Modelo Base
| Modelo base | Adaptação | Justificativa |
|-------------|-----------|----------------|
| `main.py` (abstrato, permite exportação) | Exportação removida completamente | Cenário de “exportação proibida” solicitado explicitamente. |
| `rgridCAPEXSimple.py` | Adicionada condição cíclica do SOC e restrição de não-simultaneidade de carga/descarga | Evita degradação prematura da bateria e garante operação repetível dia a dia. |

### 1.9 Lacunas para Evolução (Versionamento Futuro)
- **v2:** Incluir incerteza na geração PV (cenários estocásticos).  
- **v3:** Adicionar degradação da bateria ao longo dos anos.  
- **v4:** Modelar falhas discretas da rede (ex: perda total por 2h).

### 1.10 Correspondência Código ↔ Itens Científicos
| Item científico | Trecho de código correspondente |
|----------------|----------------------------------|
| Balanço de energia horário | `EnergyBalance` constraint |
| Limite de potência do transformador | `TrafoLimit` constraint |
| Estado de carga cíclico | `TerminalSOC` constraint |
| Função objetivo econômica | `objective_rule` |

---

## 2. SUBPROGRAMA: `CapexSimplesSimulator`

### 2.1–2.7 (itens similares ao anterior, com diferenças na função objetivo e permissão de exportação)

### 2.8 Adaptações em Relação ao Modelo Base
| Modelo base | Adaptação | Justificativa |
|-------------|-----------|----------------|
| `rgridCAPEXSimple.py` | Adicionada opção `permitir_exportacao` (flag booleana) | Permite comparar diretamente os dois cenários regulatórios. |
| Ambos os códigos | CAPEX tratado como custo único (sem anualização) | Solicitação explícita do usuário: “CAPEX simples”. |

### 2.9 Lacunas para Evolução
- **v2:** Incluir taxa de desconto (VPL).  
- **v3:** Modelar custos de O&M anuais e substituição do BESS.  

---

## 3. SUBPROGRAMA: `RiscoRedeSimulator`

### 3.8 Adaptações em Relação ao Modelo Base
| Modelo base | Adaptação | Justificativa |
|-------------|-----------|----------------|
| Nenhum modelo base anterior tratava falhas | Criação de simulação multi-nível de disponibilidade da rede | Atende diretamente ao pedido: “nível de disponibilidade da rede elétrica em termos de potência ou perigos de falha”. |
| `ExportProibidoSimulator` | Envelopado por um loop que reduz `P_trafo_cap_max` | Implementa análise de sensibilidade sistemática. |

### 3.9 Lacunas para Evolução
- **v2:** Falhas estocásticas com duração variável.  
- **v3:** Custo de interrupção não suprida (perda de carga).  

### 3.10 Correspondência Código ↔ Itens Científicos
| Item científico | Código |
|----------------|--------|
| Curva de impacto da disponibilidade no lucro | `visualizar_analise_risco` – primeiro gráfico |
| Dimensionamento ótimo vs disponibilidade | `visualizar_analise_risco` – segundo gráfico |
| Pior cenário operacional | `visualizar_analise_risco` – terceiro gráfico |

---

## 4. SUBPROGRAMA: `ComparacaoRecursosRiscos`

### 4.8 Adaptações em Relação ao Modelo Base
| Modelo base | Adaptação | Justificativa |
|-------------|-----------|----------------|
| Nenhum modelo base original comparava estratégias | Criação de estrutura de comparação entre PV-only, BESS-only e PV+BESS | Permite avaliar custo-benefício de cada recurso sob risco. |
| Modelo de exportação proibida | Reutilizado com limites zerados para recursos não considerados | Garante base de comparação consistente. |

### 4.9 Lacunas para Evolução
- **v2:** Incluir estratégia com gerador a diesel como reserva.  
- **v3:** Otimização multi-objetivo (lucro vs. resiliência).  

---

## 5. DOCUMENTAÇÃO DO CONJUNTO DOS SUBPROGRAMAS

### 5.1 Rastreabilidade e Versionamento Científico

| Versão do modelo | Subprogramas envolvidos | Mudança documentada | Data |
|------------------|------------------------|----------------------|------|
| v1.0 | Todos | Versão inicial baseada em `main.py` e `rgridCAPEXSimple.py` | 2026-05-21 |
| v1.1 (futuro) | `ExportProibidoSimulator` | Inclusão de incerteza na irradiância | TBD |
| v2.0 (futuro) | `RiscoRedeSimulator` | Falhas estocásticas com duração variável | TBD |

### 5.2 Conexão com Repositório Original
- **Base conceitual:** As equações de balanço de energia, limites do transformador e dinâmica do BESS foram mantidas conforme os modelos originais.  
- **Diferenças documentadas:** Tabelas de adaptações em cada subprograma.  
- **Elo documental:** Este arquivo `.md` deve ser versionado junto com os códigos, mantendo o link para os repositórios originais via comentários no cabeçalho de cada arquivo `.py`.

### 5.3 Registro Formal para Auditoria e Certificação
Cada subprograma inclui:
- **Bloco de documentação no código:**  
  ```python
  """
  SUBPROGRAMA PARA ANÁLISE DE MICRORREDES EM ELETROPOSTOS
  ========================================================
  Autor: Baseado nos códigos de leticiasdrummond
  Versão: 1.0
  Data: 2026-05-21
  Base metodológica: Mapeamento_Estrutura_Modelo.md
  Adaptações documentadas em: (https://chat.deepseek.com/share/vci2gg5uw99kj8ciml)
  """
  ```

- **Saídas padronizadas:** Relatórios textuais + gráficos nomeados consistentemente (ex: `export_proibido_balanco.png`, `risco_rede_impacto_economico.png`).

### 5.4 Reprodutibilidade e Colaboração
- **Pré-requisitos documentados:** Pyomo, matplotlib, pandas, numpy, solver (Gurobi recomendado).  
- **Dados de exemplo:** Função `exemplo_dados()` fornece um caso de teste completo.  
- **Instruções de execução:** Função `main()` demonstra o fluxo completo.  
- **Estrutura de saída:** Pasta `./resultados_microrredes` com subpastas por cenário.

---

## ✅ Conclusão

Os quatro subprogramas e sua documentação estruturante atendem aos seguintes objetivos estratégicos:

- **Registro formal:** Cada modelo tem seu propósito, adaptações e lacunas documentadas, permitindo auditoria.  
- **Versionamento científico:** As tabelas de evolução e as anotações de versão futura mostram claramente como o modelo pode crescer.  
- **Rastreabilidade:** A correspondência código ↔ itens científicos garante que cada exigência metodológica está explicitamente ligada a um trecho de código.  
- **Reprodutibilidade:** Os exemplos e a função `main()` permitem que qualquer pesquisador execute e valide os resultados.

**Arquivos gerados para repositório:**  
- Este documento (`.md`)  
- Código dos quatro subprogramas (`.py`)  
- Relatórios e gráficos salvos automaticamente na pasta `./resultados_microrredes`
