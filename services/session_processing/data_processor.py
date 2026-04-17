import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from services.session_processing.models import FlatDOMNode, KinematicVector, PageMetadata, ProcessedSession, RRWebEvent, RawAction, UserAction
from services.domain.interaction_patterns import normalize_text

# Configuração de Logs para monitoramento do processamento de traços de eventos
logger = logging.getLogger("ux_auditor")

# --- Lógica de Processamento (O(N) - Single Pass) ---

class SessionPreprocessor:
    """
    Pré-processamento neutro e barato do rrweb.

    A responsabilidade desta classe é preparar o material estrutural que será
    consumido pela fase 1 e pelo executor determinístico. Ela deliberadamente
    não tenta fechar semântica de formulário, não executa heurísticas UX e não
    comprime o rastro em unidades finais de interação, porque essas decisões
    agora dependem do plano estrutural gerado depois.
    """

    # Tags que devem ser ignoradas para economizar tokens do LLM e remover ruído estrutural (scripts, CSS)
    IGNORED_TAGS: Set[str] = {'script', 'style', 'link', 'meta', 'noscript'}
    
    # Atributos HTML que carregam significado semântico real para a UX (IDs, nomes, labels)
    RELEVANT_ATTRS: Set[str] = {'id', 'class', 'name', 'type', 'aria-label', 'placeholder', 'value', 'href', 'role'}

    @staticmethod
    def process(events: List[RRWebEvent], extension_metadata: Optional[Dict[str, Any]] = None) -> ProcessedSession:
        """
        Processa uma lista de eventos brutos do rrweb em artefatos neutros.
        
        Aproveita metadados enriquecidos vindos da extensão (Axe, Semantics, Meta) 
        para acelerar a reconstrução e fornecer contexto semântico imediato à fase 1.
        
        Args:
            events: Lista de eventos técnicos do rrweb.
            extension_metadata: Dicionário opcional contendo o payload consolidado da extensão.
            
        Returns:
            ProcessedSession: Estrutura processada para análise semântica.
        """
        if not events:
            return ProcessedSession(initial_timestamp=0, total_duration=0)

        # 1. Setup Temporal: O primeiro evento marca o início (T0) da sessão
        start_time = events[0].timestamp
        last_timestamp = start_time
        
        # Estruturas para acumular os dados durante o loop único (O(N))
        kinematics: List[KinematicVector] = []
        actions: List[UserAction] = []
        dom_map: Dict[int, str] = {}
        flattened_dom: List[FlatDOMNode] = []
        raw_actions: List[RawAction] = []
        event_index: Dict[str, List[int]] = defaultdict(list)
        page_metadata = PageMetadata()
        current_page_url: Optional[str] = None

        # Se houver metadados da extensão, preenchemos o contexto inicial de página
        # para que o agente da fase 1 já saiba em que URL/Título o usuário estava.
        if extension_metadata:
            session_meta = extension_metadata.get("session_meta", {})
            if session_meta:
                page_metadata.initial_url = session_meta.get("page_url")
                page_metadata.current_url = session_meta.get("page_url")
                page_metadata.title = session_meta.get("page_title")
                if page_metadata.initial_url:
                    page_metadata.page_history.append(page_metadata.initial_url)

            # Aproveita a semântica de página capturada pela extensão para logs e contexto
            page_semantics = extension_metadata.get("page_semantics", {})
            if page_semantics:
                logger.info("Aproveitando page_semantics da extensão para enriquecimento estrutural")

        # Mapeamento de constantes internas do protocolo rrweb
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
                        # A refatoração preserva uma versão achatada do DOM com relações
                        # explícitas pai/filho para que o executor siga o plano da fase 1
                        # sem pedir que o LLM "leia" o rrweb inteiro.
                        flattened_dom.clear()
                        dom_map.clear()
                        SessionPreprocessor._flatten_dom_tree(
                            root_node,
                            dom_map,
                            flattened_dom,
                            parent_id=None,
                            depth=0,
                        )

                # --- Ramo B: Meta Eventos (Navegação/Viewport) ---
                elif evt_type == TYPE_META:
                    href = data.get('href')
                    width = data.get('width')

                    if href:
                        current_page_url = href
                        if page_metadata.initial_url is None:
                            page_metadata.initial_url = href
                        page_metadata.current_url = href
                        if not page_metadata.page_history or page_metadata.page_history[-1] != href:
                            page_metadata.page_history.append(href)
                        # Registra transições de URL como ações de navegação
                        actions.append(UserAction(
                            timestamp=delta_ts,
                            action_type='navigation',
                            details=f"URL: {href}"
                        ))
                        raw_actions.append(
                            RawAction(
                                timestamp=delta_ts,
                                action_type="navigation",
                                event_type=evt_type,
                                event_index=idx,
                                page_url=current_page_url,
                                details={"href": href},
                            )
                        )
                        event_index["navigation"].append(idx)
                    elif width:
                        page_metadata.viewport_width = width
                        page_metadata.viewport_height = data.get("height")
                        # Registra mudanças na viewport do usuário
                        actions.append(UserAction(
                            timestamp=delta_ts,
                            action_type='resize',
                            details=f"Viewport: {width}x{data.get('height')}"
                        ))
                        raw_actions.append(
                            RawAction(
                                timestamp=delta_ts,
                                action_type="resize",
                                event_type=evt_type,
                                event_index=idx,
                                page_url=current_page_url,
                                details={"width": width, "height": data.get("height")},
                            )
                        )
                        event_index["resize"].append(idx)

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
                            raw_actions.append(
                                RawAction(
                                    timestamp=delta_ts,
                                    action_type="click",
                                    event_type=evt_type,
                                    source=source,
                                    event_index=idx,
                                    target_id=target_id,
                                    page_url=current_page_url,
                                    x=data.get("x"),
                                    y=data.get("y"),
                                    details={"html": node_html},
                                )
                            )
                            event_index["click"].append(idx)
                            if target_id is not None:
                                event_index[f"target:{target_id}"].append(idx)

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
                            raw_actions.append(
                                RawAction(
                                    timestamp=delta_ts,
                                    action_type="scroll",
                                    event_type=evt_type,
                                    source=source,
                                    event_index=idx,
                                    page_url=current_page_url,
                                    details={
                                        "direction": direction,
                                        "deltaY": delta_y,
                                        "scrollY": scroll_y,
                                    },
                                )
                            )
                            event_index["scroll"].append(idx)

                    # C.4: Redimensionamento de janela (Resize)
                    elif source == SOURCE_RESIZE:
                        width = data.get('width')
                        if width:
                            actions.append(UserAction(
                                timestamp=delta_ts,
                                action_type='resize',
                                details=f"Viewport: {width}x{data.get('height')}"
                            ))
                            raw_actions.append(
                                RawAction(
                                    timestamp=delta_ts,
                                    action_type="resize",
                                    event_type=evt_type,
                                    source=source,
                                    event_index=idx,
                                    page_url=current_page_url,
                                    details={"width": width, "height": data.get("height")},
                                )
                            )
                            event_index["resize"].append(idx)

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
                        raw_actions.append(
                            RawAction(
                                timestamp=delta_ts,
                                action_type="input",
                                event_type=evt_type,
                                source=source,
                                event_index=idx,
                                target_id=target_id,
                                page_url=current_page_url,
                                value=text_val if text_val is not None else None,
                                checked=is_checked if isinstance(is_checked, bool) else None,
                                details={"html": node_context, "text": text_val},
                            )
                        )
                        event_index["input"].append(idx)
                        if target_id is not None:
                            event_index[f"target:{target_id}"].append(idx)
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
            dom_map=dom_map,
            flattened_dom=flattened_dom,
            raw_actions=raw_actions,
            event_index=dict(event_index),
            page_metadata=page_metadata,
        )

    @staticmethod
    def _flatten_dom_tree(
        node: Dict[str, Any],
        dom_map: Dict[int, str],
        flattened_dom: List[FlatDOMNode],
        ancestors: Optional[List[Dict[str, Any]]] = None,
        parent_id: Optional[int] = None,
        depth: int = 0,
    ) -> None:
        """
        Percorre recursivamente a árvore rrweb para construir um DOM simplificado.

        A estrutura achatada resultante substitui a dependência do pipeline
        antigo em snippets soltos de HTML. O agente estrutural recebe apenas a
        visão necessária, enquanto o executor determinístico preserva ids e
        relações estruturais para resolver labels e containers sem heurísticas
        espalhadas.
        """
        ancestors = ancestors or []
        node_id = node.get('id')
        tag_name = node.get('tagName', '').lower()
        
        # Filtro de exclusão: Nós que não contribuem para a análise visual/funcional do LLM
        if tag_name in SessionPreprocessor.IGNORED_TAGS:
            return

        row_context: Dict[str, str] = {}

        def _node_text(current_node: Dict[str, Any]) -> str:
            pieces: List[str] = []
            for child in current_node.get('childNodes', []):
                if child.get('type') == 3:
                    raw_text = normalize_text(child.get('textContent', ''), 80)
                    if raw_text:
                        pieces.append(raw_text)
            return " ".join(pieces).strip()

        def _radio_row_context(current_node: Dict[str, Any]) -> Dict[str, str]:
            if current_node.get('tagName', '').lower() != 'tr':
                return {}
            question_label = None
            scale_labels: List[str] = []
            for child in current_node.get('childNodes', []):
                child_tag = child.get('tagName', '').lower()
                if child_tag != 'td':
                    continue
                child_attrs = child.get('attributes', {}) or {}
                child_text = _node_text(child)
                if not child_text:
                    continue
                child_classes = str(child_attrs.get('class', '') or '')
                if 'item-name' in child_classes.split():
                    question_label = normalize_text(child_text, 80)
                    continue
                scale_labels.append(normalize_text(child_text, 40) or child_text)
            context: Dict[str, str] = {}
            if question_label:
                context['data-question-label'] = question_label
            if scale_labels:
                context['data-scale-labels'] = '|'.join(label for label in scale_labels if label)
            return context

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

            ancestor_context: Dict[str, str] = {}
            for ancestor in reversed(ancestors):
                if ancestor.get('data-question-label') and 'data-question-label' not in ancestor_context:
                    ancestor_context['data-question-label'] = ancestor['data-question-label']
                if ancestor.get('data-scale-labels') and 'data-scale-labels' not in ancestor_context:
                    ancestor_context['data-scale-labels'] = ancestor['data-scale-labels']
                if ancestor_context.get('data-question-label') and ancestor_context.get('data-scale-labels'):
                    break

            row_context = _radio_row_context(node)
            if row_context:
                ancestor_context.update(row_context)

            input_type = str(attributes.get('type', '')).lower()
            if tag_name == 'input' and input_type == 'radio' and ancestor_context.get('data-question-label'):
                question_label = ancestor_context["data-question-label"].replace('"', "'")
                # O radio é composto: a pergunta vive na linha da tabela e deve acompanhar o input
                # para que o pré-processador possa consolidar a seleção em uma única ação semântica.
                attrs_str += f' data-question-label="{question_label}"'
                if ancestor_context.get('data-scale-labels'):
                    scale_labels = ancestor_context["data-scale-labels"].replace('"', "'")
                    attrs_str += f' data-scale-labels="{scale_labels}"'
            
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
            flattened_dom.append(
                FlatDOMNode(
                    node_id=node_id,
                    tag=tag_name,
                    attributes={str(k): str(v) for k, v in attributes.items()},
                    text=normalize_text(text_content.strip(), 120),
                    simplified_html=simplified_html,
                    parent_id=parent_id,
                    depth=depth,
                )
            )
            if parent_id is not None:
                for existing in flattened_dom:
                    if existing.node_id == parent_id:
                        existing.child_ids.append(node_id)
                        break

        # Chamada recursiva para processar os filhos da árvore (Depth-First Search)
        if 'childNodes' in node:
            for child in node['childNodes']:
                next_ancestors = ancestors + [row_context] if row_context else ancestors
                SessionPreprocessor._flatten_dom_tree(
                    child,
                    dom_map,
                    flattened_dom,
                    next_ancestors,
                    parent_id=node_id if node_id and tag_name else parent_id,
                    depth=depth + 1,
                )
