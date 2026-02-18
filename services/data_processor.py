import logging
from typing import List, Dict, Optional, Any, Set
from pydantic import BaseModel, Field
from models.models import RRWebEvent

# Configuração de Logs
logger = logging.getLogger("ux_auditor")

# --- Modelagem de Dados (Buckets) ---

class KinematicVector(BaseModel):
    """
    Bucket Otimizado para ML (Isolation Forest).
    Focado exclusivamente em geometria e tempo para detecção de anomalias.
    """
    timestamp: int = Field(..., description="Delta em ms relativo ao início da sessão")
    x: int
    y: int

class UserAction(BaseModel):
    """
    Bucket Otimizado para LLM (Geração de Narrativa).
    Focado em intenção semântica e contexto de interface.
    """
    timestamp: int
    action_type: str = Field(..., description="'click' | 'input' | 'navigation' | 'resize'")
    target_id: Optional[int] = None
    details: Optional[str] = Field(None, description="Contexto rico: HTML simplificado, URL, ou valor input")

class ProcessedSession(BaseModel):
    """
    Container final agnóstico.
    """
    initial_timestamp: int
    total_duration: int
    kinematics: List[KinematicVector] = Field(default_factory=list)
    actions: List[UserAction] = Field(default_factory=list)
    dom_map: Dict[int, str] = Field(default_factory=dict, description="Lookup O(1) de ID -> HTML Simplificado")

# --- Lógica de Processamento (O(N) - Single Pass) ---

class SessionPreprocessor:
    # Tags que devem ser ignoradas para economizar tokens do LLM
    IGNORED_TAGS: Set[str] = {'script', 'style', 'link', 'meta', 'noscript'}
    
    # Atributos HTML relevantes para contexto (whitelist)
    RELEVANT_ATTRS: Set[str] = {'id', 'class', 'name', 'type', 'aria-label', 'placeholder', 'value', 'href', 'role'}

    @staticmethod
    def process(events: List[RRWebEvent]) -> ProcessedSession:
        """
        Processa uma lista de eventos brutos do rrweb em estruturas otimizadas para análise.
        
        Executa um único loop O(N) para extrair:
        - Vetores cinemáticos (timestamp, x, y) para detecção de anomalias via ML
        - Ações do usuário (cliques, inputs, navegação) para geração de narrativa via LLM
        - Mapa DOM simplificado para contexto enriquecido das interações
        
        Args:
            events (List[RRWebEvent]): Lista de eventos brutos capturados pelo rrweb.
            
        Returns:
            ProcessedSession: Objeto contendo os dados processados organizados em buckets otimizados.
        """
        if not events:
            return ProcessedSession(initial_timestamp=0, total_duration=0)

        # 1. Setup Temporal
        # Ordenamos preventivamente por segurança, embora o rrweb garanta ordem na maioria das vezes
        # events.sort(key=lambda x: x.timestamp) 
        
        start_time = events[0].timestamp
        last_timestamp = start_time
        
        kinematics: List[KinematicVector] = []
        actions: List[UserAction] = []
        dom_map: Dict[int, str] = {}

        # Mapeamento de Constantes RRWeb v2
        TYPE_DOM_SNAPSHOT = 2
        TYPE_INCREMENTAL = 3
        TYPE_META = 4
        
        SOURCE_MOUSE_MOVE = 1
        SOURCE_MOUSE_INTERACTION = 2
        SOURCE_INPUT = 5
        
        # Tipos de interação de mouse
        INTERACTION_CLICK = 2

        # --- LOOP ÚNICO (O(N)) ---
        for event in events:
            current_raw_ts = event.timestamp
            delta_ts = current_raw_ts - start_time
            last_timestamp = current_raw_ts
            
            evt_type = event.type
            data = event.data or {}

            # --- Ramo A: Reconstrução de Contexto (DOM) ---
            if evt_type == TYPE_DOM_SNAPSHOT:
                root_node = data.get('node')
                if root_node:
                    SessionPreprocessor._flatten_dom_tree(root_node, dom_map)

            # --- Ramo B: Meta Eventos (Navegação/Viewport) ---
            elif evt_type == TYPE_META:
                href = data.get('href')
                width = data.get('width')
                
                if href:
                    actions.append(UserAction(
                        timestamp=delta_ts,
                        action_type='navigation',
                        details=f"URL: {href}"
                    ))
                elif width:
                    actions.append(UserAction(
                        timestamp=delta_ts,
                        action_type='resize',
                        details=f"Viewport: {width}x{data.get('height')}"
                    ))

            # --- Ramo C: Eventos Incrementais ---
            elif evt_type == TYPE_INCREMENTAL:
                source = data.get('source')

                # C.1: Cinemática (ML) - Descompactando posições
                if source == SOURCE_MOUSE_MOVE:
                    positions = data.get('positions', [])
                    for pos in positions:
                        # pos format: [x, y, id, timeOffset]
                        if len(pos) >= 2:
                            x, y = pos[0], pos[1]
                            # timeOffset é relativo ao timestamp do evento pai
                            p_offset = pos[3] if len(pos) >= 4 else 0
                            
                            # Validar coordenadas (ignorar negativos extremos ou outliers óbvios)
                            if x >= 0 and y >= 0:
                                kinematics.append(KinematicVector(
                                    timestamp=delta_ts + p_offset,
                                    x=x,
                                    y=y
                                ))

                # C.2: Ações do Usuário (LLM) - Cliques
                elif source == SOURCE_MOUSE_INTERACTION:
                    i_type = data.get('type')
                    if i_type == INTERACTION_CLICK:
                        target_id = data.get('id')
                        # Lookup O(1) no mapa gerado
                        node_html = dom_map.get(target_id, "unknown_element")
                        
                        actions.append(UserAction(
                            timestamp=delta_ts,
                            action_type='click',
                            target_id=target_id,
                            details=f"Element: {node_html} | Coords: ({data.get('x')}, {data.get('y')})"
                        ))

                # C.3: Ações do Usuário (LLM) - Inputs
                elif source == SOURCE_INPUT:
                    target_id = data.get('id')
                    text_val = data.get('text', '')
                    is_checked = data.get('isChecked')
                    
                    details = ""
                    if is_checked is not None:
                        details = f"Checked: {is_checked}"
                    else:
                        # Privacidade e Economia: Truncar inputs longos
                        safe_text = text_val[:40] + "..." if len(text_val) > 40 else text_val
                        details = f"Typed: '{safe_text}'"

                    # Node enrichment se possível
                    node_context = dom_map.get(target_id, "")
                    if node_context:
                        details += f" on {node_context}"

                    actions.append(UserAction(
                        timestamp=delta_ts,
                        action_type='input',
                        target_id=target_id,
                        details=details
                    ))

        # 3. Consolidação Final
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
        Objetivo: Criar uma representação HTML 'token-efficient' para o LLM.
        Ignora nós irrelevantes (estilos, scripts, metadados ocultos).
        """
        node_id = node.get('id')
        tag_name = node.get('tagName', '').lower()
        
        # Regra de exclusão: Ignorar CSS massivo e scripts
        if tag_name in SessionPreprocessor.IGNORED_TAGS:
            return

        # Se for um nó de texto (type 3), não tem ID próprio relevante para clique, 
        # mas o conteúdo é útil para o pai. Aqui focamos em nós estruturais (type 2).
        if node_id and tag_name:
            attributes = node.get('attributes', {})
            
            # Construção seletiva de atributos
            attrs_str = ""
            for k, v in attributes.items():
                if k in SessionPreprocessor.RELEVANT_ATTRS:
                    # Truncar valores de atributos muito longos (ex: URLs base64 ou paths complexos)
                    v_str = str(v)
                    if len(v_str) > 50: 
                        v_str = v_str[:47] + "..."
                    attrs_str += f' {k}="{v_str}"'
            
            # Tentar extrair conteúdo de texto imediato (child text nodes)
            text_content = ""
            children = node.get('childNodes', [])
            for child in children:
                if child.get('type') == 3: # Text Node
                    raw_text = child.get('textContent', '').strip()
                    if raw_text:
                        text_content += raw_text + " "
            
            if len(text_content) > 30:
                text_content = text_content[:30] + "..."

            # Formato Compacto: <button id="12" class="btn">Enviar</button>
            simplified_html = f"<{tag_name}{attrs_str}>{text_content.strip()}</{tag_name}>"
            dom_map[node_id] = simplified_html

        # Recursão
        if 'childNodes' in node:
            for child in node['childNodes']:
                SessionPreprocessor._flatten_dom_tree(child, dom_map)