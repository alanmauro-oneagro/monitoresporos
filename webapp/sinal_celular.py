"""Sinal de celular numa coordenada pelos mapas oficiais de cobertura da
Anatel (Mosaico SMP, pedido do Alan, 31/10/2026).

A Anatel publica, por tecnologia (2G/3G/4G/5G) e operadora, dois mapas
de cobertura prevista: sinal forte (-82 dBm no 2G/3G, -90 dBm no 4G/5G) e
sinal minimo (-95 / -102 / -110 dBm). Cada mapa e' um shapefile com um
poligono do Brasil inteiro (centenas de milhares de partes). O servidor
baixa os ~70 arquivos uma vez por mes (`atualizar_se_necessario`, no
agendador) e transforma cada mapa numa grade de bits (`RESOLUCAO` graus,
~280 m) em blocos de 1 grau, guardados em `cobertura_anatel.db` ao lado do
banco (fora do banco principal: nao entra no backup). A consulta de uma
coordenada (`estimar`) le um bit por mapa.
"""
import os
import re
import sqlite3
import threading
import urllib.request
import zipfile
import zlib
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import models

PAGINA = "https://sistemas.anatel.gov.br/se/public/cmap.php"
SITE = "https://sistemas.anatel.gov.br"
_LINK = re.compile(r"/se/public/file/b/smp/pred_[A-Za-z0-9_]+_dbm-\d+\.zip")
_NOME = re.compile(r"pred_(\dG)_(?:SA_NSA_)?([A-Za-z0-9_]+?)_dbm-(\d+)\.zip$")
_CABECALHOS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/124.0 Safari/537.36"}
COBERTURA_PATH = Path(models.DB_PATH).parent / "cobertura_anatel.db"
INTERVALO_ATUALIZACAO = timedelta(days=30)
RETENTATIVA_APOS_FALHA = timedelta(hours=6)

# Grade: celulas de RESOLUCAO grau, blocos de 1 grau (BLOCO x BLOCO celulas).
RESOLUCAO = 0.0025
BLOCO = 400
LON_MIN, LON_MAX, LAT_MIN, LAT_MAX = -75, -28, -35, 6

_MARCAS = {"VIVO": "Vivo", "CLARO": "Claro", "TIM": "TIM", "ALGAR": "Algar", "BRISANET": "Brisanet",
           "SERCOMTEL": "Sercomtel", "UNIFIQUE": "Unifique", "IEZ": "IEZ!", "LIGUE": "Ligue", "SUMICITY": "Sumicity",
           "OI": "Oi"}
NIVEIS = ["bom", "fraco"]  # por mapa: limiar forte / minimo da Anatel
# Nivel da operadora no ponto (pedido do Alan, 31/10/2026 -- icone colorido):
# otimo = 4G/5G forte; bom = 4G/5G no minimo ou 2G/3G forte; fraco = so'
# 2G/3G no minimo; sem = sem cobertura (so' pras PRINCIPAIS, pra comparar).
NIVEIS_OPERADORA = ["otimo", "bom", "fraco", "sem"]
PRINCIPAIS = ["Vivo", "Claro", "TIM"]
_TEC_ORDEM = ["5G", "4G", "3G", "2G"]

_lock = threading.Lock()
_rodando = threading.Event()
_progresso = {}


def _agora():
    return datetime.strptime(models._agora_cuiaba()[:19], "%Y-%m-%d %H:%M:%S")


def _camada(nome_arquivo):
    """'pred_4G_VIVO_dbm-90.zip' -> ("4G", "Vivo", 90) ou None."""
    m = _NOME.search(nome_arquivo)
    if not m:
        return None
    tec, operadora, dbm = m.groups()
    return tec, _MARCAS.get(operadora.upper(), operadora.replace("_", " ").title()), int(dbm)


# ---------------------------------------------------------------- leitura

def _aneis(conteudo_shp):
    """Pontos (N x 2, lon/lat) e inicio de cada anel de todos os poligonos
    de um .shp (tipos 5/15/25 -- os primeiros campos sao iguais)."""
    b = memoryview(conteudo_shp)
    pos, pontos, inicios, total = 100, [], [], 0
    while pos + 8 <= len(b):
        tamanho = int.from_bytes(b[pos + 4:pos + 8], "big") * 2
        reg = pos + 8
        tipo = int.from_bytes(b[reg:reg + 4], "little")
        if tipo in (5, 15, 25):
            npartes, npts = np.frombuffer(b[reg + 36:reg + 44], "<i4")
            partes = np.frombuffer(b[reg + 44:reg + 44 + 4 * npartes], "<i4")
            ini = reg + 44 + 4 * npartes
            pontos.append(np.frombuffer(b[ini:ini + 16 * npts], "<f8").reshape(-1, 2))
            inicios.append(partes.astype(np.int64) + total)
            total += int(npts)
        pos = reg + tamanho
    if not pontos:
        return np.zeros((0, 2)), np.zeros(0, np.int64)
    if len(pontos) == 1:  # o normal: um registro so' (sem copiar)
        return pontos[0], inicios[0]
    return np.concatenate(pontos), np.concatenate(inicios)


def _blocos(pontos, inicios):
    """Varredura por linha da grade (regra par-impar), uma faixa de 1 grau
    por vez (pouca memoria): gera (bx, by, cheio, dados) de cada bloco de
    1 grau com cobertura -- `dados` = bits das celulas, compactados."""
    ncol = round((LON_MAX - LON_MIN) / RESOLUCAO)
    nlin = round((LAT_MAX - LAT_MIN) / RESOLUCAO)
    if len(pontos) < 2:
        return
    p1, p2 = pontos[:-1], pontos[1:]
    # linha r tem centro em LAT_MIN + (r + 0.5) * RESOLUCAO; a aresta cruza
    # as linhas r0 <= r < r1
    ya = (p1[:, 1] - LAT_MIN) / RESOLUCAO - 0.5
    yb = (p2[:, 1] - LAT_MIN) / RESOLUCAO - 0.5
    r0 = np.ceil(np.minimum(ya, yb)).astype(np.int32)
    r1 = np.ceil(np.maximum(ya, yb)).astype(np.int32)
    del ya, yb
    r1[inicios[1:] - 1] = r0[inicios[1:] - 1]  # aresta entre o fim de um anel e o inicio do proximo
    for by in range(nlin // BLOCO):
        lo, hi = by * BLOCO, (by + 1) * BLOCO
        idx = np.nonzero((r1 > r0) & (r1 > lo) & (r0 < hi))[0]
        if not len(idx):
            continue
        a0, a1 = np.maximum(r0[idx], lo), np.minimum(r1[idx], hi)
        n = (a1 - a0).astype(np.int64)
        aresta = np.repeat(idx, n)
        linhas = np.repeat(a0, n) + (np.arange(len(aresta)) - np.repeat(np.cumsum(n) - n, n))
        yc = LAT_MIN + (linhas + 0.5) * RESOLUCAO
        x1, y1, x2, y2 = p1[aresta, 0], p1[aresta, 1], p2[aresta, 0], p2[aresta, 1]
        xs = x1 + (yc - y1) * (x2 - x1) / (y2 - y1)
        ordem = np.lexsort((xs, linhas))
        linhas, xs = linhas[ordem], xs[ordem]
        # aneis fechados: cada linha tem numero par de cruzamentos -> pares seguidos
        r, xa, xb = linhas[0::2] - lo, xs[0::2], xs[1::2]
        c0 = np.clip(np.ceil((xa - LON_MIN) / RESOLUCAO - 0.5), 0, ncol).astype(np.int64)
        c1 = np.clip(np.ceil((xb - LON_MIN) / RESOLUCAO - 0.5), 0, ncol).astype(np.int64)
        dif = np.zeros((BLOCO, ncol + 1), np.int32)
        np.add.at(dif, (r, c0), 1)
        np.add.at(dif, (r, c1), -1)
        grade = np.cumsum(dif, axis=1)[:, :ncol] > 0
        del dif
        for bx in range(ncol // BLOCO):
            bloco = grade[:, bx * BLOCO:(bx + 1) * BLOCO]
            soma = int(bloco.sum())
            if soma == 0:
                continue
            if soma == BLOCO * BLOCO:
                yield bx, by, 1, None
            else:
                yield bx, by, 0, zlib.compress(np.packbits(bloco).tobytes(), 9)


def _criar_tabelas(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS camadas (id INTEGER PRIMARY KEY, arquivo TEXT, tecnologia TEXT,
                                            operadora TEXT, dbm INTEGER);
        CREATE TABLE IF NOT EXISTS blocos (bx INTEGER, by INTEGER, camada INTEGER, cheio INTEGER, dados BLOB,
                                           PRIMARY KEY (bx, by, camada)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS info (chave TEXT PRIMARY KEY, valor TEXT);
    """)


def importar_zip(conn, caminho_zip, nome_arquivo):
    """Le um zip da Anatel e grava a camada na base em montagem."""
    camada = _camada(nome_arquivo)
    if not camada:
        return False
    with zipfile.ZipFile(caminho_zip) as z:
        shp = next((n for n in z.namelist() if n.lower().endswith(".shp")), None)
        if not shp:
            raise ValueError(f"{nome_arquivo}: sem .shp")
        conteudo = z.read(shp)
    pontos, inicios = _aneis(conteudo)
    del conteudo
    cur = conn.execute("INSERT INTO camadas (arquivo, tecnologia, operadora, dbm) VALUES (?, ?, ?, ?)",
                       (nome_arquivo, *camada))
    camada_id = cur.lastrowid
    conn.executemany("INSERT INTO blocos (bx, by, camada, cheio, dados) VALUES (?, ?, ?, ?, ?)",
                     ((bx, by, camada_id, cheio, dados) for bx, by, cheio, dados in _blocos(pontos, inicios)))
    conn.commit()
    return True


# ---------------------------------------------------------------- download

def _abrir(url, timeout):
    return urllib.request.urlopen(urllib.request.Request(url, headers=_CABECALHOS), timeout=timeout)


def _links():
    with _abrir(PAGINA, 60) as resp:
        html = resp.read().decode("utf-8", "replace")
    return sorted(set(_LINK.findall(html)))


def _ler_info(caminho=None):
    caminho = caminho or COBERTURA_PATH
    if not Path(caminho).exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
        try:
            return dict(conn.execute("SELECT chave, valor FROM info").fetchall())
        finally:
            conn.close()
    except sqlite3.Error:
        return {}


def info():
    """Situacao da base pra tela: baixado_em, camadas, erro, rodando."""
    dados = _ler_info()
    dados["rodando"] = _rodando.is_set()
    dados["progresso"] = dict(_progresso)
    return dados


def _gravar_info(**valores):
    conn = sqlite3.connect(COBERTURA_PATH)
    try:
        _criar_tabelas(conn)
        conn.executemany("INSERT OR REPLACE INTO info (chave, valor) VALUES (?, ?)",
                         [(k, str(v)) for k, v in valores.items()])
        conn.commit()
    finally:
        conn.close()


def _baixar_tudo():
    links = _links()
    if not links:
        raise RuntimeError("nenhum mapa de cobertura encontrado na pagina da Anatel")
    tmp_db = COBERTURA_PATH.with_suffix(".montando")
    tmp_zip = COBERTURA_PATH.with_suffix(".zip.download")
    tmp_db.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp_db)
    try:
        _criar_tabelas(conn)
        for i, link in enumerate(links, 1):
            nome = link.rsplit("/", 1)[-1]
            _progresso.update(etapa=f"baixando {nome}", feitos=i - 1, total=len(links))
            for tentativa in range(3):
                try:
                    with _abrir(SITE + link, 600) as resp, open(tmp_zip, "wb") as f:
                        while True:
                            bloco = resp.read(1 << 20)
                            if not bloco:
                                break
                            f.write(bloco)
                    break
                except OSError:
                    if tentativa == 2:
                        raise
            importar_zip(conn, tmp_zip, nome)
        conn.executemany("INSERT OR REPLACE INTO info (chave, valor) VALUES (?, ?)",
                         [("baixado_em", _agora().isoformat()), ("camadas", str(len(links)))])
        conn.commit()
    finally:
        conn.close()
        tmp_zip.unlink(missing_ok=True)
    tmp_db.replace(COBERTURA_PATH)
    _cache.clear()


def atualizar(forcar=False):
    with _lock:
        _rodando.set()
        try:
            agora = _agora()
            if not forcar and not _vencida():
                return
            try:
                _baixar_tudo()
            except Exception as exc:
                COBERTURA_PATH.with_suffix(".montando").unlink(missing_ok=True)
                _gravar_info(tentativa_em=agora.isoformat(), erro=str(exc)[:300])
        finally:
            _rodando.clear()
            _progresso.clear()


def _vencida():
    meta, agora = _ler_info(), _agora()
    if meta.get("baixado_em") and agora - datetime.fromisoformat(meta["baixado_em"]) < INTERVALO_ATUALIZACAO:
        return False
    if meta.get("tentativa_em") and agora - datetime.fromisoformat(meta["tentativa_em"]) < RETENTATIVA_APOS_FALHA:
        return False
    return True


def atualizar_se_necessario():
    """Chamado pelo agendador a cada minuto: baixa em segundo plano (leva
    alguns minutos) so' se a base tiver mais de 30 dias."""
    if not _rodando.is_set() and _vencida():
        atualizar_em_segundo_plano()


def atualizar_em_segundo_plano(forcar=False):
    if _rodando.is_set():
        return
    _rodando.set()
    threading.Thread(target=atualizar, kwargs={"forcar": forcar}, daemon=True).start()


# ---------------------------------------------------------------- consulta

_cache = {}


def base_carregada():
    return COBERTURA_PATH.exists() and bool(_ler_info().get("baixado_em"))


def _nivel_operadora(tecs):
    dados = [t["nivel"] for t in tecs if t["tecnologia"] in ("4G", "5G")]
    voz = [t["nivel"] for t in tecs if t["tecnologia"] in ("2G", "3G")]
    if "bom" in dados:
        return "otimo"
    if dados or "bom" in voz:
        return "bom"
    return "fraco"


def estimar(lat, lon):
    """Por operadora (melhor primeiro): [{"operadora", "nivel"
    (otimo/bom/fraco/sem), "tecnologias": [{"tecnologia", "nivel" (bom/fraco)}]}]
    -- quem tem cobertura no ponto, mais Vivo/Claro/TIM como "sem" se nao
    tiverem. None se os mapas da Anatel ainda nao foram baixados."""
    if not base_carregada():
        return None
    col = int((lon - LON_MIN) // RESOLUCAO)
    lin = int((lat - LAT_MIN) // RESOLUCAO)
    chave = (os.path.getmtime(COBERTURA_PATH), lin, col)
    if chave in _cache:
        return _cache[chave]
    bx, by = col // BLOCO, lin // BLOCO
    indice = (lin - by * BLOCO) * BLOCO + (col - bx * BLOCO)
    conn = sqlite3.connect(f"file:{COBERTURA_PATH}?mode=ro", uri=True)
    try:
        dbm_por = {}
        for row in conn.execute("SELECT tecnologia, dbm FROM camadas"):
            dbm_por.setdefault(row[0], set()).add(row[1])
        linhas = conn.execute(
            "SELECT c.tecnologia, c.operadora, c.dbm, b.cheio, b.dados FROM blocos b JOIN camadas c ON c.id = b.camada "
            "WHERE b.bx = ? AND b.by = ?", (bx, by)).fetchall()
    finally:
        conn.close()
    melhor = {}  # (operadora, tecnologia) -> nivel
    for tec, operadora, dbm, cheio, dados in linhas:
        if not cheio:
            bits = np.frombuffer(zlib.decompress(dados), np.uint8)
            if not (bits[indice >> 3] >> (7 - (indice & 7))) & 1:
                continue
        nivel = "bom" if dbm == min(dbm_por[tec]) else "fraco"
        atual = melhor.get((operadora, tec))
        if atual is None or NIVEIS.index(nivel) < NIVEIS.index(atual):
            melhor[(operadora, tec)] = nivel
    por_operadora = {}
    for (operadora, tec), nivel in melhor.items():
        por_operadora.setdefault(operadora, []).append({"tecnologia": tec, "nivel": nivel})
    resultado = []
    for operadora, tecs in por_operadora.items():
        tecs.sort(key=lambda t: _TEC_ORDEM.index(t["tecnologia"]))
        resultado.append({"operadora": operadora, "nivel": _nivel_operadora(tecs), "tecnologias": tecs})
    for operadora in PRINCIPAIS:
        if operadora not in por_operadora:
            resultado.append({"operadora": operadora, "nivel": "sem", "tecnologias": []})
    resultado.sort(key=lambda o: (NIVEIS_OPERADORA.index(o["nivel"]), -sum(t["nivel"] == "bom" for t in o["tecnologias"]),
                                  -len(o["tecnologias"]), o["operadora"]))
    if len(_cache) > 5000:
        _cache.clear()
    _cache[chave] = resultado
    return resultado
