"""
Módulo de autenticação da UX Auditor API.
Implementa Resource Server OAuth2 com validação de JWT para tokens emitidos pelo janus-idp.
Suporta algoritmo RS256 (assimétrico) com JWKS dinâmico.
"""
from typing import Optional, Dict, Any
from datetime import datetime
import time
import jwt
import json
import requests
from jwt.algorithms import RSAAlgorithm
from fastapi import HTTPException, Request, status
from config import settings


class TokenData:
    """
    Modelo de dados para informações extraídas do token.
    """
    def __init__(self, user_id: str, exp: Optional[int] = None, iss: Optional[str] = None):
        # user_id mapeia para o claim 'sub' do JWT, representando a identidade única do usuário
        self.user_id = user_id
        # exp armazena o timestamp UNIX de expiração do token
        self.exp = exp
        # iss armazena o emissor (Issuer) que gerou o token (ex: URL do Janus IDP)
        self.iss = iss


# Cache global para evitar chamadas repetitivas ao endpoint de chaves públicas (JWKS) do IDP
# Isso melhora significativamente a performance e reduz a carga no servidor de identidade
_jwks_cache: Optional[Dict[str, Any]] = None
_jwks_cache_time: Optional[float] = None
# Tempo de vida do cache das chaves públicas (5 minutos)
_JWKS_CACHE_TTL = 300  # 5 minutos


def _extract_bearer_token(request: Request) -> str:
    """
    Extrai o token Bearer do cabeçalho Authorization.
    """
    # Recupera o header 'Authorization' da requisição HTTP recebida pelo FastAPI
    auth = request.headers.get("Authorization")
    if not auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cabeçalho Authorization ausente",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # O formato padrão esperado para OAuth2 é "Bearer <string_do_token>"
    parts = auth.split(" ", 1)
    # Valida se o cabeçalho possui exatamente duas partes e se o esquema é 'Bearer'
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cabeçalho Authorization inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return parts[1].strip()


def _get_cached_jwks() -> Dict[str, Any]:
    """
    Busca o JWKS do endpoint configurado, com cache por TTL.
    """
    global _jwks_cache, _jwks_cache_time

    # O serviço depende da URL do JWKS para buscar as chaves públicas de validação
    if not settings.AUTH_JWKS_URL:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AUTH_JWKS_URL não configurado",
        )

    # Implementação de cache simples baseada no tempo do sistema (monotonic clock)
    current_time = time.monotonic()
    if _jwks_cache and _jwks_cache_time and (current_time - _jwks_cache_time) < _JWKS_CACHE_TTL:
        return _jwks_cache

    try:
        # Realiza a busca síncrona das chaves públicas no IDP
        print(f"🔑 Buscando JWKS em: {settings.AUTH_JWKS_URL}")
        response = requests.get(settings.AUTH_JWKS_URL, timeout=5)
        response.raise_for_status()
        jwks = response.json()
    except requests.RequestException as e:
        # Se o IDP estiver fora do ar, o serviço não consegue validar tokens novos
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Falha ao obter JWKS: {str(e)}",
        )

    # Valida se a resposta segue o esquema JSON Web Key Set (deve conter uma lista de 'keys')
    if not isinstance(jwks, dict) or "keys" not in jwks:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Resposta JWKS inválida",
        )

    # Atualiza o cache global com as novas chaves e o timestamp atual
    _jwks_cache = jwks
    _jwks_cache_time = current_time
    return jwks


def get_jwks_public_key(token: str) -> Any:
    """
    Obtém a chave pública do JWKS endpoint para validar o token RS256.
    
    Args:
        token: Token JWT para extrair o kid (key ID)
        
    Returns:
        Chave pública RSA montada a partir do JWK
        
    Raises:
        HTTPException: Se falhar ao obter ou processar JWKS
    """
    try:
        # Extrai o cabeçalho (header) do JWT sem validar a assinatura.
        # Precisamos ler o 'kid' (Key ID) para saber qual chave do JWKS usar.
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        alg = header.get("alg")
        
        # O Janus IDP deve fornecer o 'kid' para permitir a rotação de chaves
        if not kid:
            print("❌ ERRO JWT: Token não contém 'kid' no header")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token não contém 'kid' no header",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Garante que o token está usando o algoritmo assimétrico RS256 configurado
        if alg and alg != settings.JWT_ALGORITHM:
            print(f"❌ ERRO JWT: Algoritmo do token inválido: {alg}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Algoritmo do token inválido: {alg}",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ ERRO JWT ao ler header: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Falha ao ler header do token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Busca o conjunto de chaves públicas disponíveis no IDP
    jwks = _get_cached_jwks()

    # Procura no conjunto de chaves aquela que possui o ID (kid) presente no token
    keys = jwks.get("keys", [])
    jwk_key = None
    for key in keys:
        if key.get("kid") == kid:
            jwk_key = key
            break

    # Se a chave não existir no JWKS, o token pode ser de outro IDP ou estar corrompido
    if not jwk_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Chave pública não encontrada para kid: {kid}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        # Converte a representação JSON da chave (JWK) em um objeto de chave RSA pública utilizável
        return RSAAlgorithm.from_jwk(json.dumps(jwk_key))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Falha ao converter JWK em chave pública: {str(e)}",
        )


def decode_jwt_token(token: str) -> Dict[str, Any]:
    """
    Decodifica e valida o token JWT usando RS256.
    
    Args:
        token: String do token JWT
        
    Returns:
        Payload do token decodificado como dicionário
        
    Raises:
        HTTPException: Se o token for inválido, expirado ou malformado
    """
    try:
        # Segurança: Este serviço só opera com criptografia de chave pública RS256
        if settings.JWT_ALGORITHM != "RS256":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"JWT_ALGORITHM inválido para este serviço: {settings.JWT_ALGORITHM}",
            )

        # Verifica configurações críticas do emissor e audiência para evitar tokens de outros contextos
        if not settings.AUTH_ISSUER_URL:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="AUTH_ISSUER_URL não configurado",
            )

        if not settings.AUTH_AUDIENCE:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="AUTH_AUDIENCE não configurado",
            )

        # Obtém a chave pública correspondente ao token
        public_key = get_jwks_public_key(token)
        
        # O jwt.decode realiza a validação criptográfica da assinatura e dos claims padrão
        payload = jwt.decode(
            token,
            key=public_key,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.AUTH_AUDIENCE,
            issuer=settings.AUTH_ISSUER_URL,
            options={
                "verify_signature": True, # Garante que o conteúdo não foi alterado
                "verify_exp": True,       # Garante que o tempo de validade do token não expirou
                "verify_iss": True,       # Valida se o emissor é o Janus IDP configurado
                "verify_aud": True,       # Valida se o token foi emitido especificamente para esta API
            }
        )

        return payload

    except HTTPException:
        raise

    # Tratamentos específicos para erros de JWT para fornecer feedback claro ao cliente (401)
    except jwt.ExpiredSignatureError:
        print("❌ ERRO JWT: Token expirado prematuramente (Problema de relógio/sync?)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidSignatureError:
        print("❌ ERRO JWT: Assinatura do token inválida (Chave pública não bate)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Assinatura do token inválida",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidIssuerError:
        print("❌ ERRO JWT: Emissor do token inválido")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Emissor do token inválido. Esperado: {settings.AUTH_ISSUER_URL}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        print(f"❌ ERRO JWT (Token ou Algoritmo Inválido): {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        print(f"❌ ERRO JWT Exceção Geral: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Falha na validação do token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def validate_token_payload(payload: Dict[str, Any]) -> TokenData:
    """
    Valida se o payload decodificado do token contém os claims necessários.
    
    Args:
        payload: Payload JWT decodificado
        
    Returns:
        Objeto TokenData com as informações extraídas
        
    Raises:
        HTTPException: Se os claims necessários estiverem faltando ou forem inválidos
    """
    # Extrai o subject (user_id) - claim obrigatório que identifica o proprietário do token
    sub: Optional[str] = payload.get("sub")
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Payload do token sem claim 'sub' (ID do usuário)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Extrai a expiração (exp) - claim obrigatório para segurança temporal
    exp: Optional[int] = payload.get("exp")
    if exp is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Payload do token sem claim 'exp' (expiração)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Verifica se o token não expirou (verificação dupla manual contra o clock do servidor)
    current_time = datetime.utcnow().timestamp()
    if exp < current_time:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # O issuer e o audience já foram validados estruturalmente em decode_jwt_token()
    iss: Optional[str] = payload.get("iss")

    return TokenData(user_id=sub, exp=exp, iss=iss)


async def get_current_user(request: Request) -> TokenData:
    """
    Dependência FastAPI para autenticar e extrair informações do usuário do token JWT.
    
    Esta dependência:
    1. Extrai o token Bearer do cabeçalho Authorization
    2. Valida a assinatura do token usando chave pública (RS256)
    3. Verifica a expiração do token (claim exp)
    4. Verifica se o emissor (claim iss) corresponde ao janus-idp
    5. Extrai o user_id do claim sub
    
    Uso:
        @app.get("/protected")
        async def endpoint_protected(current_user: TokenData = Depends(get_current_user)):
            return {"user_id": current_user.user_id}

    Args:
        request: Requisição HTTP contendo o cabeçalho Authorization
        
    Returns:
        Objeto TokenData contendo user_id e outras informações do token
        
    Raises:
        HTTPException: 401 Unauthorized se o token for inválido, ausente ou expirado
    """
    # 1. Recupera a string bruta do token do cabeçalho de autorização
    token = _extract_bearer_token(request)
    
    # 2. Decodifica e valida a integridade criptográfica e claims estruturais (iss, aud, exp)
    payload = decode_jwt_token(token)
    
    # 3. Valida se o payload contém os dados de identidade necessários para o negócio (sub)
    token_data = validate_token_payload(payload)
    
    return token_data


async def get_current_user_optional(request: Request) -> Optional[TokenData]:
    """
    Versão opcional do get_current_user que retorna None em vez de lançar 401.
    Útil para endpoints que funcionam com ou sem autenticação (degradação graciosa).
    
    Args:
        request: Requisição HTTP contendo o cabeçalho Authorization (opcional)
        
    Returns:
        Objeto TokenData se o token for válido, None caso contrário
    """
    # Verifica a existência do cabeçalho sem disparar erro imediato
    auth = request.headers.get("Authorization")
    if not auth:
        return None
    try:
        # Tenta o fluxo normal de validação. Se falhar em qualquer ponto (formato, assinatura, expiração),
        # retorna None em vez de interromper a requisição com 401.
        token = _extract_bearer_token(request)
        payload = decode_jwt_token(token)
        token_data = validate_token_payload(payload)
        return token_data
    except HTTPException:
        return None
