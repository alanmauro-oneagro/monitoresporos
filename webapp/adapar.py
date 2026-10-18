"""Segunda conferencia de registro do botao "Registros" da Biblioteca de
fungicidas: bulas dos fungicidas cadastrados na ADAPAR (adapar.pr.gov.br).
So' entra quando o Agrofit nao achou nenhuma cultura registrada pro
produto (pedido do Alan, 18/10/2026). A lista de bulas e' lida uma vez por
mes; o texto de cada bula baixada fica guardado (`CACHE_PATH`)."""
import html
import io
import json
import re
import threading
import time
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import agrofit_dados
import models

LISTA_URL = "https://www.adapar.pr.gov.br/Pagina/Agrotoxicos-Fungicidas"
CACHE_PATH = Path(models.DB_PATH).parent / "adapar_bulas.json"
INTERVALO_LISTA = timedelta(days=30)
MAX_BULA_BYTES = 25 << 20
# Bulas lidas por produto (as de mesmo ingrediente primeiro).
MAX_BULAS_POR_PRODUTO = 6
# Distancia maxima (caracteres) entre a cultura e o fungo na tabela de
# indicacoes da bula.
JANELA_CULTURA = 1500

_lock = threading.Lock()


class Indisponivel(Exception):
    pass


def _slug(texto):
    return re.sub(r"[^a-z0-9]", "", agrofit_dados.normalizar(texto))


def _ler_cache():
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"lista_em": None, "bulas": {}, "textos": {}}


def _gravar_cache(cache):
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def _baixar(url, timeout, limite=None):
    with agrofit_dados._abrir(url, timeout) as resp:
        dados = resp.read(limite + 1 if limite else -1)
    return b"" if limite and len(dados) > limite else dados


def montar_lista(pagina_html, base_url=LISTA_URL):
    """{slug: url do PDF} -- pelo texto do link e pelo nome do arquivo
    ("fox_supra.pdf" -> "foxsupra")."""
    bulas = {}
    for href, texto in re.findall(r'<a\b[^>]*href="([^"]+\.pdf)"[^>]*>(.*?)</a>', pagina_html, re.I | re.S):
        url = urljoin(base_url, html.unescape(href))
        nome = re.sub(r"<[^>]+>", " ", html.unescape(texto))
        for chave in (_slug(nome), _slug(Path(href).stem)):
            if chave:
                bulas.setdefault(chave, url)
    return bulas


def _lista(cache, prazo):
    agora = agrofit_dados._agora()
    if cache.get("lista_em") and agora - datetime.fromisoformat(cache["lista_em"]) < INTERVALO_LISTA:
        return cache["bulas"]
    try:
        pagina = _baixar(LISTA_URL, max(5, prazo - time.monotonic())).decode("utf-8", "replace")
    except Exception as exc:
        if cache.get("bulas"):
            return cache["bulas"]
        raise Indisponivel(f"site da ADAPAR fora do ar ({exc})") from exc
    bulas = montar_lista(pagina)
    if not bulas:
        if cache.get("bulas"):
            return cache["bulas"]
        raise Indisponivel("nenhuma bula encontrada na pagina de fungicidas da ADAPAR")
    cache.update(lista_em=agora.isoformat(), bulas=bulas)
    return bulas


def url_da_marca(marca, bulas):
    chave = _slug(marca)
    if not chave:
        return None
    if chave in bulas:
        return bulas[chave]
    # "absolutosc0420.pdf" = "Absoluto SC" (nome do arquivo com a data).
    for outra, url in bulas.items():
        if outra.startswith(chave) and outra[len(chave):].isdigit():
            return url
    return None


def texto_do_pdf(conteudo):
    from pypdf import PdfReader
    leitor = PdfReader(io.BytesIO(conteudo))
    return agrofit_dados.normalizar(" ".join(p.extract_text() or "" for p in leitor.pages))


def culturas_na_bula(texto, alvos, culturas):
    """Culturas (chaves de `culturas`: {cultura: {nomes normalizados}})
    indicadas na bula pra algum dos fungos de `alvos` -- a cultura mais
    proxima ANTES do nome do fungo na tabela de indicacoes."""
    mencoes = []
    for cultura, nomes in culturas.items():
        for nome in nomes:
            mencoes += [(m.start(), cultura) for m in re.finditer(r"\b" + re.escape(nome) + r"\b", texto)]
    mencoes.sort()
    achadas = set()
    for alvo in alvos:
        for m in re.finditer(r"\b" + re.escape(alvo) + r"\b", texto):
            antes = [c for pos, c in mencoes if m.start() - JANELA_CULTURA <= pos < m.start()]
            if antes:
                achadas.add(antes[-1])
    return achadas


def culturas_registradas(marcas_por_chave, alvos, culturas, prazo):
    """{chave_quimico: culturas indicadas nas bulas da ADAPAR} pras marcas
    de cada chave. `prazo` (time.monotonic) limita o tempo gasto
    baixando bulas; estourou ou o site nao responde -> `Indisponivel`."""
    with _lock:
        cache = _ler_cache()
        try:
            bulas = _lista(cache, prazo)
            resultado = {}
            for chave, marcas in marcas_por_chave.items():
                urls = [u for u in (url_da_marca(m, bulas) for m in sorted(marcas)) if u]
                for url in list(dict.fromkeys(urls))[:MAX_BULAS_POR_PRODUTO]:
                    if url not in cache["textos"]:
                        restante = prazo - time.monotonic()
                        if restante <= 1:
                            raise Indisponivel("tempo esgotado lendo as bulas -- clique de novo para continuar")
                        try:
                            conteudo = _baixar(url, min(25, restante), MAX_BULA_BYTES)
                        except urllib.error.HTTPError as exc:
                            if exc.code != 404:
                                raise Indisponivel(f"bula nao baixou ({exc})") from exc
                            conteudo = b""
                        except Indisponivel:
                            raise
                        except Exception as exc:
                            raise Indisponivel(f"bula nao baixou ({exc})") from exc
                        try:
                            cache["textos"][url] = texto_do_pdf(conteudo) if conteudo else ""
                        except Exception:
                            cache["textos"][url] = ""
                    resultado.setdefault(chave, set()).update(culturas_na_bula(cache["textos"][url], alvos, culturas))
            return resultado
        finally:
            _gravar_cache(cache)
