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
import json
import os
import threading
import time
from pathlib import Path

CACHE_TTL_SECONDS = 24 * 60 * 60  # catalogo de estacoes quase nunca muda -- sucesso fica valido por isso
FALHA_BACKOFF_SECONDS = 5 * 60  # sem sucesso, nao tenta de novo antes disso
# Copia em disco (mesmo volume dos CSVs) -- sobrevive a deploy/reinicio,
# senao a 1a visita depois de cada deploy esperava o download de novo.
_DIR = Path(os.environ.get("BIOSCOUT_DATA_DIR", str(Path(__file__).parent.parent / "data"))) / "catalogos_estacoes"


def _arquivo(fetch_fn):
    return _DIR / f"{fetch_fn.__module__}.{fetch_fn.__name__}.json".replace("<", "").replace(">", "")


def _carregar_do_disco(cache, fetch_fn):
    if cache.get("disco_lido"):
        return
    cache["disco_lido"] = True
    try:
        with open(_arquivo(fetch_fn), encoding="utf-8") as f:
            salvo = json.load(f)
        if salvo.get("estacoes") and not cache["estacoes"]:
            cache["estacoes"], cache["timestamp"] = salvo["estacoes"], salvo.get("timestamp", 0)
    except (OSError, ValueError):
        pass


def _buscar(cache, fetch_fn):
    try:
        estacoes = fetch_fn()
    except Exception:
        estacoes = None
    finally:
        cache["buscando"] = False
    if not estacoes:
        return
    cache["estacoes"], cache["timestamp"] = estacoes, time.time()
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        tmp = _arquivo(fetch_fn).with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"timestamp": cache["timestamp"], "estacoes": estacoes}, f)
        os.replace(tmp, _arquivo(fetch_fn))
    except OSError:
        pass


def cached_fetch(cache, fetch_fn):
    """`cache` e' o dict do MODULO CHAMADOR (cada provedor tem o seu
    proprio, formato `{"timestamp": 0, "estacoes": [], "ultima_tentativa": 0}`
    -- nunca compartilhado entre paises, cada um tem seu proprio backoff).
    `fetch_fn()` busca e devolve a lista de estacoes de verdade (pode
    lancar excecao -- tratada aqui, vira "sem sucesso" igual devolver
    lista vazia).

    NUNCA espera a rede (pedido do usuario, 16/10/2026 -- navegacao entre
    abas demorando quando o site de algum pais estava fora do ar ou logo
    depois de um deploy): devolve o que ja tem (memoria ou disco, mesmo
    vencido) e busca de novo em segundo plano."""
    _carregar_do_disco(cache, fetch_fn)
    now = time.time()
    if cache["estacoes"] and now - cache["timestamp"] < CACHE_TTL_SECONDS:
        return cache["estacoes"]
    if now - cache.get("ultima_tentativa", 0) < FALHA_BACKOFF_SECONDS or cache.get("buscando"):
        # Tentou ha pouco (ou esta tentando agora) -- nao martela a rede.
        return cache["estacoes"]
    cache["ultima_tentativa"] = now
    cache["buscando"] = True
    threading.Thread(target=_buscar, args=(cache, fetch_fn), daemon=True).start()
    return cache["estacoes"]
