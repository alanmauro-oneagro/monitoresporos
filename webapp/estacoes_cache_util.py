"""Cache compartilhado por todo provedor de estacoes (inmet_stations,
dmc_stations, smn_stations, senamhi_stations, senamhi_bolivia_stations,
inumet_stations, dmh_paraguay_stations) -- padrao repetido em cada um
deles antes, agora centralizado aqui depois de um bug real ter sido
descoberto medindo a performance do site (aba Mapa levando 40+ segundos
pra carregar, MESMO na segunda visita).

O bug: cada provedor cacheava o catalogo por 24h, mas SO' quando a busca
tinha sucesso -- se o pais estivesse fora do ar (ex. o Paraguai, ja visto
acontecer de verdade), `_cache["estacoes"]` continuava vazio pra sempre,
e a condicao `if _cache["estacoes"] and ...` nunca virava verdadeira,
entao TODA chamada de `get_estacoes()` tentava a rede de novo -- ou seja,
enquanto um pais estivesse fora do ar, TODA visita a /mapa ou
/mapa-interpolado esperava o timeout inteiro daquele pais, pra sempre,
sem excecao. `cached_fetch` corrige isso guardando tambem quando foi a
ULTIMA TENTATIVA (com sucesso ou nao) e so' tentando de novo depois de
`FALHA_BACKOFF_SECONDS` -- assim, um pais fora do ar so' "custa" um
timeout a cada alguns minutos, nao em toda requisicao."""
import time

CACHE_TTL_SECONDS = 24 * 60 * 60  # catalogo de estacoes quase nunca muda -- sucesso fica valido por isso
FALHA_BACKOFF_SECONDS = 5 * 60  # sem sucesso, nao tenta de novo antes disso


def cached_fetch(cache, fetch_fn):
    """`cache` e' o dict do MODULO CHAMADOR (cada provedor tem o seu
    proprio, formato `{"timestamp": 0, "estacoes": [], "ultima_tentativa": 0}`
    -- nunca compartilhado entre paises, cada um tem seu proprio backoff).
    `fetch_fn()` busca e devolve a lista de estacoes de verdade (pode
    lancar excecao -- tratada aqui, vira "sem sucesso" igual devolver
    lista vazia)."""
    now = time.time()
    if cache["estacoes"] and now - cache["timestamp"] < CACHE_TTL_SECONDS:
        return cache["estacoes"]
    if not cache["estacoes"] and now - cache.get("ultima_tentativa", 0) < FALHA_BACKOFF_SECONDS:
        # Falhou (ou nunca teve sucesso) ha pouco tempo -- nao martela a
        # rede de novo ainda, so' devolve o que ja tinha (provavelmente []).
        return cache["estacoes"]
    cache["ultima_tentativa"] = now
    try:
        estacoes = fetch_fn()
    except Exception:
        return cache["estacoes"]
    if not estacoes:
        return cache["estacoes"]
    cache["estacoes"] = estacoes
    cache["timestamp"] = now
    return estacoes
