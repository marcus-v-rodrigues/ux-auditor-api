"""
Módulo de autenticação da UX Auditor API.
Implementa Resource Server OAuth2 com validação de JWT para tokens emitidos pelo janus-idp.
Suporta algoritmo RS256 (assimétrico) com JWKS ou chave pública estática.
"""
from typing import Optional, Dict, Any
from datetime import datetime
import jwt
import json
import requests
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from config import settings


# Esquema OAuth2 que espera token Bearer no cabeçalho Authorization
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/token",  # Placeholder; emissão real de tokens é feita pelo janus-idp
    auto_error=True
)


class TokenData:
    """
    Modelo de dados para informações extraídas do token.
    """
    def __init__(self, user_id: str, exp: Optional[int] = None, iss: Optional[str] = None):
        self.user_id = user_id
        self.exp = exp
        self.iss = iss


# Cache para chaves públicas JWKS
_jwks_cache: Optional[Dict[str, Any]] = None
_jwks_cache_time: Optional[float] = None
_JWKS_CACHE_TTL = 300  # 5 minutos


def get_jwks_public_key(token: str) -> str:
    """
    Obtém a chave pública do JWKS endpoint para validar o token RS256.
    
    Args:
        token: Token JWT para extrair o kid (key ID)
        
    Returns:
        Chave pública PEM formatada
        
    Raises:
        HTTPException: Se falhar ao obter ou processar JWKS
    """
    global _jwks_cache, _jwks_cache_time
    
    # Extrai o kid (key ID) do token header
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get('kid')
        if not kid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token não contém 'kid' no header",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Falha ao ler header do token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Verifica cache
    current_time = datetime.utcnow().timestamp()
    if _jwks_cache and _jwks_cache_time and (current_time - _jwks_cache_time) < _JWKS_CACHE_TTL:
        jwks = _jwks_cache
    else:
        # Busca JWKS do endpoint
        try:
            response = requests.get(settings.JWT_JWKS_URL, timeout=5)
            response.raise_for_status()
            jwks = response.json()
            _jwks_cache = jwks
            _jwks_cache_time = current_time
        except requests.RequestException as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Falha ao obter JWKS: {str(e)}",
            )
    
    # Encontra a chave correspondente ao kid
    keys = jwks.get('keys', [])
    rsa_key = None
    for key in keys:
        if key.get('kid') == kid:
            rsa_key = {
                'kty': key['kty'],
                'kid': key['kid'],
                'use': key['use'],
                'n': key['n'],
                'e': key['e']
            }
            break
    
    if not rsa_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Chave pública não encontrada para kid: {kid}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return rsa_key


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
        # Determina a chave pública a ser usada
        if settings.JWT_JWKS_URL:
            # Usa JWKS endpoint para obter chave pública dinamicamente
            public_key = get_jwks_public_key(token)
            payload = jwt.decode(
                token,
                key=public_key,
                algorithms=[settings.JWT_ALGORITHM],
                issuer=settings.JWT_ISSUER,
                options={
                    'verify_signature': True,
                    'verify_exp': True,
                    'verify_iss': True
                }
            )
        elif settings.JWT_PUBLIC_KEY:
            # Usa chave pública estática
            payload = jwt.decode(
                token,
                key=settings.JWT_PUBLIC_KEY,
                algorithms=[settings.JWT_ALGORITHM],
                issuer=settings.JWT_ISSUER,
                options={
                    'verify_signature': True,
                    'verify_exp': True,
                    'verify_iss': True
                }
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="JWT_JWKS_URL ou JWT_PUBLIC_KEY deve ser configurado",
            )
        
        return payload
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Assinatura do token inválida",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidIssuerError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Emissor do token inválido. Esperado: {settings.JWT_ISSUER}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
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
    # Extrai o subject (user_id) - claim obrigatório
    sub: Optional[str] = payload.get("sub")
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Payload do token sem claim 'sub' (ID do usuário)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Extrai a expiração (exp) - claim obrigatório
    exp: Optional[int] = payload.get("exp")
    if exp is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Payload do token sem claim 'exp' (expiração)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Verifica se o token não expirou (verificação dupla)
    current_time = datetime.utcnow().timestamp()
    if exp < current_time:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Extrai o emissor (iss) - opcional mas deve corresponder ao emissor esperado
    iss: Optional[str] = payload.get("iss")
    if settings.JWT_ISSUER and iss != settings.JWT_ISSUER:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Emissor do token incorreto. Esperado: {settings.JWT_ISSUER}, Recebido: {iss}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return TokenData(user_id=sub, exp=exp, iss=iss)


async def get_current_user(token: str = Depends(oauth2_scheme)) -> TokenData:
    """
    Dependência FastAPI para autenticar e extrair informações do usuário do token JWT.
    
    Esta dependência:
    1. Extrai o token Bearer do cabeçalho Authorization
    2. Valida a assinatura do token usando chave pública (RS256)
    3. Verifica a expiração do token (claim exp)
    4. Verifica se o emissor (claim iss) corresponde ao janus-idp
    5. Extrai o user_id do claim sub
    
    Suporta validação via JWKS endpoint ou chave pública estática.
    
    Uso:
        @app.get("/protected")
        async def endpoint_protected(current_user: TokenData = Depends(get_current_user)):
            return {"user_id": current_user.user_id}
    
    Args:
        token: Token JWT extraído do cabeçalho Authorization pelo oauth2_scheme
        
    Returns:
        Objeto TokenData contendo user_id e outras informações do token
        
    Raises:
        HTTPException: 401 Unauthorized se o token for inválido, ausente ou expirado
    """
    # Decodifica e valida o token
    payload = decode_jwt_token(token)
    
    # Valida se o payload contém os claims necessários
    token_data = validate_token_payload(payload)
    
    return token_data


async def get_current_user_optional(token: Optional[str] = Depends(oauth2_scheme)) -> Optional[TokenData]:
    """
    Versão opcional do get_current_user que retorna None em vez de lançar 401.
    Útil para endpoints que funcionam com ou sem autenticação.
    
    Args:
        token: Token JWT extraído do cabeçalho Authorization (pode ser None)
        
    Returns:
        Objeto TokenData se o token for válido, None caso contrário
    """
    if token is None:
        return None
    
    try:
        payload = decode_jwt_token(token)
        token_data = validate_token_payload(payload)
        return token_data
    except HTTPException:
        return None
