# Documento-base para análises de mestrado

## 1. Finalidade
Este documento registra o conjunto **útil e suficiente** para análises acadêmicas (nível mestrado) sobre os cenários de microrrede para eletroposto deste repositório.

---

## 2. Escopo do estudo
- Sistema: microrrede com carga de recarga de veículos elétricos (EV), geração fotovoltaica (PV), bateria (BESS) e conexão com rede.
- Horizonte operacional: 24 horas representativas, anualizadas por dias operacionais equivalentes.
- Tipo de modelo: otimização MILP (Pyomo), com decisão conjunta de investimento (CAPEX) e despacho horário.
- Premissa central dos estudos da pasta principal: operação com importação da rede e sem exportação.

---

## 3. Arquivos essenciais para análise
- Modelo e pós-processamento:  
  `/home/runner/work/cenariosEVCS/cenariosEVCS/CAPEX SIMPLES e sem exportação/rgridCAPEXSimple.py`
- Execução automática dos cenários:  
  `/home/runner/work/cenariosEVCS/cenariosEVCS/CAPEX SIMPLES e sem exportação/run_cenarios.py`
- Resultados comparativos consolidados:  
  `/home/runner/work/cenariosEVCS/cenariosEVCS/CAPEX SIMPLES e sem exportação/cenarios_2026-05-13/comparativo_cenarios.txt`
- Relatórios por cenário: subpastas em  
  `/home/runner/work/cenariosEVCS/cenariosEVCS/CAPEX SIMPLES e sem exportação/cenarios_2026-05-13/`

---

## 4. Metodologia adotada
1. Definição dos parâmetros econômicos e técnicos do caso-base.
2. Formulação do problema em MILP:
   - variáveis de capacidade (PV, BESS, transformador);
   - variáveis horárias de operação (geração, importação, carga/descarga, SOC);
   - variável binária para não simultaneidade de carga/descarga do BESS.
3. Resolução com solver (ex.: Gurobi).
4. Extração de resultados técnicos e econômicos.
5. Comparação entre cenários com mesmo perfil de carga EV e perfis tarifário/solar.

---

## 5. Parâmetros principais empregados (caso-base)
- `delta_t = 1 h`
- `operational_days_equivalent = 365`
- `tariff_ev = 1.60 R$/kWh`
- CAPEX:
  - `capex_pv_kw = 1200 R$/kW`
  - `capex_bess_kwh = 700 R$/kWh`
  - `capex_trafo_kw = 1000 R$/kW`
- Eficiências:
  - `eta_charge = 0.91`
  - `eta_discharge = 0.91`
- SOC:
  - `soc_min_frac = 0.05`
  - `soc_max_frac = 0.95`
  - `soc_initial_frac = 0.50`
- C-rate:
  - `c_rate_charge = 1.00`
  - `c_rate_discharge = 1.00`

---

## 6. Equacionamento matemático (núcleo)
### 6.1 Função objetivo
Maximizar:
\[
\text{FO} = \text{Lucro operacional anual} - \text{CAPEX total}
\]

com:
\[
\text{Lucro operacional anual} =
D \cdot \sum_{t=1}^{24}
\left(
\tau_{EV}\,P^{EV}_t\Delta t - c^{rede}_t\,P^{rede}_t\Delta t
\right)
\]
\[
\text{CAPEX total} =
c^{PV}P^{PV}_{cap} + c^{BESS}E^{BESS}_{cap} + c^{TR}P^{TR}_{cap}
\]

### 6.2 Restrições estruturais
- Limite PV: \(P^{PV}_t \le P^{PV}_{cap}\cdot CF_t\)
- Limite transformador: \(P^{rede}_t \le P^{TR}_{cap}\)
- Balanço do barramento:
\[
P^{PV}_t + P^{rede}_t + P^{desc}_t = P^{EV}_t + P^{carga}_t
\]
- Dinâmica SOC:
\[
SOC_t = SOC_{t-1} + \Delta t\left(\eta_c P^{carga}_t - \frac{P^{desc}_t}{\eta_d}\right)
\]
- Limites SOC: \(SOC_{min} \le SOC_t \le SOC_{max}\)
- Condição cíclica: \(SOC_{24} = SOC_{inicial}\)
- Não simultaneidade de carga e descarga via variável binária.

---

## 7. Cenários analisados no lote consolidado
1. **100% rede, trafo até 500 kW, sem PV/BESS**
2. **Trafo limitado a 75 kW com PV+BESS**
3. **Sem BESS, com PV+rede**

---

## 8. Resultados consolidados (arquivo comparativo)
Lucro operacional anual:
- `100_rede_trafo_500kw`: **R$ 621.704,50**
- `trafo_75kw_pv_bess`: **R$ 959.405,74**
- `sem_bess_pv_rede`: **R$ 868.484,84**

Leitura técnica:
- O cenário com trafo restrito e PV+BESS maximiza lucro operacional anual.
- O cenário sem BESS pode apresentar maior atratividade líquida quando o CAPEX do armazenamento pesa na função objetivo total.

---

## 9. Indicadores mínimos para dissertação
Para cada cenário, reportar obrigatoriamente:
1. Capacidades ótimas: \(P^{PV}_{cap}\), \(E^{BESS}_{cap}\), \(P^{TR}_{cap}\)
2. Receita anual com recarga
3. Custo anual de importação da rede
4. Lucro operacional anual
5. CAPEX total
6. Valor da função objetivo
7. Verificações de consistência (resíduo de balanço, limites de SOC, limites de potência)

---

## 10. Procedimento reprodutível (resumo)
1. Ajustar parâmetros em `run_cenarios.py` ou no dicionário de dados.
2. Executar:
   - `python run_cenarios.py --solver gurobi`
3. Coletar:
   - `comparativo_cenarios.txt`
   - `resultados.json` por cenário
   - `relatorio_*.txt` por cenário
4. Consolidar tabelas e gráficos para capítulo de resultados.

---

## 11. Limitações atuais e evolução recomendada
- Horizonte determinístico diário (sem incerteza explícita).
- Sem modelagem de degradação eletroquímica no modelo principal.
- Sem análise financeira com taxa de desconto (VPL/TIR).

Extensões recomendadas para continuidade:
1. Cenários estocásticos (irradiância, tarifa, demanda EV).
2. Degradação do BESS e reposição.
3. Análise econômica de longo prazo (CAPEX anualizado + O&M + desconto).

---

## 12. Conclusão de suficiência documental
Com os arquivos e itens acima, o estudo já possui base suficiente para:
- descrever metodologia;
- justificar hipóteses;
- reproduzir resultados;
- comparar cenários;
- sustentar análise técnica e econômica em nível de mestrado.
