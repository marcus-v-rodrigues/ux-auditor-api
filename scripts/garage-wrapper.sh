#!/bin/sh
# ===========================================
# Garage Wrapper Script
# ===========================================
# Este script implementa o padrão "Inversão de Controle" para o Garage.
# 
# Estratégia:
# 1. Inicia o Garage server em background
# 2. Aguarda o healthcheck estar disponível
# 3. Cria chave e bucket usando a CLI interna do Garage
# 4. Extrai as credenciais geradas (Access Key e Secret Key)
# 5. Salva as credenciais em /secrets/garage.env para consumo pela aplicação
# 6. Mantém o container rodando em foreground
#
# IMPORTANTE: O Garage não aceita chaves estáticas pré-definidas.
# Este script permite que o Garage gere suas próprias credenciais
# e as compartilhe com a aplicação Python via volume compartilhado.

set -e

# ===========================================
# Variáveis de Ambiente
# ===========================================
SECRETS_DIR="/secrets"
SECRETS_FILE="$SECRETS_DIR/garage.env"
KEY_NAME="ux-auditor-key"
BUCKET_NAME="${GARAGE_BUCKET:-ux-auditor-sessions}"
GARAGE_PID=""

# ===========================================
# Funções Auxiliares
# ===========================================

log_info() {
    echo "ℹ️  $1"
}

log_success() {
    echo "✅ $1"
}

log_warning() {
    echo "⚠️  $1"
}

log_error() {
    echo "❌ $1"
}

# Aguarda o Garage estar pronto via healthcheck
wait_for_garage() {
    log_info "Aguardando o serviço Garage estar pronto..."
    
    MAX_RETRIES=30
    RETRY_COUNT=0
    
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if wget -q --spider http://localhost:3900/health 2>/dev/null; then
            log_success "Garage está pronto!"
            return 0
        fi
        
        RETRY_COUNT=$((RETRY_COUNT + 1))
        log_info "Tentativa $RETRY_COUNT de $MAX_RETRIES - Garage não está pronto ainda..."
        sleep 2
    done
    
    log_error "Timeout aguardando Garage. Verifique se o serviço está rodando."
    return 1
}

# Cria a chave de acesso
create_key() {
    log_info "Provisionando chave de acesso..."
    
    # Verifica se a chave já existe
    if garage key info "$KEY_NAME" > /dev/null 2>&1; then
        log_warning "Chave já existe: $KEY_NAME"
    else
        # Cria uma nova chave - o Garage gera access_key e secret automaticamente
        garage key create "$KEY_NAME" 2>/dev/null || {
            log_warning "Erro ao criar chave, verificando se já existe..."
            if ! garage key info "$KEY_NAME" > /dev/null 2>&1; then
                log_error "Falha ao criar chave"
                return 1
            fi
        }
        log_success "Chave criada: $KEY_NAME"
    fi
    
    return 0
}

# Cria o bucket
create_bucket() {
    log_info "Provisionando bucket..."
    
    # Verifica se o bucket já existe
    if garage bucket info "$BUCKET_NAME" > /dev/null 2>&1; then
        log_warning "Bucket já existe: $BUCKET_NAME"
    else
        # Cria o bucket
        garage bucket create "$BUCKET_NAME" 2>/dev/null || {
            if ! garage bucket info "$BUCKET_NAME" > /dev/null 2>&1; then
                log_error "Falha ao criar bucket: $BUCKET_NAME"
                return 1
            fi
            log_warning "Bucket já existe: $BUCKET_NAME"
        }
        log_success "Bucket criado: $BUCKET_NAME"
    fi
    
    return 0
}

# Vincula a chave ao bucket com permissões
allow_bucket() {
    log_info "Vinculando chave ao bucket com permissões de leitura e escrita..."
    
    # Vincula a chave ao bucket com permissões de leitura e escrita
    garage bucket allow "$BUCKET_NAME" --read --write --key "$KEY_NAME" 2>/dev/null || {
        log_warning "Permissões já configuradas ou erro ao vincular."
    }
    
    log_success "Permissões configuradas para o bucket: $BUCKET_NAME"
    return 0
}

# Extrai e salva as credenciais
save_credentials() {
    log_info "Extraindo credenciais geradas..."
    
    # Cria o diretório de secrets se não existir
    mkdir -p "$SECRETS_DIR"
    
    # Extrai as credenciais da chave criada
    KEY_INFO=$(garage key info "$KEY_NAME" 2>/dev/null || true)
    
    if [ -z "$KEY_INFO" ]; then
        log_error "Não foi possível obter informações da chave"
        return 1
    fi
    
    # Extração robusta usando awk (compatível com Alpine/BusyBox)
    ACCESS_KEY_ID=$(echo "$KEY_INFO" | awk '/Key ID:/ {print $3}')
    SECRET_KEY=$(echo "$KEY_INFO" | awk '/Secret key:/ {print $3}')
    
    if [ -z "$ACCESS_KEY_ID" ] || [ -z "$SECRET_KEY" ]; then
        log_error "Não foi possível extrair as credenciais."
        log_info "Output do comando 'garage key info':"
        echo "$KEY_INFO"
        return 1
    fi
    
    # Salva as credenciais no arquivo
    cat > "$SECRETS_FILE" << EOF
# ===========================================
# Credenciais do Garage - Gerado automaticamente
# ===========================================
# Este arquivo foi gerado pelo garage-wrapper.sh
# Data: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

GARAGE_ACCESS_KEY=${ACCESS_KEY_ID}
GARAGE_SECRET_KEY=${SECRET_KEY}
GARAGE_BUCKET=${BUCKET_NAME}
EOF
    
    log_success "Credenciais salvas em: $SECRETS_FILE"
    
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "🎉 Provisionamento do Garage concluído com sucesso!"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    echo "📋 Credenciais geradas:"
    echo "   - Key Name:    $KEY_NAME"
    echo "   - Access Key:  $ACCESS_KEY_ID"
    echo "   - Secret Key:  $SECRET_KEY"
    echo "   - Bucket:      $BUCKET_NAME"
    echo "   - Permissões:  leitura e escrita"
    echo ""
    echo "📁 Credenciais salvas em: $SECRETS_FILE"
    echo "🔗 A aplicação Python carregará estas credenciais automaticamente"
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    
    return 0
}

# ===========================================
# Função de cleanup para saída graciosa
# ===========================================
cleanup() {
    log_info "Recebido sinal de encerramento..."
    if [ -n "$GARAGE_PID" ] && kill -0 "$GARAGE_PID" 2>/dev/null; then
        log_info "Encerrando processo Garage (PID: $GARAGE_PID)..."
        kill "$GARAGE_PID" 2>/dev/null || true
        wait "$GARAGE_PID" 2>/dev/null || true
    fi
    exit 0
}

# Captura sinais de encerramento
trap cleanup SIGTERM SIGINT SIGQUIT

# ===========================================
# Fluxo Principal
# ===========================================
main() {
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "🚀 Iniciando Garage com provisionamento automático"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    
    # 1. Iniciar o Garage server em background
    log_info "Iniciando Garage server em background..."
    /garage server &
    GARAGE_PID=$!
    log_info "Garage iniciado com PID: $GARAGE_PID"
    
    # 2. Aguardar o healthcheck
    if ! wait_for_garage; then
        log_error "Falha ao aguardar Garage"
        exit 1
    fi
    
    # 3. Criar chave de acesso
    if ! create_key; then
        log_error "Falha ao criar chave"
        exit 1
    fi
    
    # 4. Criar bucket
    if ! create_bucket; then
        log_error "Falha ao criar bucket"
        exit 1
    fi
    
    # 5. Vincular chave ao bucket
    if ! allow_bucket; then
        log_error "Falha ao vincular chave ao bucket"
        exit 1
    fi
    
    # 6. Extrair e salvar credenciais
    if ! save_credentials; then
        log_error "Falha ao salvar credenciais"
        exit 1
    fi
    
    # 7. Manter o container rodando em foreground
    log_info "Garage server rodando em foreground (PID: $GARAGE_PID)..."
    log_info "Pressione Ctrl+C para encerrar"
    
    # Aguarda o processo do Garage indefinidamente
    wait "$GARAGE_PID"
}

# Executa o fluxo principal
main