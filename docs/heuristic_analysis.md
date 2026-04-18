# Catálogo de Heurísticas de UX

Este documento descreve as heurísticas determinísticas e comportamentais implementadas no sistema para identificar padrões de uso e pontos de fricção.

## 1. Heurísticas de Frustração (Baixo Nível)

Estas heurísticas operam sobre eventos técnicos brutos para capturar sinais óbvios de estresse ou falha na interface.

| Heurística | Gatilho | Significado | Confiança Base |
| :--- | :--- | :--- | :--- |
| **Rage Click** | 3+ cliques em < 1s na mesma região. | Frustração com elemento não responsivo ou lento. | 0.95 |
| **Dead Click** | Clique sem mudança no DOM ou navegação em 1.2s. | Elemento parece interativo mas não é, ou quebra de expectativa. | 0.58 |
| **Repeated Toggle** | Alternância rápida de estado (ex: checkbox) 2+ vezes. | Incerteza ou erro acidental de clique. | 0.78 |

## 2. Heurísticas de Navegação e Fluxo (Médio Nível)

Operam sobre as **Interações Canônicas** (pós-processadas) para entender como o usuário progride na tarefa.

-   **Local Hesitation:** Detecta pausas longas (>3s) entre interações no mesmo grupo semântico. Indica dificuldade em processar a informação ou decidir a resposta.
-   **Input Revision:** Mudança de valor em campos de texto após uma entrada inicial. Pode indicar correção de erro ou mudança de ideia.
-   **Out of Order Progression:** Preenchimento de campos em ordem não linear (ex: pular do item 1 para o 5 e voltar para o 2). Indica desorientação no layout.
-   **Region Alternation:** Alternância frequente entre diferentes áreas da página. Sugere busca por suporte ou comparação de informações.

## 3. Heurísticas de Atenção e Movimento (Cinemática)

Baseadas na trajetória do cursor e análise estatística/ML.

-   **Hover Prolongado:** Cursor parado sobre um elemento por >1.5s. Sugere leitura ou dúvida.
-   **Visual Search Burst:** Muitos movimentos de mouse com poucas ações efetivas em um curto intervalo. Indica que o usuário está "procurando" algo ativamente.
-   **Erratic Motion:** Movimentos com baixa eficiência de trajetória e muitas mudanças bruscas de ângulo. Sinal clássico de confusão ou dificuldade motora.
-   **ML Erratic Motion:** Detecção via *Isolation Forest* que isola outliers de movimento baseados em velocidade e torque (aceleração angular).

## 4. Heurísticas de Resumo e Performance

-   **Task Progression:** Avalia se o fluxo de interações culminou em um evento de submissão bem-sucedido.
-   **Session Fragmentation:** Identifica sessões com muitos gaps temporais, sugerindo que o usuário foi interrompido ou está multi-tarefando.
-   **Multi-region Attention:** Quantifica quão distribuída está a atenção do usuário entre as diferentes seções identificadas na Fase 1.

## Configuração de Thresholds

Os valores de tempo e distância podem ser ajustados via variáveis de ambiente ou no arquivo `config.py`:

- `RAGE_CLICK_WINDOW_MS`: Janela de tempo para detecção de cliques rápidos.
- `LONG_IDLE_MS`: Tempo para considerar hesitação.
- `ERRATIC_MOTION_PATH_EFFICIENCY_MAX`: Limiar de eficiência para movimento errático.

## Fundamentação Teórica e Acadêmica

Abaixo estão as bases científicas que sustentam a validade das heurísticas implementadas no sistema.

### 1. Frustração e Resposta do Sistema
As heurísticas de **Rage Click** e **Dead Click** baseiam-se na quebra de expectativa e no tempo de resposta do sistema.
*   **Miller (1968):** Estabeleceu que atrasos superiores a 1 segundo quebram o fluxo de pensamento do usuário. A ausência de feedback visual (Dead Click) viola a heurística de **Visibilidade do Status do Sistema (Nielsen, 1994)**.
*   **Aprendizado Desamparado (Seligman):** Em psicologia, cliques repetitivos (Rage) são manifestações de frustração quando o usuário sente que perdeu o controle sobre a interface.

### 2. Teoria do Forageamento de Informação (IFT)
As heurísticas de **Visual Search Burst**, **Hover Prolongado** e **Region Alternation** são explicadas pela IFT (**Pirolli & Card, 1999**).
*   **Cheiro de Informação (Information Scent):** O usuário move o cursor e pausa (Hover) em elementos que prometem levá-lo ao objetivo.
*   **Burstiness (Barabási, 2005):** A dinâmica humana é naturalmente "em rajadas". Explosões de movimento sem cliques indicam uma fase de busca visual intensa ou baixa densidade de informação (baixo "scent").

### 3. Dinâmica Cinemática e Emoção
A detecção de **Erratic Motion** (Heurística e ML) fundamenta-se em pesquisas recentes de HCI.
*   **Jenkins et al. (2016) - "The Cursor Betrays Your Emotions":** Pesquisadores da BYU demonstraram que o movimento do mouse é um proxy robusto para o estado emocional. Usuários frustrados exibem trajetórias menos suaves (jagged), maior aceleração angular e menor precisão.
*   **Correlação Mouse-Olho (Chen et al., 2001; Huang et al., 2012):** Existe uma correlação estatística alta (até 80%) entre a posição do cursor e o foco atencional (gaze), validando o uso do mouse como sensor de atenção.

### 4. Carga Cognitiva
**Local Hesitation** e **Out of Order Progression** relacionam-se à **Teoria da Carga Cognitiva (Sweller, 1988)**.
*   Pausas longas entre campos semanticamente relacionados indicam uma alta carga intrínseca (dificuldade de processar a pergunta) ou extrínseca (layout confuso).
*   A progressão não-linear sugere uma falha no modelo mental do usuário em relação à hierarquia visual da página.
