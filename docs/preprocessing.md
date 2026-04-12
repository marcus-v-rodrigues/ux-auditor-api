# Pré-processamento Otimizado de Sessões

## Visão Geral e Propósito
O módulo `SessionPreprocessor` (em `services/data_processor.py`) é o motor de transformação de dados do sistema. Sua função é converter o formato JSON hierárquico e verboso do `rrweb` em estruturas planas e otimizadas para diferentes tipos de consumidores (algoritmos de ML e modelos de linguagem).

## Arquitetura e Lógica
```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#bfbfbf', 'edgeColor': '#5d5d5d' }, "flowchart": {"subGraphTitleMargin": {"bottom": 30}}}}%%
graph LR
    Input[Eventos rrweb] --> SP[SessionPreprocessor]
    subgraph "Single Pass O(N)"
        SP --> K[Kinematic Vectors: x, y, t]
        SP --> UA[User Actions: clicks, inputs]
        SP --> DM[DOM Map: O(1) Lookup]
    end
    DM --> DF[DOM Flattening]
    DF --> Output[DOM Semântico Limpo]
```

O processamento é realizado em uma única passagem (**Single Pass O(N)**), garantindo escalabilidade para sessões longas. O fluxo divide os dados em três "buckets" principais:

1.  **Kinematic Vectors:** Lista de coordenadas $(x, y)$ com timestamps, filtrando apenas movimentos de mouse.
2.  **User Actions:** Sequência de eventos de alto nível (cliques, inputs, navegação) enriquecidos com contexto do DOM.
3.  **DOM Map:** Um mapa de busca $O(1)$ que associa IDs de nós do rrweb a representações HTML simplificadas.

### Algoritmo de Flattening de DOM
Para economizar tokens em chamadas de LLM, o sistema implementa uma limpeza agressiva da árvore DOM:
*   Remove tags irrelevantes (`<script>`, `<style>`).
*   Mantém apenas atributos semânticos (`aria-label`, `id`, `name`, `type`).
*   Trunca conteúdos de texto e valores de atributos longos.

## Fundamentação Matemática
A eficiência temporal é o foco matemático deste módulo. Ao evitar múltiplas iterações sobre a lista de eventos (que pode conter milhares de entradas), o custo computacional é reduzido de $O(K \cdot N)$ para $O(N)$, onde $K$ é o número de análises realizadas.

A representação de tempo é normalizada como deltas relativos ao início da sessão:
$$ \Delta t_i = t_i - t_{start} $$

## Parâmetros Técnicos
*   `IGNORED_TAGS`: Conjunto de tags descartadas na reconstrução.
*   `RELEVANT_ATTRS`: Lista branca de atributos para preservação de contexto.
*   `MAX_TEXT_LENGTH`: Limiar de 30-40 caracteres para truncamento de strings.

## Mapeamento Tecnológico e Referências
*   **Pydantic:** Utilizado para validação rigorosa dos esquemas de dados processados. [Documentação](https://docs.pydantic.dev/)
*   **RRWeb Snapshot:** Protocolo de serialização do DOM. [Referência Técnica](https://github.com/rrweb-io/rrweb/blob/master/guide.md#serialization)

## Justificativa de Escolha
A separação em buckets permite que cada serviço de análise consuma apenas o dado necessário. O `Isolation Forest` não precisa de nomes de classes CSS, e o `LLM` não precisa de cada micro-movimento do mouse. Isso reduz o consumo de memória e a latência de rede.
