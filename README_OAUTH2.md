# UX Auditor API - Resource Server OAuth2

Este documento descreve a implementação do Resource Server OAuth2 para a UX Auditor API, que valida tokens JWT emitidos pelo janus-idp (RS256) e ingere dados de telemetria no RabbitMQ.

## Visão Geral

A API foi transformada em um Resource Server OAuth2 robusto com as seguintes capacidades:

- **Validação de Token JWT RS256**: Valida tokens emitidos pelo janus-idp usando criptografia assimétrica
- **Suporte a JWKS**: Validação dinâmica de chaves públicas via endpoint JWKS
- **Chave Pública Estática**: Opção para usar chave pública estática quando JWKS não está disponível
- **Endpoints Protegidos**: O endpoint `/ingest` requer autenticação válida
- **Integração RabbitMQ**: Publica eventos de telemetria de forma assíncrona na fila de mensagens
- **Rastreamento de Sessão**: Associa cada ingestão a um UUID de sessão único e ID de usuário

## Arquitetura

```
┌─────────────┐         ┌─────────────────┐         ┌─────────────┐
│  janus-idp  │────────▶│  UX Auditor API │────────▶│  RabbitMQ   │
│ (Emissor de │  JWT    │  (Resource      │  Eventos │  (Fila de   │
│  Tokens     │  RS256) │   Server)       │         │   Mensagens)│
│  JWKS       │         │                 │         │             │
└─────────────┘         └─────────────────┘         └─────────────┘
                                │
                                ▼
                        ┌───────────────┐
                        │  Processamento│
                        │  Workers      │
                        └───────────────┘
```

## Componentes

### 1. Módulo de Autenticação ([`services/auth.py`](services/auth.py:1))

Fornece validação de token OAuth2 com RS256 e suporte a JWKS:

- **OAuth2PasswordBearer**: Extrai token Bearer do cabeçalho Authorization
- **Validação RS256**: Valida assinatura usando chave pública (assimétrica)
- **Suporte JWKS**: Busca chaves públicas dinamicamente do endpoint JWKS
- **Cache de JWKS**: Cache de 5 minutos para chaves públicas
- **Verificação de Claims**: Verifica `exp` (expiração), `iss` (emissor) e `sub` (ID do usuário)
- **Respostas 401 Automáticas**: Retorna não autorizado para tokens inválidos

#### Funções Principais

- [`get_current_user()`](services/auth.py:200): Dependência principal para endpoints protegidos
- [`get_current_user_optional()`](services/auth.py:242): Variante opcional de autenticação
- [`get_jwks_public_key()`](services/auth.py:43): Obtém chave pública do endpoint JWKS
- [`decode_jwt_token()`](services/auth.py:115): Decodifica e valida token JWT RS256

### 2. Módulo de Configuração ([`config.py`](config.py:1))

Configuração centralizada usando `pydantic-settings`:

- **JWT_JWKS_URL**: URL do endpoint JWKS para validação dinâmica
- **JWT_PUBLIC_KEY**: Chave pública estática (alternativa ao JWKS)
- **JWT_ALGORITHM**: Algoritmo de assinatura (RS256 para Janus)
- **JWT_ISSUER**: Emissor esperado (URL do realm Janus)
- **RABBITMQ_URL**: String de conexão RabbitMQ
- **RABBITMQ_QUEUE**: Nome da fila para sessões brutas

### 3. Endpoint de Ingestão ([`main.py`](main.py:220))

O endpoint [`POST /ingest`](main.py:220):

1. **Autenticação**: Requer token JWT válido via dependência [`get_current_user`](services/auth.py:200)
2. **UUID de Sessão**: Gera identificador único para cada ingestão
3. **Publicação de Mensagem**: Envia eventos para RabbitMQ com metadados
4. **Resposta**: Retorna UUID da sessão e confirmação

## Configuração

### 1. Instalar Dependências

```bash
pip install -r requirements.txt
```

### 2. Configurar Variáveis de Ambiente

Copie o arquivo de ambiente de exemplo e configure:

```bash
cp .env.example .env
```

Edite `.env` com sua configuração:

#### Opção A: Usando JWKS (Recomendado para Janus)

```env
# URL do endpoint JWKS do Janus
JWT_JWKS_URL=https://seu-janus-idp.com/realms/master/protocol/openid-connect/certs

# Algoritmo JWT
JWT_ALGORITHM=RS256

# Emissor JWT (URL do realm)
JWT_ISSUER=https://seu-janus-idp.com/realms/master

# Configuração RabbitMQ
RABBITMQ_URL=amqp://guest:guest@localhost:5672/
RABBITMQ_QUEUE=raw_sessions

# Configuração da Aplicação
APP_HOST=0.0.0.0
APP_PORT=8000
```

#### Opção B: Usando Chave Pública Estática

```env
# Chave pública estática (PEM formatado)
JWT_PUBLIC_KEY=-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...
-----END PUBLIC KEY-----

# Algoritmo JWT
JWT_ALGORITHM=RS256

# Emissor JWT
JWT_ISSUER=https://seu-janus-idp.com/realms/master

# Configuração RabbitMQ
RABBITMQ_URL=amqp://guest:guest@localhost:5672/
RABBITMQ_QUEUE=raw_sessions

# Configuração da Aplicação
APP_HOST=0.0.0.0
APP_PORT=8000
```

### 3. Obter URL do JWKS do Janus

Para encontrar a URL do JWKS do seu Janus IDP:

1. Acesse o realm do Janus
2. Vá em Realm Settings → General
3. Copie a URL do realm (ex: `https://seu-janus-idp.com/realms/master`)
4. Adicione `/protocol/openid-connect/certs` ao final

Exemplo final:
```
https://seu-janus-idp.com/realms/master/protocol/openid-connect/certs
```

### 4. Iniciar RabbitMQ

```bash
# Usando Docker
docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3-management

# Ou usando instalação local
sudo systemctl start rabbitmq-server
```

### 5. Executar a API

```bash
python main.py
```

Ou usando uvicorn diretamente:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Uso

### Autenticação

Todas as requisições para endpoints protegidos devem incluir um token JWT válido no cabeçalho Authorization:

```http
Authorization: Bearer <JWT_TOKEN>
```

### Ingerir Eventos de Sessão

**Endpoint**: `POST /ingest`

**Cabeçalhos**:
```
Content-Type: application/json
Authorization: Bearer <JWT_TOKEN>
```

**Corpo**:
```json
[
  {
    "type": "IncrementalSnapshot",
    "data": {},
    "timestamp": 1234567890
  },
  {
    "type": "MouseMove",
    "data": {
      "x": 100,
      "y": 200
    },
    "timestamp": 1234567891
  }
]
```

**Resposta**:
```json
{
  "status": "success",
  "message": "Eventos da sessão ingeridos com sucesso",
  "session_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "user_id": "user-123",
  "events_count": 2
}
```

### Exemplo Usando cURL

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..." \
  -d '[{"type": "MouseMove", "data": {"x": 100, "y": 200}, "timestamp": 1234567890}]'
```

### Exemplo Usando Python

```python
import requests
import json

url = "http://localhost:8000/ingest"
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."
}
events = [
    {"type": "MouseMove", "data": {"x": 100, "y": 200}, "timestamp": 1234567890}
]

response = requests.post(url, headers=headers, json=events)
print(response.json())
```

## Validação de Token

A API valida os seguintes claims JWT:

| Claim | Obrigatório | Descrição |
|-------|-------------|-----------|
| `sub` | Sim | ID do usuário (subject) |
| `exp` | Sim | Timestamp de expiração (epoch Unix) |
| `iss` | Sim | Emissor (deve corresponder a `JWT_ISSUER`) |
| `kid` | Sim (JWKS) | Key ID para identificar a chave pública |

### Exemplo de Estrutura de Token

```json
{
  "sub": "user-123",
  "exp": 1734567890,
  "iss": "https://seu-janus-idp.com/realms/master",
  "iat": 1734564290,
  "preferred_username": "joao.silva",
  "email": "joao.silva@exemplo.com"
}
```

## Tratamento de Erros

### 401 Unauthorized

Retornado quando:
- Token está ausente
- Token está malformado
- Assinatura do token é inválida
- Token expirou
- Emissor não corresponde ao valor esperado
- Chave pública não encontrada (JWKS)

**Resposta**:
```json
{
  "detail": "Token expirado"
}
```

### 500 Internal Server Error

Retornado quando:
- Falha na conexão JWKS
- Falha na conexão RabbitMQ
- Falha na publicação de mensagem
- Configuração JWT incompleta

**Resposta**:
```json
{
  "detail": "Falha ao obter JWKS: connection refused"
}
```

## Formato de Mensagem RabbitMQ

Mensagens publicadas na fila têm a seguinte estrutura:

```json
{
  "user_id": "user-123",
  "session_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "events": [
    {
      "type": "MouseMove",
      "data": {"x": 100, "y": 200},
      "timestamp": 1234567890
    }
  ],
  "timestamp": "2024-01-15T10:30:00.000000"
}
```

## Considerações de Segurança

1. **RS256 vs HS256**: RS256 usa criptografia assimétrica (chave pública/privada), mais seguro para produção
2. **HTTPS**: Use HTTPS em produção para evitar interceptação de tokens
3. **Expiração de Token**: Certifique-se de que tokens tem tempos de expiração razoáveis
4. **Autenticação RabbitMQ**: Use credenciais fortes para RabbitMQ
5. **CORS**: Configure CORS apropriadamente para seus domínios frontend
6. **Cache JWKS**: O cache de 5 minutos reduz chamadas ao endpoint JWKS
7. **Verificação de Emissor**: Sempre verifique o claim `iss` para evitar ataques de substituição

## Testes

### Testar com Token Válido do Janus

1. Obtenha um token do Janus:
```bash
curl -X POST https://seu-janus-idp.com/realms/master/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=password&username=usuario&senha=senha&client_id=seu-client-id"
```

2. Use o token para testar:
```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <SEU_TOKEN>" \
  -d '[{"type": "MouseMove", "data": {"x": 100, "y": 200}, "timestamp": 1234567890}]'
```

### Testar com Token Inválido

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer token-invalido" \
  -d '[]'
```

Resposta esperada: `401 Unauthorized`

## Monitoramento

### Verificação de Saúde

```bash
curl http://localhost:8000/docs
```

Acesse a documentação FastAPI em `http://localhost:8000/docs`

### Gerenciamento RabbitMQ

Se estiver usando RabbitMQ Management UI:
```
http://localhost:15672
Usuário: guest
Senha: guest
```

### Logs de JWKS

A API imprime mensagens de conexão ao RabbitMQ no startup:
```
✓ Conectado ao RabbitMQ em amqp://guest:guest@localhost:5672/
```

## Solução de Problemas

### Conexão Recusada ao RabbitMQ

- Certifique-se de que o RabbitMQ está rodando
- Verifique `RABBITMQ_URL` no `.env`
- Verifique a conectividade de rede

### Erros de Token Inválido

- Verifique se `JWT_JWKS_URL` ou `JWT_PUBLIC_KEY` está configurado
- Verifique se `JWT_ISSUER` corresponde ao emissor do token
- Verifique a expiração do token
- Teste a URL do JWKS diretamente no navegador

### Erros de JWKS

- Verifique se a URL do JWKS está acessível
- Teste: `curl https://seu-janus-idp.com/realms/master/protocol/openid-connect/certs`
- Verifique se o token contém o claim `kid` no header
- Verifique se a chave correspondente ao `kid` existe no JWKS

### Erros de Importação

- Certifique-se de que todas as dependências estão instaladas: `pip install -r requirements.txt`
- Verifique a versão do Python (3.8+ recomendado)

## Diferenças entre RS256 e HS256

| Característica | RS256 (Janus) | HS256 |
|---------------|---------------|-------|
| Tipo | Assimétrico | Simétrico |
| Chaves | Pública/Privada | Segredo compartilhado |
| Segurança | Mais seguro | Menos seguro |
| Rotação de Chaves | Fácil via JWKS | Requer coordenação |
| Uso | Produção | Desenvolvimento/Testes |

## Próximos Passos

1. **Implementar Workers de Consumo**: Criar workers para processar mensagens da fila
2. **Adicionar Rate Limiting**: Proteger contra abuso
3. **Implementar Logging**: Adicionar logging estruturado para monitoramento
4. **Adicionar Métricas**: Integrar Prometheus ou similar para observabilidade
5. **Integração com Banco de Dados**: Armazenar dados de sessões processadas
6. **Refresh de Token**: Implementar mecanismo de refresh de token
7. **Health Check Endpoint**: Adicionar endpoint para verificar status do JWKS

## Licença

[Sua Licença Aqui]
