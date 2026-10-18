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
# Link direto do arquivo (com o id do dataset) -- o mesmo que outros
# projetos baixam todo mes; o link pelo nome do dataset responde 403.
_DOWNLOAD_DIRETO = (
    "https://dados.agricultura.gov.br/dataset/6c913699-e82e-4da3-a0a1-fb6c431e367f/resource/"
    + RESOURCE_ID + "/download/agrofitprodutosformulados.csv"
)
# O portal recusa (403) o User-Agent padrao do Python.
_CABECALHOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept": "text/csv,application/json,application/zip,*/*",
}
INTERVALO_ATUALIZACAO = timedelta(days=30)
# Falha no download (portal fora do ar etc) -- tenta de novo so' depois disso.
RETENTATIVA_APOS_FALHA = timedelta(hours=6)

INDICE_PATH = Path(models.DB_PATH).parent / "agrofit_indice.json"
_PALAVRAS_IGNORADAS = {"spp", "sp", "and", "e", "var", "f", "complex", "complexo"}
# Ingrediente com um desses generos (microrganismo) conta como biologico.
# Biologico = TODOS os ingredientes sao microrganismos destes generos. A
# classe do Agrofit nao serve: produto de oleo de neem/azadiractina vem
# como "Fungicida microbiologico" (pedido do Alan: nao e' biologico).
_GENEROS_BIOLOGICOS = {
    "bacillus", "trichoderma", "pseudomonas", "streptomyces", "clonostachys", "saccharomyces",
    "aureobasidium", "gliocladium", "paenibacillus", "burkholderia", "coniothyrium", "ulocladium",
    "beauveria", "metarhizium", "isaria", "cordyceps", "purpureocillium", "pochonia", "azospirillum",
    "bradyrhizobium", "lysinibacillus", "brevibacillus", "chromobacterium", "pasteuria", "ampelomyces",
    "lecanicillium", "verticillium", "hirsutella", "akanthomyces", "talaromyces", "penicillium",
}
# Cerevisane (parede celular de Saccharomyces cerevisiae) tambem conta.
# Sem registro vigente no Brasil (banidos/suspensos) -- nunca aparecem,
# mesmo sem a base do Agrofit carregada. Com a base carregada, qualquer
# quimico que nao esteja nela tambem some (ver `produto_sem_registro`).
INGREDIENTES_BANIDOS = ("carbendazim",)

_lock = threading.Lock()
_baixando = threading.Event()
_cache = {"mtime": None, "dados": None}
# Progresso do download em andamento (mostrado na Biblioteca de fungicidas).
_progresso = {"etapa": None, "baixados": 0, "total": None, "linhas": 0}


def _agora():
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=4)


def normalizar(texto):
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()


def _sem_parenteses(texto):
    """Tira tudo entre parenteses, inclusive aninhados --
    "mancozebe (alquilenobis(ditiocarbamato)) (440 g/L)" -> "mancozebe"."""
    texto = texto or ""
    anterior = None
    while anterior != texto:
        anterior, texto = texto, re.sub(r"\([^()]*\)", " ", texto)
    return texto.replace("(", " ").replace(")", " ")


def ativos(ingrediente):
    """"Piraclostrobina (estrobilurina) (133 g/L) + Epoxiconazol (triazol)"
    -> ("epoxiconazol", "piraclostrobina") -- sem parenteses, sem acento,
    em ordem alfabetica, pra comparar com o texto da Biblioteca."""
    partes = [normalizar(p).strip(" .;,") for p in re.split(r"\+|;", _sem_parenteses(ingrediente))]
    return tuple(sorted({p for p in partes if p}))


def _canon_palavra(palavra):
    """Grafia do Agrofit x Biblioteca: "benzovindiflupyr" = "benzovindiflupir",
    "bixafem" = "bixafeno", "metominostrobin" = "metominostrobina",
    "oxatiapiprolim" = "oxatiapiprolina"."""
    palavra = palavra.replace("y", "i")
    palavra = re.sub(r"[aeo]$", "", palavra)
    return re.sub(r"m$", "n", palavra)


def chave_quimico(ingrediente):
    """Chave de comparacao de um quimico: ingredientes ativos sem
    parenteses, acento nem diferenca de grafia, em ordem alfabetica."""
    partes = set()
    for ativo in ativos(ingrediente):
        palavras = re.findall(r"[a-z0-9]+", ativo)
        if palavras:
            partes.add(" ".join(_canon_palavra(w) for w in palavras))
    return " + ".join(sorted(partes))


def chave_biologico(ingrediente):
    """Chave de um biologico: so' genero + especie de cada organismo, com
    abreviacao expandida -- "Bacillus subtilis + B. velezensis BV02" =
    "Bacillus velezensis, isolado X + Bacillus subtilis"."""
    partes, genero = set(), None
    for parte in re.split(r"\+|;", _sem_parenteses(ingrediente)):
        palavras = re.findall(r"[a-z]+\.?", normalizar(parte))
        if not palavras:
            continue
        if palavras[0].endswith(".") and genero and genero.startswith(palavras[0][:-1]):
            primeira = genero
        else:
            primeira = genero = palavras[0].rstrip(".")
        especie = palavras[1].rstrip(".") if len(palavras) > 1 else "spp"
        partes.add(f"{primeira} {especie}")
    return " + ".join(sorted(partes))


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
    # Algumas celulas (EMPRESA_PAIS_TIPO) passam do limite padrao do csv.
    csv.field_size_limit(50_000_000)
    leitor = csv.reader(texto, delimiter=delimitador)
    col_ingrediente = _coluna(cabecalho, "ingrediente")
    col_cultura = _coluna(cabecalho, "cultura")
    col_praga = _coluna(cabecalho, "praga", "cientif")
    col_situacao = _coluna(cabecalho, "situacao")
    col_marca = _coluna(cabecalho, "marca")
    if None in (col_ingrediente, col_cultura, col_praga):
        raise ValueError(f"Colunas nao reconhecidas no CSV do Agrofit: {cabecalho}")
    pragas, nomes, culturas, marcas = {}, {}, set(), {}
    for linha in leitor:
        _progresso["linhas"] += 1
        if len(linha) <= max(col_ingrediente, col_cultura, col_praga):
            continue
        if col_situacao is not None and col_situacao < len(linha) and normalizar(linha[col_situacao]) in (
            "false", "0", "nao", "cancelado", "suspenso", "inativo",
        ):
            continue
        chave = ativos(linha[col_ingrediente])
        cultura = linha[col_cultura].strip()
        if not chave or not cultura:
            continue
        culturas.add(cultura)
        texto_ativos = " + ".join(chave)
        if col_marca is not None and col_marca < len(linha):
            for marca in linha[col_marca].split(";"):
                if marca.strip():
                    marcas.setdefault(texto_ativos, set()).add(marca.strip())
        nomes.setdefault(texto_ativos, " + ".join(
            normalizar(p).strip(" .;,")
            for p in re.split(r"\+|;", _sem_parenteses(linha[col_ingrediente])) if p.strip()
        ))
        for praga in re.split(r"[;,/]", linha[col_praga]):
            praga = normalizar(praga)
            if praga:
                pragas.setdefault(praga, set()).add((texto_ativos, cultura))
    return {
        "pragas": {p: sorted(map(list, v)) for p, v in pragas.items()},
        "nomes": nomes,
        "culturas": sorted(culturas),
        "marcas": {k: sorted(v) for k, v in marcas.items()},
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
        "enviado": "arquivo enviado" in (meta.get("fonte") or ""),
        "progresso": dict(_progresso) if _baixando.is_set() else None,
    }


def _abrir(url, timeout):
    return urllib.request.urlopen(urllib.request.Request(url, headers=_CABECALHOS), timeout=timeout)


def _urls_do_arquivo():
    urls = [_DOWNLOAD_DIRETO]
    try:
        with _abrir(_RESOURCE_SHOW, 60) as resp:
            urls.append(json.loads(resp.read().decode("utf-8"))["result"]["url"])
    except Exception:
        pass
    return urls


def _baixar():
    _progresso.update(etapa="conectando", baixados=0, total=None, linhas=0)
    tmp = INDICE_PATH.with_suffix(".download")
    try:
        erros = []
        for url in _urls_do_arquivo():
            try:
                resp = _abrir(url, 600)
                break
            except Exception as exc:
                erros.append(f"{exc} ({url})")
        else:
            raise RuntimeError(" / ".join(erros))
        tamanho = resp.headers.get("Content-Length")
        _progresso.update(etapa="baixando", total=int(tamanho) if tamanho and tamanho.isdigit() else None)
        with resp, open(tmp, "wb") as f:
            while True:
                bloco = resp.read(1 << 20)
                if not bloco:
                    break
                f.write(bloco)
                _progresso["baixados"] += len(bloco)
        _progresso["etapa"] = "processando"
        with open(tmp, "rb") as f:
            return montar_indice(io.BufferedReader(f))
    finally:
        tmp.unlink(missing_ok=True)


def atualizar(forcar=False):
    """Baixa e reindexa a base, se estiver vencida (ou `forcar`). Roda
    no proprio thread de quem chamou -- ver `atualizar_em_segundo_plano`."""
    with _lock:
        _baixando.set()
        try:
            _atualizar(forcar)
        finally:
            _baixando.clear()


def _atualizar(forcar):
    meta = _ler_meta()
    agora = _agora()
    if not forcar:
        if meta.get("baixado_em") and agora - datetime.fromisoformat(meta["baixado_em"]) < INTERVALO_ATUALIZACAO:
            return
        if meta.get("tentativa_em") and agora - datetime.fromisoformat(meta["tentativa_em"]) < RETENTATIVA_APOS_FALHA:
            return
    try:
        dados = _baixar()
        dados["meta"] = {"baixado_em": agora.isoformat(), "fonte": RESOURCE_PAGE}
    except Exception as exc:
        dados = carregar() if INDICE_PATH.exists() else {}
        dados["meta"] = {**dados.get("meta", {}), "tentativa_em": agora.isoformat(), "erro": str(exc)[:300]}
    tmp = INDICE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    tmp.replace(INDICE_PATH)


def _importar(caminho):
    with _lock:
        _baixando.set()
        _progresso.update(etapa="processando", baixados=0, total=None, linhas=0)
        agora = _agora()
        try:
            with open(caminho, "rb") as f:
                dados = montar_indice(io.BufferedReader(f))
            dados["meta"] = {"baixado_em": agora.isoformat(), "fonte": "arquivo enviado pela Biblioteca de fungicidas"}
        except Exception as exc:
            dados = carregar() if INDICE_PATH.exists() else {}
            dados["meta"] = {**dados.get("meta", {}), "erro": f"Arquivo enviado nao reconhecido: {exc}"[:300]}
        try:
            tmp = INDICE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
            tmp.replace(INDICE_PATH)
        finally:
            Path(caminho).unlink(missing_ok=True)
            _baixando.clear()


def importar_em_segundo_plano(caminho):
    """Arquivo do Agrofit baixado pelo navegador e enviado pela tela
    (o portal do MAPA recusa o download direto pelo servidor -- 403)."""
    _baixando.set()
    threading.Thread(target=_importar, args=(caminho,), daemon=True).start()


def atualizar_em_segundo_plano(forcar=False):
    if _baixando.is_set():
        return
    _baixando.set()
    threading.Thread(target=atualizar, kwargs={"forcar": forcar}, daemon=True).start()


def atualizar_se_necessario():
    """Chamado a cada tick do scheduler -- so' dispara o download (em
    outro thread) quando a base tem mais de 30 dias ou nunca foi baixada."""
    meta = _ler_meta()
    agora = _agora()
    # Indice antigo, sem as marcas comerciais (usadas na conferencia da
    # ADAPAR) -- baixa de novo sem esperar o mes.
    sem_marcas = INDICE_PATH.exists() and "marcas" not in carregar()
    if not sem_marcas and meta.get("baixado_em") and agora - datetime.fromisoformat(meta["baixado_em"]) < INTERVALO_ATUALIZACAO:
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


def eh_biologico(ingrediente):
    chave = chave_biologico(ingrediente)
    return bool(chave) and all(
        p.split(" ")[0] in _GENEROS_BIOLOGICOS or p.split(" ")[0] == "cerevisane" for p in chave.split(" + ")
    )


def _ativos_conhecidos(dados):
    """Todos os ingredientes (quimicos, ja na chave de comparacao) que
    existem no Agrofit, pra separar "sem registro pra esse fungo" de "nome
    nao encontrado" (grafia muito diferente ou produto fora do Agrofit)."""
    if _cache.get("conhecidos_mtime") != _cache["mtime"]:
        conhecidos = set()
        for itens in dados.get("pragas", {}).values():
            for texto_ativos, _ in itens:
                conhecidos.update(chave_quimico(texto_ativos).split(" + "))
        _cache["conhecidos"], _cache["conhecidos_mtime"] = conhecidos, _cache["mtime"]
    return _cache["conhecidos"]


def registros(nome_cientifico):
    """Produtos registrados no Agrofit pro fungo do nome cientifico:
    {"pragas": [...], "quimico": {chave: set(culturas)}, "biologico": {chave:
    set(culturas)}, "nomes": {chave: texto exibido}, "conhecidos": set,
    "culturas": [...]} -- chaves de `chave_quimico`/`chave_biologico`."""
    dados = carregar()
    pragas = pragas_do_nome_cientifico(nome_cientifico, dados.get("pragas", {}))
    resultado = {"pragas": pragas, "quimico": {}, "biologico": {}, "nomes": {},
                 "conhecidos": _ativos_conhecidos(dados), "culturas": dados.get("culturas", [])}
    for praga in pragas:
        for texto_ativos, cultura in dados["pragas"][praga]:
            tipo = "biologico" if eh_biologico(texto_ativos) else "quimico"
            chave = chave_biologico(texto_ativos) if tipo == "biologico" else chave_quimico(texto_ativos)
            resultado[tipo].setdefault(chave, set()).add(cultura)
            resultado["nomes"].setdefault(chave, dados["nomes"].get(texto_ativos, texto_ativos))
    return resultado


_SINONIMOS_CULTURA = {"citrus": "citros"}


def cultura_do_agrofit(cultura, culturas_agrofit):
    """Nome da cultura do site -> nome(s) equivalentes no Agrofit
    ("Algodao" = "Algodão", "Cana" = "Cana-de-açúcar", "Citrus" =
    "Citros"); vazio se o Agrofit nao tem essa cultura (ai' o registro
    dela nao e' mexido)."""
    alvo = normalizar(cultura)
    alvo = _SINONIMOS_CULTURA.get(alvo, alvo)
    return {c for c in culturas_agrofit if normalizar(c) == alvo or re.split(r"[\s-]", normalizar(c))[0] == alvo}


def culturas_cobertas(chave, culturas_por_chave):
    """Culturas em que o item da Biblioteca tem registro: o proprio
    produto registrado, ou uma mistura de tanque de produtos registrados
    (cada um so' com ingredientes do item) que junta todos os
    ingredientes -- ex.: "tebuconazol + metominostrobina" = um produto de
    tebuconazol + um de metominostrobina, registrados na mesma cultura."""
    alvo = set(chave.split(" + "))
    juntos = {}
    for outra, culturas in culturas_por_chave.items():
        partes = set(outra.split(" + "))
        if partes <= alvo:
            for cultura in culturas:
                juntos.setdefault(cultura, set()).update(partes)
    return {cultura for cultura, partes in juntos.items() if partes == alvo}


def marcas_por_chave():
    """{chave_quimico: {marca comercial}} de todos os produtos da base
    (vazio em indice antigo, sem marcas)."""
    dados = carregar()
    if _cache.get("marcas_mtime") != _cache["mtime"]:
        resultado = {}
        for texto_ativos, marcas in dados.get("marcas", {}).items():
            resultado.setdefault(chave_quimico(texto_ativos), set()).update(marcas)
        _cache["marcas"], _cache["marcas_mtime"] = resultado, _cache["mtime"]
    return _cache["marcas"]


def produto_sem_registro(ingrediente):
    """Quimico que nao pode aparecer: banido/suspenso (`INGREDIENTES_BANIDOS`)
    ou, com a base do Agrofit carregada, algum ingrediente que nao tem
    registro vigente nela."""
    chave = chave_quimico(ingrediente)
    if not chave:
        return False
    partes = chave.split(" + ")
    if any(chave_quimico(b) in partes for b in INGREDIENTES_BANIDOS):
        return True
    if not INDICE_PATH.exists():
        return False
    try:
        dados = carregar()
    except Exception:
        return False
    if not dados.get("pragas"):
        return False
    conhecidos = _ativos_conhecidos(dados)
    return any(p not in conhecidos for p in partes)
