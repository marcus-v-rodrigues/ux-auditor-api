# Worker IO - Documentação

## Visão Geral

O [`worker_io.py`](worker_io.py) é um processo em background assíncrono que:

1. **Consome mensagens** do RabbitMQ (fila `raw_sessions`)
2. **Persiste os dados** no Garage (S3-compatible storage)
3. **Implementa at-least-once delivery** para garantir processamento confiável

## Arquitetura

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   API       │────▶│  RabbitMQ   │────▶│  Worker IO  │
│  (FastAPI)  │     │  (Fila)     │     │             │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                             │
                                             ▼
                                      ┌─────────────┐
                                      │   Garage    │
                                      │ (S3 Storage)│
                                      └─────────────┘
```

## Estrutura de Armazenamento

Os dados são armazenados no Garage com a seguinte estrutura:

```
ux-auditor-sessions/
└── sessions/
    └── {user_id}/
        └── {session_uuid}.json
```

## Configuração

### Variáveis de Ambiente

Adicione as seguintes variáveis ao seu arquivo `.env`:

```bash
# Configuração RabbitMQ
RABBITMQ_URL=amqp://guest:guest@localhost:5672/
RABBITMQ_QUEUE=raw_sessions

# Configuração S3/Garage (Storage)
GARAGE_ENDPOINT=http://localhost:3900
GARAGE_ACCESS_KEY=your_access_key_here
GARAGE_SECRET_KEY=your_secret_key_here
GARAGE_BUCKET=ux-auditor-sessions
GARAGE_REGION=us-east-1
```

### Instalação de Dependências

```bash
pip install -r requirements.txt
```

As dependências incluídas:
- `aio-pika` - Cliente assíncrono para RabbitMQ
- `aioboto3` - Cliente assíncrono para AWS S3 (compatível com Garage)

## Execução

### Localmente

```bash
python worker_io.py
```

### Via Docker Compose

```bash
# Subir todos os serviços
docker-compose up -d

# Verificar logs do worker
docker-compose logs -f worker-io

# Parar todos os serviços
docker-compose down
```

## Funcionalidades

### 1. Consumo Assíncrono RabbitMQ

- Usa [`aio_pika.connect_robust()`](worker_io.py:189) para conexão resiliente
- Reconexão automática em caso de falha do broker
- QoS configurado para processar uma mensagem por vez

### 2. Integração com Garage (S3)

- Cliente [`GarageStorageClient`](worker_io.py:38) usando [`aioboto3`](worker_io.py:19)
- Cria automaticamente o bucket se não existir
- Upload assíncrono com [`put_object()`](worker_io.py:97)

### 3. At-Least-Once Delivery

O worker garante que cada mensagem seja processada pelo menos uma vez:

1. **Recebe mensagem** do RabbitMQ
2. **Processa e faz upload** para o Garage
3. **Só envia ACK** após confirmação de sucesso
4. **Em caso de falha**: não envia ACK, mensagem é reprocessada

### 4. Resiliência

- **Reconexão automática** ao RabbitMQ
- **Limite de reentregas** (`x-delivery-limit: 5`) para evitar loops infinitos
- **Shutdown gracioso** via sinais SIGINT/SIGTERM
- **Logging detalhado** para troubleshooting

## Formato da Mensagem

As mensagens enviadas para a fila `raw_sessions` devem seguir este formato JSON:

```json
{
  "user_id": "user_123",
  "session_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2024-01-01T12:00:00Z",
  "events": [...],
  "metadata": {...}
}
```

**Campos obrigatórios:**
- `user_id`: Identificador do usuário
- `session_uuid`: UUID único da sessão

## Logging

O worker gera logs em dois locais:

1. **Console** (stdout) - para visualização em tempo real
2. **Arquivo** `worker_io.log` - para persistência

Níveis de log:
- `INFO`: Operações normais (conexão, upload, etc.)
- `WARNING`: Situações anormais que não impedem operação
- `ERROR`: Erros que requerem atenção

### Exemplo de Logs

```
2024-01-01 12:00:00 - INFO - Conectando ao RabbitMQ: amqp://guest:guest@localhost:5672/
2024-01-01 12:00:01 - INFO - Conexão RabbitMQ estabelecida com sucesso.
2024-01-01 12:00:01 - INFO - Configurando canal e fila...
2024-01-01 12:00:01 - INFO - Fila 'raw_sessions' configurada com sucesso.
2024-01-01 12:00:01 - INFO - Iniciando consumo da fila 'raw_sessions'...
2024-01-01 12:00:05 - INFO - Mensagem recebida | Delivery Tag: 1 | Message ID: msg_123
2024-01-01 12:00:05 - INFO - Processando sessão: user_id=user_123, session_uuid=550e8400-e29b-41d4-a716-446655440000
2024-01-01 12:00:05 - INFO - Iniciando upload para Garage: sessions/user_123/550e8400-e29b-41d4-a716-446655440000.json (2048 bytes)
2024-01-01 12:00:06 - INFO - Upload concluído com sucesso: sessions/user_123/550e8400-e29b-41d4-a716-446655440000.json
2024-01-01 12:00:06 - INFO - Processamento concluído com sucesso | Delivery Tag: 1
```

## Troubleshooting

### Erro: Conexão RabbitMQ falhou

**Solução:** Verifique se o RabbitMQ está rodando e a URL está correta.

```bash
# Verificar status do RabbitMQ
docker-compose ps rabbitmq

# Ver logs
docker-compose logs rabbitmq
```

### Erro: Upload para Garage falhou

**Solução:** Verifique as credenciais e se o Garage está acessível.

```bash
# Verificar status do Garage
docker-compose ps garage

# Ver logs
docker-compose logs garage

# Testar conexão manual
curl http://localhost:3900
```

### Mensagens não sendo processadas

**Solução:** Verifique se a fila existe e há mensagens pendentes.

```bash
# Acessar interface de gestão do RabbitMQ
# http://localhost:15672 (usuário: guest, senha: guest)
```

### Worker reiniciando constantemente

**Solução:** Verifique os logs para identificar o erro raiz.

```bash
docker-compose logs worker-io
```

## Monitoramento

### Métricas Importantes

- **Mensagens processadas**: Contagem de mensagens com sucesso
- **Taxa de erro**: Mensagens que falharam no upload
- **Tempo de processamento**: Duração média por mensagem
- **Tamanho da fila**: Mensagens pendentes

### Ferramentas Sugeridas

- **RabbitMQ Management UI**: http://localhost:15672
- **Prometheus + Grafana**: Para métricas avançadas
- **ELK Stack**: Para análise de logs

## Segurança

### Recomendações

1. **Credenciais**: Nunca commitar `.env` no versionamento
2. **HTTPS**: Use HTTPS em produção para o endpoint do Garage
3. **Autenticação**: Configure autenticação adequada no RabbitMQ
4. **Network Isolation**: Use redes Docker separadas para diferentes ambientes

### Variáveis Sensíveis

As seguintes variáveis contêm informações sensíveis:
- `GARAGE_ACCESS_KEY`
- `GARAGE_SECRET_KEY`
- `RABBITMQ_URL` (contém credenciais)

## Escalabilidade

### Escala Horizontal

Para escalar o worker, adicione mais instâncias no [`docker-compose.yml`](docker-compose.yml):

```yaml
worker-io:
  deploy:
    replicas: 3  # Executa 3 instâncias do worker
```

### Considerações

- Cada instância processa mensagens em paralelo
- O RabbitMQ distribui mensagens entre consumidores
- Monitore a fila para evitar backlog

## Manutenção

### Atualização do Worker

```bash
# Parar o worker
docker-compose stop worker-io

# Fazer pull da nova imagem
docker-compose pull worker-io

# Reiniciar
docker-compose up -d worker-io
```

### Limpeza de Logs

```bash
# Limpar arquivo de log
> worker_io.log

# Ou configurar logrotate para rotação automática
```

## Suporte

Para problemas ou dúvidas:
1. Verifique os logs do worker
2. Consulte a documentação do [aio-pika](https://aio-pika.readthedocs.io/)
3. Consulte a documentação do [aioboto3](https://aioboto3.readthedocs.io/)
4. Consulte a documentação do [Garage](https://garagehq.deuxfleurs.fr/)
