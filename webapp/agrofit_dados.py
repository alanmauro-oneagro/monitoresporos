"""Base aberta do Agrofit/MAPA ("Produto Formulado", dados.agricultura.gov.br)
-- produto x ingrediente ativo x cultura x praga (nome cientifico). Baixada
pelo servidor uma vez por mes (`atualizar_se_necessario`, chamado pelo
scheduler) e guardada como um indice compacto em JSON ao lado do banco
(mesmo volume persistente). Usada pelo botao "Agrofit" de cada doenca da
Biblioteca de fungicidas: a busca e' pelo nome cientifico do fungo."""
import csv
import io
import json
import re
import threading
import unicodedata
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import models

RESOURCE_ID = "d30b30d7-e256-484e-9ab8-cd40974e1238"
RESOURCE_PAGE = (
    "https://dados.agricultura.gov.br/dataset/sistema-de-agrotoxicos-fitossanitarios-agrofit/resource/" + RESOURCE_ID
)
_RESOURCE_SHOW = "https://dados.agricultura.gov.br/api/3/action/resource_show?id=" + RESOURCE_ID
INTERVALO_ATUALIZACAO = timedelta(days=30)
# Falha no download (portal fora do ar etc) -- tenta de novo so' depois disso.
RETENTATIVA_APOS_FALHA = timedelta(hours=6)

INDICE_PATH = Path(models.DB_PATH).parent / "agrofit_indice.json"
_PALAVRAS_IGNORADAS = {"spp", "sp", "and", "e", "var", "f", "complex", "complexo"}

_lock = threading.Lock()
_baixando = threading.Event()
_cache = {"mtime": None, "dados": None}


def _agora():
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=4)


def normalizar(texto):
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()


def ativos(ingrediente):
    """"Piraclostrobina (estrobilurina) (133 g/L) + Epoxiconazol (triazol)"
    -> ("epoxiconazol", "piraclostrobina") -- sem parenteses, sem acento,
    em ordem alfabetica, pra comparar com o texto da Biblioteca."""
    sem_parenteses = re.sub(r"\([^)]*\)", " ", ingrediente or "")
    partes = [normalizar(p).strip(" .;,") for p in re.split(r"\+|;", sem_parenteses)]
    return tuple(sorted({p for p in partes if p}))


def _coluna(cabecalho, *palavras):
    for i, nome in enumerate(cabecalho):
        n = normalizar(nome).replace("_", " ")
        if all(p in n for p in palavras):
            return i
    return None


def montar_indice(arquivo):
    """Arquivo binario do CSV (ou .zip com o CSV), lido em streaming ->
    {"pragas": {praga: [[ativos, cultura], ...]}, "nomes": {ativos: texto
    exibido}, "culturas": [...]}."""
    inicio = arquivo.read(4)
    arquivo.seek(0)
    if inicio[:2] == b"PK":
        z = zipfile.ZipFile(arquivo)
        arquivo = z.open(next(n for n in z.namelist() if n.lower().endswith(".csv")))
    amostra = arquivo.peek(1 << 20)[: 1 << 20] if hasattr(arquivo, "peek") else b""
    try:
        amostra.decode("utf-8")
        encoding = "utf-8-sig"
    except UnicodeDecodeError as exc:
        encoding = "utf-8-sig" if exc.start > len(amostra) - 4 else "latin-1"
    texto = io.TextIOWrapper(arquivo, encoding=encoding, errors="replace", newline="")
    primeira = texto.readline()
    delimitador = ";" if primeira.count(";") >= primeira.count(",") else ","
    cabecalho = next(csv.reader([primeira], delimiter=delimitador))
    leitor = csv.reader(texto, delimiter=delimitador)
    col_ingrediente = _coluna(cabecalho, "ingrediente")
    col_cultura = _coluna(cabecalho, "cultura")
    col_praga = _coluna(cabecalho, "praga", "cientif")
    if None in (col_ingrediente, col_cultura, col_praga):
        raise ValueError(f"Colunas nao reconhecidas no CSV do Agrofit: {cabecalho}")
    pragas, nomes, culturas = {}, {}, set()
    for linha in leitor:
        if len(linha) <= max(col_ingrediente, col_cultura, col_praga):
            continue
        chave = ativos(linha[col_ingrediente])
        cultura = linha[col_cultura].strip()
        if not chave or not cultura:
            continue
        culturas.add(cultura)
        texto_ativos = " + ".join(chave)
        nomes.setdefault(texto_ativos, " + ".join(
            normalizar(re.sub(r"\([^)]*\)", " ", p)).strip(" .;,")
            for p in re.split(r"\+|;", linha[col_ingrediente]) if p.strip()
        ))
        for praga in re.split(r"[;,/]", linha[col_praga]):
            praga = normalizar(praga)
            if praga:
                pragas.setdefault(praga, set()).add((texto_ativos, cultura))
    return {
        "pragas": {p: sorted(map(list, v)) for p, v in pragas.items()},
        "nomes": nomes,
        "culturas": sorted(culturas),
    }


def _ler_meta():
    if not INDICE_PATH.exists():
        return {}
    try:
        return carregar().get("meta", {})
    except Exception:
        return {}


def carregar():
    """Indice salvo (cache em memoria enquanto o arquivo nao mudar)."""
    mtime = INDICE_PATH.stat().st_mtime
    if _cache["mtime"] != mtime:
        _cache["dados"] = json.loads(INDICE_PATH.read_text(encoding="utf-8"))
        _cache["mtime"] = mtime
    return _cache["dados"]


def status():
    """{"baixado_em": "dd/mm/aaaa" | None, "baixando": bool, "erro": str | None}."""
    meta = _ler_meta()
    baixado = meta.get("baixado_em")
    return {
        "baixado_em": datetime.fromisoformat(baixado).strftime("%d/%m/%Y") if baixado else None,
        "baixando": _baixando.is_set(),
        "erro": meta.get("erro"),
    }


def _baixar():
    with urllib.request.urlopen(_RESOURCE_SHOW, timeout=60) as resp:
        url = json.loads(resp.read().decode("utf-8"))["result"]["url"]
    tmp = INDICE_PATH.with_suffix(".download")
    try:
        with urllib.request.urlopen(url, timeout=600) as resp, open(tmp, "wb") as f:
            while True:
                bloco = resp.read(1 << 20)
                if not bloco:
                    break
                f.write(bloco)
        with open(tmp, "rb") as f:
            return montar_indice(io.BufferedReader(f))
    finally:
        tmp.unlink(missing_ok=True)


def atualizar(forcar=False):
    """Baixa e reindexa a base, se estiver vencida (ou `forcar`). Roda
    no proprio thread de quem chamou -- ver `atualizar_em_segundo_plano`."""
    with _lock:
        meta = _ler_meta()
        agora = _agora()
        if not forcar:
            if meta.get("baixado_em") and agora - datetime.fromisoformat(meta["baixado_em"]) < INTERVALO_ATUALIZACAO:
                return
            if meta.get("tentativa_em") and agora - datetime.fromisoformat(meta["tentativa_em"]) < RETENTATIVA_APOS_FALHA:
                return
        _baixando.set()
        try:
            dados = _baixar()
            dados["meta"] = {"baixado_em": agora.isoformat(), "fonte": RESOURCE_PAGE}
        except Exception as exc:
            if INDICE_PATH.exists():
                dados = carregar()
                dados["meta"] = {**dados.get("meta", {}), "tentativa_em": agora.isoformat(), "erro": str(exc)[:300]}
            else:
                dados = {"meta": {"tentativa_em": agora.isoformat(), "erro": str(exc)[:300]}}
        finally:
            _baixando.clear()
        tmp = INDICE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
        tmp.replace(INDICE_PATH)


def atualizar_em_segundo_plano(forcar=False):
    if _baixando.is_set():
        return
    threading.Thread(target=atualizar, kwargs={"forcar": forcar}, daemon=True).start()


def atualizar_se_necessario():
    """Chamado a cada tick do scheduler -- so' dispara o download (em
    outro thread) quando a base tem mais de 30 dias ou nunca foi baixada."""
    meta = _ler_meta()
    agora = _agora()
    if meta.get("baixado_em") and agora - datetime.fromisoformat(meta["baixado_em"]) < INTERVALO_ATUALIZACAO:
        return
    if meta.get("tentativa_em") and agora - datetime.fromisoformat(meta["tentativa_em"]) < RETENTATIVA_APOS_FALHA:
        return
    atualizar_em_segundo_plano()


def pragas_do_nome_cientifico(nome_cientifico, pragas):
    """Pragas do indice que batem com o nome cientifico da doenca:
    "genero especie" quando a especie existe no Agrofit, senao todas as
    especies do genero ("Phakopsora spp." -> todas as Phakopsora)."""
    palavras = [p for p in re.findall(r"[a-z]+", normalizar(nome_cientifico)) if p not in _PALAVRAS_IGNORADAS]
    achadas = set()
    for i, genero in enumerate(palavras):
        do_genero = [p for p in pragas if p.split(" ")[0] == genero]
        if not do_genero:
            continue
        if i + 1 < len(palavras):
            da_especie = [p for p in do_genero if p.startswith(f"{genero} {palavras[i + 1]}")]
            if da_especie:
                achadas.update(da_especie)
                continue
        achadas.update(do_genero)
    return sorted(achadas)


def registros(nome_cientifico):
    """(pragas encontradas, {ativos: set(culturas do Agrofit)}, indice)."""
    dados = carregar()
    pragas = pragas_do_nome_cientifico(nome_cientifico, dados.get("pragas", {}))
    por_ativos = {}
    for praga in pragas:
        for texto_ativos, cultura in dados["pragas"][praga]:
            por_ativos.setdefault(texto_ativos, set()).add(cultura)
    return pragas, por_ativos, dados


def cultura_do_agrofit(cultura, culturas_agrofit):
    """Nome da cultura do site -> nome(s) equivalentes no Agrofit
    ("Algodao" = "Algodão", "Cana" = "Cana-de-açúcar"); vazio se o
    Agrofit nao tem essa cultura (ai' o registro dela nao e' mexido)."""
    alvo = normalizar(cultura)
    return {c for c in culturas_agrofit if normalizar(c) == alvo or re.split(r"[\s-]", normalizar(c))[0] == alvo}
