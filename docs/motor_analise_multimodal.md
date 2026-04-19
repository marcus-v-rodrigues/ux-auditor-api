# Motor de Análise Multimodal

Este documento descreve a arquitetura do motor de análise da UX Auditor API, que integra Heurísticas Determinísticas, Machine Learning (Isolation Forest) e Agentes Baseados em LLM (Fases 1 e 2) para transformar rastros técnicos de interação em insights psicométricos e funcionais.

## Arquitetura de Processamento

O pipeline é dividido em etapas que elevam gradualmente o nível de abstração dos dados, desde eventos brutos até uma narrativa semântica consolidada.

### Diagrama de Fluxo

```mermaid
%%{init: { 'theme': 'dark', 'themeVariables': { 'fontSize': '13px', 'fontFamily': 'Fira Code' }, 'flowchart': { 'rankSpacing': 80, 'nodeSpacing': 20, 'curve': 'basis' } } }%%
flowchart TD
    %% ==================== COLUNA 1: INPUT ====================
    subgraph Col1["[1] Ingestão de Telemetria"]
        direction TB
        SD["Eventos rrweb<br/>(Rastro Técnico)"]
        AD["Metadados Axe<br/>(Acessibilidade)"]
        SMD["Anotações Semânticas<br/>(Contexto)"]
        IS["Sumário Interação<br/>(Client-side)"]
    end

    %% ==================== COLUNA 2: PRE-PROC ====================
    subgraph Col2["[2] Estruturação"]
        direction TB
        KD["Vetores Cinemáticos<br/>(x, y, t)"]
        DD["DOM Achatado<br/>(Árvore Simplificada)"]
        P1A["Agente Fase 1 (LLM)<br/>(Extraction Plan)"]
    end

    %% ==================== COLUNA 3: SINAIS (REORDENADA) ====================
    subgraph Col3["[3] Extração de Sinais"]
        direction TB
        
        %% Invertemos a ordem visual: ML na esquerda, Heurísticas/SH na direita
        subgraph ML_Group["Detecção ML"]
            direction TB
            AND["Outliers<br/>(Isolation Forest)"]
            MLE["Movimento Errático"]
        end

        CI["Interações Canônicas<br/>(Transformação)"]

        subgraph RightAnalysis["Análise Heurística e Estrutural"]
            direction LR
            BH["Comportamentais<br/>(Rage/Dead)"]
            STH["Estruturais<br/>(Variação)"]
            SH["Avaliação Nielsen<br/>(Structural)"]
        end
    end

    %% ==================== COLUNA 4: BUNDLE ====================
    subgraph Col4["[4] Consolidação"]
        direction TB
        SG["Segmentação de Episódios"]
        SB["Semantic Session Bundle<br/>(Evidence Collector)"]
        P2A["Agente Fase 2 (LLM)<br/>(Final Interpretation)"]
    end

    %% ==================== COLUNA 5: OUTPUT ====================
    subgraph Col5["[5] Insights (Quali/Quanti)"]
        direction TB
        NAR["Narrativa"]
        GH["Hipótese de Objetivo"]
        FP["Pontos de Fricção"]
        BP["Padrões Comport."]
        PS["Sinais de Progresso"]
    end

    %% --- CONEXÕES (AJUSTADAS PARA O NOVO FLUXO) ---
    SD --> KD & DD
    AD & SMD & DD --> P1A
    
    KD --> AND & BH
    P1A --> CI & SH
    SD --> BH
    AND --> MLE
    
    CI --> SG & STH & BH
    
    %% Concentração no Bundle
    MLE & BH & STH & SH & CI & SG & IS --> SB
    
    SB --> P2A
    P2A --> NAR & GH & FP & BP & PS

    %% Estilização
    classDef inputStyle fill:#0D47A1,stroke:#fff,stroke-width:1px
    classDef phase1Style fill:#1B5E20,stroke:#fff,stroke-width:1px
    classDef analysisStyle fill:#E65100,stroke:#fff,stroke-width:1px
    classDef phase2Style fill:#F9A825,stroke:#000,stroke-width:1px,color:#000
    classDef outputStyle fill:#880E4F,stroke:#fff,stroke-width:1px

    class SD,AD,SMD,IS inputStyle
    class KD,DD,P1A phase1Style
    class CI,SH,AND,MLE,BH,STH analysisStyle
    class SB,P2A,SG phase2Style
    class NAR,GH,FP,BP,PS outputStyle
```

## Componentes Principais

### 1. Telemetria Enriquecida
Diferente de sistemas puramente baseados em vídeo ou logs brutos, a UX Auditor API utiliza um payload enriquecido proveniente de uma extensão de navegador. Este payload inclui:
- **Eventos rrweb:** Rastro técnico completo para reconstrução.
- **Metadados Axe:** Resultados de auditoria de acessibilidade automática em tempo de execução.
- **Anotações Semânticas:** Metadados inseridos pelo desenvolvedor ou inferidos pela extensão sobre a natureza dos componentes.

### 2. Fase 1: Planejamento Estrutural
Um agente LLM analisa o estado inicial da página (DOM achatado) e o contexto semântico para gerar um **Extraction Plan**. Este plano define quais áreas da tela são relevantes e qual a meta provável da página, além de realizar uma avaliação inicial das Heurísticas de Nielsen.

### 3. Execução e Análise de Sinais
As ações técnicas (clicks, inputs, mouse moves) são convertidas em **Interações Canônicas**. Sobre estas interações, operam dois motores:
- **Heurísticas:** Detectores determinísticos de padrões como *Rage Clicks*, *Hesitação* e *Revisão de Input*.
- **Machine Learning:** O algoritmo **Isolation Forest** analisa a cinemática do cursor (velocidade e variação angular) para identificar anomalias motoras (*Erratic Movement*).

### 4. Fase 2: Interpretação Semântica
O **Semantic Session Bundle** consolida todas as evidências (heurísticas, ML, interações canônicas e metadados da extensão). Um segundo agente LLM interpreta este bundle para gerar a análise final estruturada, focando em intenção do usuário, pontos de fricção e progresso.

## Saída de Dados
A análise estruturada resultante (`StructuredSessionAnalysis`) fornece:
- **Narrativa Qualitativa:** Um resumo em linguagem natural do que ocorreu na sessão.
- **Hipótese de Objetivo:** O que o usuário tentou realizar e qual o nível de confiança dessa inferência.
- **Pontos de Fricção:** Identificação clara de momentos de frustração ou confusão apoiados por evidências heurísticas.
- **Sinais de Progresso:** Indicadores de que o usuário está avançando em direção ao sucesso funcional.
