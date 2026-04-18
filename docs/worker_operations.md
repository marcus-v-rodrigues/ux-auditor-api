# Operações do Worker (Background Processing)

O **Worker IO** é o componente responsável pelo processamento pesado e assíncrono das sessões de telemetria. Ele garante que a API de ingestão permaneça rápida e que as análises complexas (ML/LLM) não bloqueiem o usuário.

## 1. Responsabilidades

O worker (`worker_io.py`) opera em um ciclo contínuo de consumo de mensagens:
1.  **Consumo Garantido:** Utiliza o protocolo AMQP com Confirmações de Entrega (ACKs) para garantir que nenhuma sessão seja perdida.
2.  **Persistência S3:** Salva o JSON bruto original no **Garage/MinIO** antes de qualquer processamento, servindo como uma "caixa preta" para auditorias futuras.
3.  **Execução do Pipeline:** Orquestra a chamada de todos os módulos de análise (Pré-processamento, Fase 1, Execução, Heurísticas, ML, Fase 2).
4.  **Sincronização de Estado:** Atualiza o banco de dados PostgreSQL via SQLModel com o resultado final e status do job (`queued`, `processing`, `completed`, `failed`).

## 2. Arquitetura de Mensageria

```mermaid
graph LR
    subgraph "API Ingestion"
        A[POST /ingest] --> B{RabbitMQ Exchange}
    end
    
    subgraph "Worker Cluster"
        B --> C[Worker Instance 1]
        B --> D[Worker Instance 2]
        B --> E[Worker Instance N]
    end
    
    C & D & E --> F[(S3 Storage)]
    C & D & E --> G[(PostgreSQL)]
```

### Configurações de Resiliência
- **Quorum Queues:** Utilizadas para garantir consistência de dados entre instâncias do RabbitMQ.
- **Prefetch Count:** Configurado como `1` para evitar que um worker fique sobrecarregado enquanto outros estão ociosos.
- **Retry Policy:** Mensagens que falham são re-enfileiradas até 5 vezes (`x-delivery-limit`).

## 3. Comandos Operacionais

### Execução Local
```bash
python worker_io.py
```

### Execução via Docker
O worker é definido como um serviço no `docker-compose.yml`:
```yaml
worker:
  build: .
  command: python worker_io.py
  environment:
    - RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/
    - GARAGE_ENDPOINT=http://garage:3900
```

## 4. Monitoramento e Logs

O worker gera logs detalhados em `logs/worker.log`. Pontos críticos para monitoramento:
- **Startup:** Verificação de conexão com S3, DB e RabbitMQ.
- **Pipeline Start/End:** Tempo levado para processar cada sessão (importante para tuning de timeouts).
- **LLM Failures:** Erros de quota ou latência na API da OpenAI.

## 5. Escalabilidade Horizontal

Para aumentar o throughput de processamento, basta subir mais instâncias do worker. Como o processamento de sessões é independente, o sistema escala de forma linear:

```bash
docker-compose up -d --scale worker=5
```
