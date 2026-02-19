# Use Python 3.11 slim como imagem base
FROM python:3.11-slim

# Define o diretório de trabalho
WORKDIR /app

# Instala dependências do sistema incluindo Node.js para o Prisma CLI
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Instala o Prisma CLI globalmente
RUN npm install -g prisma

# Copia requirements.txt primeiro para aproveitar o cache do Docker
COPY requirements.txt .

# Instala dependências Python
RUN pip install --no-cache-dir -r requirements.txt

# Copia arquivos de configuração do Prisma
COPY prisma ./prisma
COPY prisma.config.ts .

# Gera o cliente do Prisma
RUN prisma generate

# Copia o código da aplicação
COPY . .

# Cria um usuário não-root
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

# Expõe a porta 8000
EXPOSE 8000

# Comando padrão para a API (pode ser sobrescrito no docker-compose)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
