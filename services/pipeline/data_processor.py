import logging
from typing import Any, Dict, List, Optional, Set

from services.pipeline.models import KinematicVector, ProcessedSession, RRWebEvent, UserAction

# Configuração de Logs para monitoramento do processamento de traços de eventos
logger = logging.getLogger("ux_auditor")

# --- Lógica de Processamento (O(N) - Single Pass) ---

class SessionPreprocessor:
    # Tags que devem ser ignoradas para economizar tokens do LLM e remover ruído estrutural (scripts, CSS)
    IGNORED_TAGS: Set[str] = {'script', 'style', 'link', 'meta', 'noscript'}
    
    # Atributos HTML que carregam significado semântico real para a UX (IDs, nomes, labels)
    RELEVANT_ATTRS: Set[str] = {'id', 'class', 'name', 'type', 'aria-label', 'placeholder', 'value', 'href', 'role'}

    @staticmethod
    def process(events: List[RRWebEvent]) -> ProcessedSession:
        """
        Processa uma lista de eventos brutos do rrweb em estruturas otimizadas para análise.
        
        Executa um único loop O(N) para extrair:
        - Vetores cinemáticos (timestamp, x, y) para detecção de anomalias via heurísticas
        - Ações do usuário (cliques, inputs, navegação) para geração de narrativa via LLM
        - Mapa DOM simplificado para contexto enriquecido das interações
        
        Args:
            events (List[RRWebEvent]): Lista de eventos brutos capturados pelo rrweb.
            
        Returns:
            ProcessedSession: Objeto contendo os dados processados organizados em buckets otimizados.
        """
        if not events:
            return ProcessedSession(initial_timestamp=0, total_duration=0)

        # 1. Setup Temporal: O primeiro evento marca o início (T0) da sessão
        # events.sort(key=lambda x: x.timestamp) # Assumimos ordem cronológica vinda da persistência
        
        start_time = events[0].timestamp
        last_timestamp = start_time
        
        # Estruturas temporárias para acumular os dados durante o loop único
        kinematics: List[KinematicVector] = []
        actions: List[UserAction] = []
        dom_map: Dict[int, str] = {}

        # Mapeamento de Constantes internas do protocolo RRWeb v2
        TYPE_DOM_SNAPSHOT = 2    # Captura completa do estado atual da página
        TYPE_INCREMENTAL = 3     # Mudanças granulares (movimento, clique, scroll, mutação)
        TYPE_META = 4            # Informações sobre a página (URL, tamanho da tela)
        
        SOURCE_MOUSE_MOVE = 1    # Movimento do cursor (Cinemática)
        SOURCE_MOUSE_INTERACTION = 2 # Cliques e interações discretas (Semântica)
        SOURCE_SCROLL = 3        # Rolagem da página
        SOURCE_RESIZE = 4        # Mudança no tamanho da janela
        SOURCE_INPUT = 5         # Entrada de texto ou valores em campos
        
        # Tipos específicos de interação de mouse dentro do rrweb
        INTERACTION_CLICK = 2

        # --- LOOP ÚNICO (O(N)) ---
        # Garantimos eficiência máxima percorrendo a lista de eventos apenas uma vez
        for idx, event in enumerate(events):
            try:
                current_raw_ts = event.timestamp
                # Normaliza o tempo para ms relativos ao início para facilitar cálculos posteriores
                delta_ts = current_raw_ts - start_time
                last_timestamp = current_raw_ts

                evt_type = event.type
                data = event.data or {}

                # --- Ramo A: Reconstrução de Contexto (DOM) ---
                # snapshots ocorrem no início ou quando o estado do DOM muda drasticamente
                if evt_type == TYPE_DOM_SNAPSHOT:
                    root_node = data.get('node')
                    if root_node:
                        # Achata a estrutura de árvore recursiva em um mapa linear de ID -> HTML
                        SessionPreprocessor._flatten_dom_tree(root_node, dom_map)

                # --- Ramo B: Meta Eventos (Navegação/Viewport) ---
                elif evt_type == TYPE_META:
                    href = data.get('href')
                    width = data.get('width')

                    if href:
                        # Registra transições de URL como ações de navegação
                        actions.append(UserAction(
                            timestamp=delta_ts,
                            action_type='navigation',
                            details=f"URL: {href}"
                        ))
                    elif width:
                        # Registra mudanças na viewport do usuário
                        actions.append(UserAction(
                            timestamp=delta_ts,
                            action_type='resize',
                            details=f"Viewport: {width}x{data.get('height')}"
                        ))

                # --- Ramo C: Eventos Incrementais (Interações Ativas) ---
                elif evt_type == TYPE_INCREMENTAL:
                    source = data.get('source')

                    # C.1: Cinemática - Extração de coordenadas para a heurística de anomalias
                    if source == SOURCE_MOUSE_MOVE:
                        positions = data.get('positions', [])
                        # Validação para evitar quebras por dados malformados em um único evento
                        if not isinstance(positions, list):
                            logger.debug(
                                "Skipping mouse move event %s because positions is %s",
                                idx,
                                type(positions).__name__,
                            )
                            continue

                        # O rrweb compacta múltiplos movimentos em um único evento incremental
                        for pos in positions:
                            x = None
                            y = None
                            p_offset = 0

                            # O formato da posição pode variar entre dicionário ou lista
                            if isinstance(pos, dict):
                                x = pos.get('x')
                                y = pos.get('y')
                                p_offset = pos.get('timeOffset', 0) or 0

                            elif isinstance(pos, (list, tuple)) and len(pos) >= 2:
                                x = pos[0]
                                y = pos[1]
                                # Algumas versões guardam o tempo relativo no quarto índice
                                p_offset = pos[3] if len(pos) >= 4 else 0

                            # Ignora se os dados obrigatórios de geometria estiverem ausentes
                            if x is None or y is None:
                                continue

                            # Filtra coordenadas negativas ou inválidas
                            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                                if x >= 0 and y >= 0:
                                    kinematics.append(KinematicVector(
                                        timestamp=delta_ts + int(p_offset or 0),
                                        x=int(x),
                                        y=int(y)
                                    ))

                    # C.2: Ações do Usuário (LLM) - Cliques e Toques
                    elif source == SOURCE_MOUSE_INTERACTION:
                        i_type = data.get('type')
                        # Focamos especificamente no evento de clique para a análise semântica
                        if i_type == INTERACTION_CLICK:
                            target_id = data.get('id')
                            # Busca no mapa DOM o que é esse ID em termos de HTML simplificado
                            node_html = dom_map.get(target_id, "unknown_element")

                            actions.append(UserAction(
                                timestamp=delta_ts,
                                action_type='click',
                                target_id=target_id,
                                details=f"Element: {node_html} | Coords: ({data.get('x')}, {data.get('y')})"
                            ))

                    # C.3: Ações do Usuário (LLM) - Scroll (Rolagem)
                    elif source == SOURCE_SCROLL:
                        delta_y = data.get('deltaY', data.get('y'))
                        scroll_y = data.get('scrollY')
                        if delta_y is not None or scroll_y is not None:
                            # Inferência de direção para facilitar a narrativa do LLM no prompt
                            direction = "down" if (delta_y or scroll_y or 0) > 0 else "up" if (delta_y or scroll_y or 0) < 0 else "neutral"
                            actions.append(UserAction(
                                timestamp=delta_ts,
                                action_type='scroll',
                                details=f"Scroll: direction={direction} deltaY={delta_y} scrollY={scroll_y}"
                            ))

                    # C.4: Redimensionamento de janela (Resize)
                    elif source == SOURCE_RESIZE:
                        width = data.get('width')
                        if width:
                            actions.append(UserAction(
                                timestamp=delta_ts,
                                action_type='resize',
                                details=f"Viewport: {width}x{data.get('height')}"
                            ))

                    # C.5: Ações do Usuário (LLM) - Digitação (Inputs)
                    elif source == SOURCE_INPUT:
                        target_id = data.get('id')
                        text_val = data.get('text', '')
                        is_checked = data.get('isChecked')

                        details = ""
                        # Trata inputs booleanos (checkbox/radio) separadamente de campos de texto
                        if is_checked is not None:
                            details = f"Checked: {is_checked}"
                        else:
                            # LGPD e Economia de Tokens: Truncamos textos longos em inputs
                            safe_text = text_val[:40] + "..." if len(text_val) > 40 else text_val
                            details = f"Typed: '{safe_text}'"

                        # Enriquecimento opcional: Adiciona o contexto do elemento HTML alvo
                        node_context = dom_map.get(target_id, "")
                        if node_context:
                            details += f" on {node_context}"

                        actions.append(UserAction(
                            timestamp=delta_ts,
                            action_type='input',
                            target_id=target_id,
                            details=details
                        ))
            except Exception as exc:
                # Loga o erro mas continua o processamento para não perder a sessão inteira por um evento malformado
                logger.warning(
                    "Skipping malformed rrweb event at index %s (type=%s): %s: %s",
                    idx,
                    getattr(event, "type", None),
                    type(exc).__name__,
                    exc,
                )
                continue

        # 3. Consolidação Final: Cálculo da duração total e retorno do container agnóstico
        total_duration = last_timestamp - start_time
        
        logger.info(f"Processed session: {len(events)} raw events -> {len(kinematics)} vectors, {len(actions)} actions.")
        
        return ProcessedSession(
            initial_timestamp=start_time,
            total_duration=total_duration,
            kinematics=kinematics,
            actions=actions,
            dom_map=dom_map
        )

    @staticmethod
    def _flatten_dom_tree(node: Dict[str, Any], dom_map: Dict[int, str]):
        """
        Percorre recursivamente a árvore JSON do rrweb.
        Objetivo: Criar uma representação HTML 'token-efficient' (compacta) para o LLM.
        Ignora nós irrelevantes como CSS e scripts para focar na semântica da interface.
        """
        node_id = node.get('id')
        tag_name = node.get('tagName', '').lower()
        
        # Filtro de exclusão: Nós que não contribuem para a análise visual/funcional do LLM
        if tag_name in SessionPreprocessor.IGNORED_TAGS:
            return

        # Processamos apenas nós do tipo Elemento (que possuem ID e Tag)
        if node_id and tag_name:
            attributes = node.get('attributes', {})
            
            # Seleção de atributos "Whitelist" que carregam semântica UX
            attrs_str = ""
            for k, v in attributes.items():
                if k in SessionPreprocessor.RELEVANT_ATTRS:
                    # Proteção contra valores de atributos gigantescos (ex: SVGs embutidos)
                    v_str = str(v)
                    if len(v_str) > 50: 
                        v_str = v_str[:47] + "..."
                    attrs_str += f' {k}="{v_str}"'
            
            # Extração de texto visível para ajudar o LLM a entender o rótulo do componente
            text_content = ""
            children = node.get('childNodes', [])
            for child in children:
                if child.get('type') == 3: # Tipo 3 é Text Node (texto puro)
                    raw_text = child.get('textContent', '').strip()
                    if raw_text:
                        text_content += raw_text + " "
            
            # Truncamento de texto excessivo por economia de tokens
            if len(text_content) > 30:
                text_content = text_content[:30] + "..."

            # Montagem do HTML sintético: <button class="btn">Login</button>
            simplified_html = f"<{tag_name}{attrs_str}>{text_content.strip()}</{tag_name}>"
            dom_map[node_id] = simplified_html

        # Chamada recursiva para processar os filhos da árvore (Depth-First Search)
        if 'childNodes' in node:
            for child in node['childNodes']:
                SessionPreprocessor._flatten_dom_tree(child, dom_map)
