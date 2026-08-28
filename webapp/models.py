"""Esquema do banco (SQLite) e funcoes de acesso a usuarios/permissoes/fazendas."""
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from werkzeug.security import generate_password_hash

import data_reader


def fmt_data_br(value):
    """Converte "YYYY-MM-DD" ou "YYYY-MM-DD HH:MM:SS" (formatos usados
    internamente pelos dados/banco) pro padrao brasileiro dd/mm/aa (ou
    dd/mm/aa HH:MM, se tiver hora) -- usado em toda tela/relatorio que
    mostra uma data pro usuario (registrado como filtro Jinja "data_br"
    em app.py, e chamado direto aqui pelo export_excel.py e por rotas
    que montam texto antes de renderizar, ex. o Mapa). Devolve o valor
    original se nao reconhecer o formato, nunca quebra a pagina por
    causa disso."""
    if not value:
        return value
    text = str(value).strip()
    for fmt_in, fmt_out in (
        ("%Y-%m-%d %H:%M:%S", "%d/%m/%y %H:%M"),
        ("%Y-%m-%dT%H:%M:%S", "%d/%m/%y %H:%M"),
        ("%Y-%m-%d", "%d/%m/%y"),
    ):
        try:
            return datetime.strptime(text, fmt_in).strftime(fmt_out)
        except ValueError:
            continue
    return value

# Local (padrao): banco fica do lado do codigo, em webapp/bioscout_web.db --
# nada muda pra quem ja usa assim. Hospedado (Railway/Render), o disco do
# container e' apagado a cada deploy -- BIOSCOUT_DB_PATH deve apontar pra
# um caminho dentro de um volume persistente (ex.: /data/bioscout_web.db),
# senao TODO usuario/permissao/fazenda virtual cadastrado pelo site some no
# proximo deploy.
DB_PATH = Path(os.environ.get("BIOSCOUT_DB_PATH", str(Path(__file__).parent / "bioscout_web.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            email TEXT,
            telefone TEXT,
            whatsapp_apikey TEXT  -- vestigio da epoca do CallMeBot, sem uso desde o whatsapp-bridge
        );

        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS virtual_farms (
            site_name TEXT PRIMARY KEY,
            nome TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            raio_km REAL NOT NULL,
            criado_em TEXT NOT NULL,
            criado_por TEXT
        );

        CREATE TABLE IF NOT EXISTS user_site_permissions (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, site_id)
        );

        CREATE TABLE IF NOT EXISTS user_report_permissions (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, site_id)
        );

        CREATE TABLE IF NOT EXISTS recommendation_notes (
            site_name TEXT NOT NULL,
            doenca TEXT NOT NULL,
            nota TEXT,
            updated_at TEXT,
            PRIMARY KEY (site_name, doenca)
        );

        CREATE TABLE IF NOT EXISTS disease_translations (
            display_name_en TEXT PRIMARY KEY,
            nome_pt TEXT NOT NULL,
            nome_cientifico TEXT,
            condicoes_germinacao TEXT
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS whatsapp_schedule (
            site_name TEXT NOT NULL,
            weekday INTEGER NOT NULL,
            PRIMARY KEY (site_name, weekday)
        );

        CREATE TABLE IF NOT EXISTS whatsapp_envio_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT NOT NULL,
            destinatario TEXT,
            telefone TEXT,
            ok INTEGER NOT NULL,
            mensagem TEXT,
            criado_em TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS fungicida_overrides (
            doenca TEXT NOT NULL,
            tipo TEXT NOT NULL,
            idx INTEGER NOT NULL,
            ingrediente TEXT NOT NULL,
            classe TEXT NOT NULL,
            removido INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (doenca, tipo, idx)
        );

        CREATE TABLE IF NOT EXISTS fungicida_ordem (
            doenca TEXT NOT NULL,
            tipo TEXT NOT NULL,
            posicao INTEGER NOT NULL,
            idx_original INTEGER NOT NULL,
            PRIMARY KEY (doenca, tipo, posicao)
        );

        CREATE TABLE IF NOT EXISTS fungicida_registro_bloqueado (
            doenca TEXT NOT NULL,
            tipo TEXT NOT NULL,
            idx INTEGER NOT NULL,
            cultura TEXT NOT NULL,
            PRIMARY KEY (doenca, tipo, idx, cultura)
        );

        CREATE TABLE IF NOT EXISTS farm_produtos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT NOT NULL,
            safra TEXT NOT NULL DEFAULT 'safra1',
            momento TEXT NOT NULL,
            tipo TEXT NOT NULL,
            nome TEXT,
            ingrediente_ativo TEXT
        );

        CREATE TABLE IF NOT EXISTS farm_plantio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT NOT NULL,
            safra TEXT NOT NULL,
            data_plantio TEXT,
            talhao TEXT,
            variedade TEXT,
            ciclo_dias TEXT
        );

        CREATE TABLE IF NOT EXISTS farm_aplicacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT NOT NULL,
            safra TEXT NOT NULL,
            data_aplicacao TEXT,
            talhao TEXT,
            fungicidas_quimicos TEXT,
            fungicidas_biologicos TEXT
        );

        CREATE TABLE IF NOT EXISTS farm_espacamento_plantio (
            site_name TEXT NOT NULL,
            safra TEXT NOT NULL,
            espacamento TEXT,
            PRIMARY KEY (site_name, safra)
        );

        CREATE TABLE IF NOT EXISTS farm_culturas (
            site_name TEXT NOT NULL,
            safra TEXT NOT NULL,
            cultura TEXT,
            updated_at TEXT,
            PRIMARY KEY (site_name, safra)
        );

        CREATE TABLE IF NOT EXISTS culturas (
            slot INTEGER PRIMARY KEY,
            nome TEXT
        );

        CREATE TABLE IF NOT EXISTS doenca_cultura (
            doenca_en TEXT NOT NULL,
            cultura TEXT NOT NULL,
            PRIMARY KEY (doenca_en, cultura)
        );

        CREATE TABLE IF NOT EXISTS weather_station_overrides (
            site_name TEXT PRIMARY KEY,
            estacao_codigo TEXT NOT NULL
        );
        """
    )
    # farm_sulco_plantio (campo unico de texto livre pro sulco de plantio)
    # foi substituida por "sulco" virar um momento normal de farm_produtos
    # (mesma grade de TS/Folha, com produtos quimicos/biologicos) -- essa
    # tabela chegou a existir em producao por pouco tempo, entao o DROP
    # cuida de limpar quem ja tiver ela criada.
    conn.execute("DROP TABLE IF EXISTS farm_sulco_plantio")
    try:
        conn.execute("ALTER TABLE disease_translations ADD COLUMN nome_cientifico TEXT")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao)
    try:
        conn.execute("ALTER TABLE disease_translations ADD COLUMN condicoes_germinacao TEXT")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao)
    try:
        conn.execute("ALTER TABLE farm_produtos ADD COLUMN data_anotacao TEXT")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao)
    try:
        conn.execute("ALTER TABLE farm_produtos ADD COLUMN safra TEXT NOT NULL DEFAULT 'safra1'")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN telefone TEXT")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN whatsapp_apikey TEXT")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao)

    # farm_culturas mudou a chave primaria de (site_name) para (site_name,
    # safra) -- se o banco foi criado antes dessa versao, a tabela existe
    # com o esquema antigo (sem coluna "safra") e o CREATE TABLE IF NOT
    # EXISTS acima nao fez nada. Migra preservando os dados existentes como
    # "safra1" (a fazenda ja tinha uma cultura atual antes de existir a
    # 2a safra).
    farm_culturas_cols = [r[1] for r in conn.execute("PRAGMA table_info(farm_culturas)")]
    if "safra" not in farm_culturas_cols:
        conn.execute("ALTER TABLE farm_culturas RENAME TO farm_culturas_old")
        conn.execute(
            """
            CREATE TABLE farm_culturas (
                site_name TEXT NOT NULL,
                safra TEXT NOT NULL,
                cultura TEXT,
                updated_at TEXT,
                PRIMARY KEY (site_name, safra)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO farm_culturas (site_name, safra, cultura, updated_at)
            SELECT site_name, 'safra1', cultura, updated_at FROM farm_culturas_old
            """
        )
        conn.execute("DROP TABLE farm_culturas_old")

    # farm_aplicacoes trocou a coluna unica "fungicidas" por duas colunas
    # separadas (quimicos/biologicos, mesma separacao das outras grades) --
    # se o banco foi criado antes dessa versao, migra preservando o texto
    # antigo como "quimicos" (nao da pra saber automaticamente qual parte
    # era biologico, mas assim nao perde o que ja tinha sido digitado).
    farm_aplicacoes_cols = [r[1] for r in conn.execute("PRAGMA table_info(farm_aplicacoes)")]
    if "fungicidas" in farm_aplicacoes_cols:
        conn.execute("ALTER TABLE farm_aplicacoes RENAME TO farm_aplicacoes_old")
        conn.execute(
            """
            CREATE TABLE farm_aplicacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_name TEXT NOT NULL,
                safra TEXT NOT NULL,
                data_aplicacao TEXT,
                talhao TEXT,
                fungicidas_quimicos TEXT,
                fungicidas_biologicos TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO farm_aplicacoes (site_name, safra, data_aplicacao, talhao, fungicidas_quimicos)
            SELECT site_name, safra, data_aplicacao, talhao, fungicidas FROM farm_aplicacoes_old
            """
        )
        conn.execute("DROP TABLE farm_aplicacoes_old")

    if conn.execute("SELECT COUNT(*) c FROM culturas").fetchone()[0] == 0:
        for slot, nome in enumerate(data_reader.DEFAULT_CULTURAS):
            conn.execute("INSERT INTO culturas (slot, nome) VALUES (?, ?)", (slot, nome))

    if conn.execute("SELECT COUNT(*) c FROM doenca_cultura").fetchone()[0] == 0:
        for doenca_en, cultura in data_reader.DEFAULT_DOENCA_CULTURA.items():
            conn.execute(
                "INSERT INTO doenca_cultura (doenca_en, cultura) VALUES (?, ?)", (doenca_en, cultura)
            )

    conn.commit()
    conn.close()


def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO app_settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    conn.commit()
    conn.close()


def get_whatsapp_days(site_name):
    conn = get_db()
    rows = conn.execute("SELECT weekday FROM whatsapp_schedule WHERE site_name = ?", (site_name,)).fetchall()
    conn.close()
    return {r["weekday"] for r in rows}


def get_all_whatsapp_days():
    conn = get_db()
    rows = conn.execute("SELECT site_name, weekday FROM whatsapp_schedule").fetchall()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault(r["site_name"], set()).add(r["weekday"])
    return result


def set_whatsapp_days(site_name, weekdays):
    """Substitui os dias agendados para a fazenda pelo conjunto informado
    (0=Segunda ... 6=Domingo)."""
    conn = get_db()
    conn.execute("DELETE FROM whatsapp_schedule WHERE site_name = ?", (site_name,))
    for wd in weekdays:
        conn.execute(
            "INSERT INTO whatsapp_schedule (site_name, weekday) VALUES (?, ?)", (site_name, wd)
        )
    conn.commit()
    conn.close()


def log_whatsapp_envio(site_name, destinatario, telefone, ok, mensagem):
    """Registra uma tentativa de envio (uma linha por numero, ou uma
    linha so' com destinatario/telefone None quando o envio nem chegou
    a tentar -- fazenda bloqueada por dado velho ou sem ninguem
    cadastrado pra receber) -- usado pela tela de Relatorios."""
    conn = get_db()
    conn.execute(
        "INSERT INTO whatsapp_envio_log (site_name, destinatario, telefone, ok, mensagem) VALUES (?, ?, ?, ?, ?)",
        (site_name, destinatario, telefone, 1 if ok else 0, mensagem),
    )
    conn.commit()
    conn.close()


def get_whatsapp_envio_log(site_name=None, apenas_falhas=False, limit=300):
    """Historico de envios, mais recente primeiro. `site_name` filtra por
    uma fazenda; `apenas_falhas` mostra so as tentativas que nao deram
    certo."""
    conn = get_db()
    condicoes, params = [], []
    if site_name:
        condicoes.append("site_name = ?")
        params.append(site_name)
    if apenas_falhas:
        condicoes.append("ok = 0")
    where = f"WHERE {' AND '.join(condicoes)}" if condicoes else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT site_name, destinatario, telefone, ok, mensagem, criado_em "
        f"FROM whatsapp_envio_log {where} ORDER BY criado_em DESC, id DESC LIMIT ?",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_disease_translations():
    """chave: display_name_en -> {nome_pt, nome_cientifico, condicoes_germinacao}
    -- os campos editaveis na aba Doencas, pra quem monta os cartoes de alerta
    (Painel/Mapa/Manejo) usar o que foi editado ali em vez do valor cru
    que vem da leitura do BioScout (ver `data_reader.get_dashboard_data`,
    que so cai pro valor cru quando nao ha nome cientifico salvo)."""
    conn = get_db()
    rows = conn.execute("SELECT display_name_en, nome_pt, nome_cientifico, condicoes_germinacao FROM disease_translations").fetchall()
    conn.close()
    return {
        r["display_name_en"]: {
            "nome_pt": r["nome_pt"],
            "nome_cientifico": r["nome_cientifico"] or "",
            "condicoes_germinacao": r["condicoes_germinacao"] or "",
        }
        for r in rows
    }


def get_all_disease_info():
    """chave: display_name_en -> {nome_pt, nome_cientifico, condicoes_germinacao}
    -- usado pela tela de admin Doencas (mostra e deixa editar as tres colunas)."""
    conn = get_db()
    rows = conn.execute("SELECT display_name_en, nome_pt, nome_cientifico, condicoes_germinacao FROM disease_translations").fetchall()
    conn.close()
    return {
        r["display_name_en"]: {
            "nome_pt": r["nome_pt"],
            "nome_cientifico": r["nome_cientifico"] or "",
            "condicoes_germinacao": r["condicoes_germinacao"] or "",
        }
        for r in rows
    }


def ensure_disease_translations(display_names, default_map, scientific_map=None):
    """Garante que toda doenca em display_names tenha uma linha na tabela --
    doencas novas (que o BioScout ainda nao tinha reportado) entram com o
    valor padrao (do DOENCA_MAP ou o proprio nome em ingles) e, se
    disponivel, o nome cientifico que o BioScout ja manda nos dados --
    prontas para serem revisadas na tela de admin. Doencas ja cadastradas
    que ainda nao tem nome cientifico salvo sao preenchidas automaticamente
    se um nome cientifico aparecer nos dados agora (sem sobrescrever uma
    edicao manual ja feita)."""
    scientific_map = scientific_map or {}
    conn = get_db()
    existing = {
        r["display_name_en"]: r["nome_cientifico"]
        for r in conn.execute("SELECT display_name_en, nome_cientifico FROM disease_translations")
    }
    for name in display_names:
        if name not in existing:
            conn.execute(
                "INSERT INTO disease_translations (display_name_en, nome_pt, nome_cientifico) VALUES (?, ?, ?)",
                (name, default_map.get(name, name), scientific_map.get(name, "")),
            )
        elif not existing[name] and scientific_map.get(name):
            conn.execute(
                "UPDATE disease_translations SET nome_cientifico = ? WHERE display_name_en = ?",
                (scientific_map[name], name),
            )
    conn.commit()
    conn.close()


def save_disease_translation(display_name_en, nome_pt, nome_cientifico="", condicoes_germinacao=None):
    conn = get_db()
    if condicoes_germinacao is None:
        # so' atualiza nome_pt/nome_cientifico, preserva condicoes_germinacao ja salva
        # (usado pelo formulario de nomes, que nao tem esse campo).
        conn.execute(
            """
            INSERT INTO disease_translations (display_name_en, nome_pt, nome_cientifico) VALUES (?, ?, ?)
            ON CONFLICT(display_name_en) DO UPDATE SET nome_pt = excluded.nome_pt, nome_cientifico = excluded.nome_cientifico
            """,
            (display_name_en, nome_pt, nome_cientifico),
        )
    else:
        conn.execute(
            """
            INSERT INTO disease_translations (display_name_en, nome_pt, nome_cientifico, condicoes_germinacao) VALUES (?, ?, ?, ?)
            ON CONFLICT(display_name_en) DO UPDATE SET nome_pt = excluded.nome_pt, nome_cientifico = excluded.nome_cientifico, condicoes_germinacao = excluded.condicoes_germinacao
            """,
            (display_name_en, nome_pt, nome_cientifico, condicoes_germinacao),
        )
    conn.commit()
    conn.close()


def get_all_fungicida_overrides():
    """chave: (doenca, tipo, idx) -> {ingrediente, classe, removido}"""
    conn = get_db()
    rows = conn.execute(
        "SELECT doenca, tipo, idx, ingrediente, classe, removido FROM fungicida_overrides"
    ).fetchall()
    conn.close()
    return {
        (r["doenca"], r["tipo"], r["idx"]): {
            "ingrediente": r["ingrediente"], "classe": r["classe"], "removido": bool(r["removido"]),
        }
        for r in rows
    }


def save_fungicida_override(doenca, tipo, idx, ingrediente, classe, removido):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO fungicida_overrides (doenca, tipo, idx, ingrediente, classe, removido)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(doenca, tipo, idx) DO UPDATE SET
            ingrediente = excluded.ingrediente, classe = excluded.classe, removido = excluded.removido
        """,
        (doenca, tipo, idx, ingrediente, classe, 1 if removido else 0),
    )
    conn.commit()
    conn.close()


def delete_fungicida_override(doenca, tipo, idx):
    conn = get_db()
    conn.execute(
        "DELETE FROM fungicida_overrides WHERE doenca = ? AND tipo = ? AND idx = ?", (doenca, tipo, idx)
    )
    conn.commit()
    conn.close()


def get_fungicida_ordem(doenca, tipo, n):
    """Lista de `idx_original` (tamanho `n`) na ordem de exibicao atual --
    ordem natural (0..n-1) se essa doenca/tipo nunca foi reordenada pelo
    admin. Indices salvos que nao existem mais (fungicida_data.py mudou de
    tamanho) sao ignorados, e indices novos entram no final."""
    conn = get_db()
    rows = conn.execute(
        "SELECT idx_original FROM fungicida_ordem WHERE doenca = ? AND tipo = ? ORDER BY posicao",
        (doenca, tipo),
    ).fetchall()
    conn.close()
    order = [r["idx_original"] for r in rows if r["idx_original"] < n]
    faltando = [i for i in range(n) if i not in order]
    return order + faltando


def set_fungicida_ordem(doenca, tipo, order):
    conn = get_db()
    conn.execute("DELETE FROM fungicida_ordem WHERE doenca = ? AND tipo = ?", (doenca, tipo))
    for posicao, idx_original in enumerate(order):
        conn.execute(
            "INSERT INTO fungicida_ordem (doenca, tipo, posicao, idx_original) VALUES (?, ?, ?, ?)",
            (doenca, tipo, posicao, idx_original),
        )
    conn.commit()
    conn.close()


def move_fungicida_item(doenca, tipo, idx_original, direction, n):
    """Troca a posicao do item com seu vizinho (`direction` = "up"/"down").
    Sem efeito se ja estiver na ponta correspondente."""
    order = get_fungicida_ordem(doenca, tipo, n)
    if idx_original not in order:
        return
    pos = order.index(idx_original)
    vizinho = pos - 1 if direction == "up" else pos + 1
    if 0 <= vizinho < len(order):
        order[pos], order[vizinho] = order[vizinho], order[pos]
        set_fungicida_ordem(doenca, tipo, order)


def get_all_fungicida_registro_bloqueado():
    """chave: (doenca, tipo, idx) -> set(culturas) SEM registro pra esse
    item -- ausencia (conjunto vazio) significa que tem registro (ou
    ainda nao foi conferido), continua aparecendo normalmente nas
    Recomendacoes/PDF/WhatsApp. So vira bloqueio quando o admin desmarca
    explicitamente a cultura na aba Fungicidas."""
    conn = get_db()
    rows = conn.execute("SELECT doenca, tipo, idx, cultura FROM fungicida_registro_bloqueado").fetchall()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault((r["doenca"], r["tipo"], r["idx"]), set()).add(r["cultura"])
    return result


def set_fungicida_registro_bloqueado(doenca, tipo, idx, culturas_bloqueadas):
    """Substitui o conjunto de culturas SEM registro pra esse item.
    `culturas_bloqueadas` vazio = tem registro pra todas as culturas
    ativas (estado padrao, sem nenhuma linha salva)."""
    conn = get_db()
    conn.execute(
        "DELETE FROM fungicida_registro_bloqueado WHERE doenca = ? AND tipo = ? AND idx = ?",
        (doenca, tipo, idx),
    )
    for cultura in culturas_bloqueadas:
        conn.execute(
            "INSERT INTO fungicida_registro_bloqueado (doenca, tipo, idx, cultura) VALUES (?, ?, ?, ?)",
            (doenca, tipo, idx, cultura),
        )
    conn.commit()
    conn.close()


def bloquear_cultura_nova_em_todos_quimicos(cultura):
    """Quando uma cultura nova e' cadastrada (aba Nome Culturas), bloqueia
    ela de saida em TODO quimico ja existente na biblioteca de
    fungicidas -- ninguem pesquisou registro pra essa cultura ainda,
    entao o padrao seguro e' exigir confirmacao explicita (o admin
    marca uma a uma as que de fato tem registro) em vez de assumir
    que ja esta tudo certo. So afeta quimicos que ja existiam antes
    dessa cultura ser criada -- chamado uma vez, no momento do cadastro."""
    import fungicida_data
    conn = get_db()
    for doenca_en, rec in fungicida_data.RECOMENDACOES.items():
        for idx in range(len(rec["quimicos"]["itens"])):
            conn.execute(
                "INSERT OR IGNORE INTO fungicida_registro_bloqueado (doenca, tipo, idx, cultura) VALUES (?, 'quimico', ?, ?)",
                (doenca_en, idx, cultura),
            )
    conn.commit()
    conn.close()


MOMENTOS = ("ts", "sulco", "folha")
TIPOS_PRODUTO = ("quimico", "biologico")

# As safras que cada fazenda acompanha em paralelo (ex.: soja na safra,
# milho safrinha na 2a safra, uma 3a cultura na 3a safra) -- (chave
# interna, rotulo exibido).
SAFRAS = [("safra1", "Safra"), ("safra2", "2ª Safra"), ("safra3", "3ª Safra")]

# Grade compacta de estoque mostrada na propria aba Recomendacoes -- usa a
# mesma tabela farm_produtos, mas com seu proprio "momento" (nao aparece na
# aba Fazendas, que so olha para MOMENTOS acima), ja que e' so um lembrete
# rapido ao lado dos alertas (o cadastro completo por momento de aplicacao
# continua sendo feito na aba Fazendas).
MOMENTO_ESTOQUE_RAPIDO = "geral"


def get_all_farm_produtos():
    """chave: site_name -> {(safra, momento, tipo): [{"data_anotacao", "nome", "ingrediente_ativo"}, ...]}
    (na ordem em que foram salvos -- usado pela aba Fazendas e Recomendacoes)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT site_name, safra, momento, tipo, data_anotacao, nome, ingrediente_ativo FROM farm_produtos ORDER BY id"
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        bucket = result.setdefault(r["site_name"], {})
        bucket.setdefault((r["safra"], r["momento"], r["tipo"]), []).append({
            "data_anotacao": r["data_anotacao"] or "",
            "nome": r["nome"] or "",
            "ingrediente_ativo": r["ingrediente_ativo"] or "",
        })
    return result


def set_farm_produtos(site_name, safra, momento, tipo, linhas):
    """Substitui as linhas preenchidas daquela grade (safra x momento x
    tipo) da fazenda -- linhas totalmente vazias (data/anotacao, nome e
    ingrediente_ativo em branco) sao descartadas, nao ocupam uma "box" a
    toa. `linhas` e' uma lista de tuplas (data_anotacao, nome, ingrediente_ativo)."""
    conn = get_db()
    conn.execute(
        "DELETE FROM farm_produtos WHERE site_name = ? AND safra = ? AND momento = ? AND tipo = ?",
        (site_name, safra, momento, tipo),
    )
    for data_anotacao, nome, ingrediente_ativo in linhas:
        data_anotacao = (data_anotacao or "").strip()
        nome = (nome or "").strip()
        ingrediente_ativo = (ingrediente_ativo or "").strip()
        if data_anotacao or nome or ingrediente_ativo:
            conn.execute(
                """
                INSERT INTO farm_produtos (site_name, safra, momento, tipo, data_anotacao, nome, ingrediente_ativo)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (site_name, safra, momento, tipo, data_anotacao, nome, ingrediente_ativo),
            )
    conn.commit()
    conn.close()


def get_all_farm_plantio():
    """chave: site_name -> {safra: [{"data_plantio", "talhao", "variedade", "ciclo_dias"}, ...]}"""
    conn = get_db()
    rows = conn.execute(
        "SELECT site_name, safra, data_plantio, talhao, variedade, ciclo_dias FROM farm_plantio ORDER BY id"
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        bucket = result.setdefault(r["site_name"], {})
        bucket.setdefault(r["safra"], []).append({
            "data_plantio": r["data_plantio"] or "",
            "talhao": r["talhao"] or "",
            "variedade": r["variedade"] or "",
            "ciclo_dias": r["ciclo_dias"] or "",
        })
    return result


def set_farm_plantio(site_name, safra, linhas):
    """`linhas` e' uma lista de tuplas (data_plantio, talhao, variedade, ciclo_dias);
    linhas totalmente vazias sao descartadas."""
    conn = get_db()
    conn.execute("DELETE FROM farm_plantio WHERE site_name = ? AND safra = ?", (site_name, safra))
    for data_plantio, talhao, variedade, ciclo_dias in linhas:
        data_plantio = (data_plantio or "").strip()
        talhao = (talhao or "").strip()
        variedade = (variedade or "").strip()
        ciclo_dias = (ciclo_dias or "").strip()
        if data_plantio or talhao or variedade or ciclo_dias:
            conn.execute(
                "INSERT INTO farm_plantio (site_name, safra, data_plantio, talhao, variedade, ciclo_dias) VALUES (?, ?, ?, ?, ?, ?)",
                (site_name, safra, data_plantio, talhao, variedade, ciclo_dias),
            )
    conn.commit()
    conn.close()


def get_all_farm_aplicacoes():
    """chave: site_name -> {safra: [{"data_aplicacao", "talhao",
    "fungicidas_quimicos", "fungicidas_biologicos"}, ...]}"""
    conn = get_db()
    rows = conn.execute(
        "SELECT site_name, safra, data_aplicacao, talhao, fungicidas_quimicos, fungicidas_biologicos "
        "FROM farm_aplicacoes ORDER BY id"
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        bucket = result.setdefault(r["site_name"], {})
        bucket.setdefault(r["safra"], []).append({
            "data_aplicacao": r["data_aplicacao"] or "",
            "talhao": r["talhao"] or "",
            "fungicidas_quimicos": r["fungicidas_quimicos"] or "",
            "fungicidas_biologicos": r["fungicidas_biologicos"] or "",
        })
    return result


def set_farm_aplicacoes(site_name, safra, linhas):
    """`linhas` e' uma lista de tuplas (data_aplicacao, talhao,
    fungicidas_quimicos, fungicidas_biologicos); linhas totalmente vazias
    sao descartadas."""
    conn = get_db()
    conn.execute("DELETE FROM farm_aplicacoes WHERE site_name = ? AND safra = ?", (site_name, safra))
    for data_aplicacao, talhao, fungicidas_quimicos, fungicidas_biologicos in linhas:
        data_aplicacao = (data_aplicacao or "").strip()
        talhao = (talhao or "").strip()
        fungicidas_quimicos = (fungicidas_quimicos or "").strip()
        fungicidas_biologicos = (fungicidas_biologicos or "").strip()
        if data_aplicacao or talhao or fungicidas_quimicos or fungicidas_biologicos:
            conn.execute(
                "INSERT INTO farm_aplicacoes (site_name, safra, data_aplicacao, talhao, "
                "fungicidas_quimicos, fungicidas_biologicos) VALUES (?, ?, ?, ?, ?, ?)",
                (site_name, safra, data_aplicacao, talhao, fungicidas_quimicos, fungicidas_biologicos),
            )
    conn.commit()
    conn.close()


def get_all_farm_espacamento_plantio():
    """chave: (site_name, safra) -> espacamento (texto livre, ex.: "45
    cm") -- so tem linha pra fazenda+safra que ja teve o espacamento
    preenchido."""
    conn = get_db()
    rows = conn.execute("SELECT site_name, safra, espacamento FROM farm_espacamento_plantio").fetchall()
    conn.close()
    return {(r["site_name"], r["safra"]): r["espacamento"] for r in rows}


def set_farm_espacamento_plantio(site_name, safra, espacamento):
    """espacamento = "" (ou None) remove a linha daquela safra."""
    conn = get_db()
    espacamento = (espacamento or "").strip()
    if espacamento:
        conn.execute(
            """
            INSERT INTO farm_espacamento_plantio (site_name, safra, espacamento) VALUES (?, ?, ?)
            ON CONFLICT(site_name, safra) DO UPDATE SET espacamento = excluded.espacamento
            """,
            (site_name, safra, espacamento),
        )
    else:
        conn.execute("DELETE FROM farm_espacamento_plantio WHERE site_name = ? AND safra = ?", (site_name, safra))
    conn.commit()
    conn.close()


def get_all_farm_culturas():
    """chave: (site_name, safra) -> {"cultura", "updated_at"} -- so tem
    linha para fazenda+safra que ja tiveram a cultura definida."""
    conn = get_db()
    rows = conn.execute("SELECT site_name, safra, cultura, updated_at FROM farm_culturas").fetchall()
    conn.close()
    return {(r["site_name"], r["safra"]): {"cultura": r["cultura"], "updated_at": r["updated_at"]} for r in rows}


def set_farm_cultura(site_name, safra, cultura):
    """cultura = "" (ou None) remove o filtro daquela safra -- volta a
    mostrar todas as doencas, como se nunca tivesse sido definida."""
    conn = get_db()
    if cultura:
        conn.execute(
            """
            INSERT INTO farm_culturas (site_name, safra, cultura, updated_at) VALUES (?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(site_name, safra) DO UPDATE SET cultura = excluded.cultura, updated_at = excluded.updated_at
            """,
            (site_name, safra, cultura),
        )
    else:
        conn.execute("DELETE FROM farm_culturas WHERE site_name = ? AND safra = ?", (site_name, safra))
    conn.commit()
    conn.close()


def get_all_weather_station_overrides():
    """site_name -> codigo da estacao INMET escolhida na aba Fazendas pra
    alimentar a previsao (Open-Meteo passa a usar a coordenada dessa
    estacao em vez da coordenada da propria fazenda). So os sites com
    escolha manual aparecem aqui -- sem entrada, o padrao e' usar a
    coordenada da fazenda."""
    conn = get_db()
    rows = conn.execute("SELECT site_name, estacao_codigo FROM weather_station_overrides").fetchall()
    conn.close()
    return {r["site_name"]: r["estacao_codigo"] for r in rows}


def set_weather_station_override(site_name, estacao_codigo):
    """estacao_codigo = "" (ou None) remove a escolha manual -- volta a
    usar a coordenada da propria fazenda pra previsao."""
    conn = get_db()
    if estacao_codigo:
        conn.execute(
            """
            INSERT INTO weather_station_overrides (site_name, estacao_codigo) VALUES (?, ?)
            ON CONFLICT(site_name) DO UPDATE SET estacao_codigo = excluded.estacao_codigo
            """,
            (site_name, estacao_codigo),
        )
    else:
        conn.execute("DELETE FROM weather_station_overrides WHERE site_name = ?", (site_name,))
    conn.commit()
    conn.close()


def get_culturas():
    """Lista de 10 nomes na ordem dos slots (com "" nos ainda nao
    preenchidos) -- menu Opcoes > Nome Culturas."""
    conn = get_db()
    rows = conn.execute("SELECT slot, nome FROM culturas ORDER BY slot").fetchall()
    conn.close()
    return [r["nome"] or "" for r in rows]


def get_culturas_ativas():
    """So os nomes ja preenchidos -- usado nos seletores de cultura atual
    por fazenda (Fazendas/Recomendacoes) e na matriz de doencas."""
    return [c for c in get_culturas() if c]


def set_culturas(nomes):
    """Substitui os 10 nomes (na ordem dos slots)."""
    conn = get_db()
    for slot, nome in enumerate(nomes[:10]):
        conn.execute(
            """
            INSERT INTO culturas (slot, nome) VALUES (?, ?)
            ON CONFLICT(slot) DO UPDATE SET nome = excluded.nome
            """,
            (slot, (nome or "").strip()),
        )
    conn.commit()
    conn.close()


def get_doenca_culturas():
    """chave: doenca_en -> set(culturas) -- matriz doenca x cultura (aba
    Doencas). Doenca ausente ou com set vazio nao e' filtrada por nenhuma
    cultura (sempre aparece)."""
    conn = get_db()
    rows = conn.execute("SELECT doenca_en, cultura FROM doenca_cultura").fetchall()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault(r["doenca_en"], set()).add(r["cultura"])
    return result


def set_doenca_culturas(doenca_en, culturas):
    """Substitui o conjunto de culturas marcadas para aquela doenca."""
    conn = get_db()
    conn.execute("DELETE FROM doenca_cultura WHERE doenca_en = ?", (doenca_en,))
    for cultura in culturas:
        conn.execute("INSERT INTO doenca_cultura (doenca_en, cultura) VALUES (?, ?)", (doenca_en, cultura))
    conn.commit()
    conn.close()


def get_all_recommendation_notes():
    conn = get_db()
    rows = conn.execute("SELECT site_name, doenca, nota FROM recommendation_notes").fetchall()
    conn.close()
    return {(r["site_name"], r["doenca"]): r["nota"] for r in rows}


def save_recommendation_note(site_name, doenca, nota):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO recommendation_notes (site_name, doenca, nota, updated_at)
        VALUES (?, ?, ?, datetime('now', 'localtime'))
        ON CONFLICT(site_name, doenca) DO UPDATE SET nota = excluded.nota, updated_at = excluded.updated_at
        """,
        (site_name, doenca, nota),
    )
    conn.commit()
    conn.close()


def sync_sites(site_names):
    """Garante que a tabela sites tenha uma linha para cada fazenda do CSV."""
    conn = get_db()
    for name in site_names:
        conn.execute(
            "INSERT OR IGNORE INTO sites (site_name) VALUES (?)", (name,)
        )
    conn.commit()
    conn.close()


def get_all_sites():
    conn = get_db()
    rows = conn.execute("SELECT id, site_name FROM sites ORDER BY site_name").fetchall()
    conn.close()
    return rows


def get_all_virtual_farms():
    """Lista de fazendas virtuais/estimadas (ver `virtual_farms.py`) --
    cada uma vira `{"site_name", "nome", "lat", "lon", "raio_km",
    "criado_em", "criado_por"}`."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM virtual_farms ORDER BY nome").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def virtual_farm_site_names():
    """Conjunto com o site_name de toda fazenda virtual/estimada -- usado
    pra saber rapido, numa lista de fazendas (real ou nao), quais sao
    estimadas, sem precisar checar prefixo/sufixo do nome (o nome de uma
    fazenda virtual e' livre, ver `create_virtual_farm`)."""
    return {vf["site_name"] for vf in get_all_virtual_farms()}


def get_virtual_farm(site_name):
    """Uma fazenda virtual pelo site_name, ou None se nao for uma
    (inclusive se `site_name` for de uma fazenda real)."""
    conn = get_db()
    row = conn.execute("SELECT * FROM virtual_farms WHERE site_name = ?", (site_name,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_virtual_farm(nome, lat, lon, raio_km, criado_por=None):
    """Cria uma fazenda virtual/estimada -- `site_name` vira
    '"{nome}" - OneAgro', o mesmo padrao de nome usado em toda tela
    (Painel, Recomendacoes, Mapa, WhatsApp): parecido com o das fazendas
    reais ("OneAgro - X"), mas na ordem invertida e entre aspas, pra dar
    pra notar de relance que foi criada aqui dentro, nao importada do
    BioScout. Levanta sqlite3.IntegrityError se ja existir uma fazenda
    com esse nome (nome precisa ser unico). Tambem registra o site_name
    na tabela `sites`, pra poder aparecer na tela de permissoes igual uma
    fazenda de verdade. Retorna o site_name criado."""
    nome = nome.strip().replace('"', "")
    site_name = f'"{nome}" - OneAgro'
    conn = get_db()
    conn.execute(
        """
        INSERT INTO virtual_farms (site_name, nome, lat, lon, raio_km, criado_em, criado_por)
        VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'), ?)
        """,
        (site_name, nome, lat, lon, raio_km, criado_por),
    )
    conn.execute("INSERT OR IGNORE INTO sites (site_name) VALUES (?)", (site_name,))
    conn.commit()
    conn.close()
    return site_name


def update_virtual_farm(site_name, nome, lat, lon, raio_km):
    """Atualiza nome/coordenada/raio de uma fazenda virtual/estimada. Se
    o nome mudar, o site_name muda junto (mesmo padrao de
    `create_virtual_farm`) -- nesse caso propaga o novo site_name pra
    todas as tabelas que guardam dado por fazenda (sites, anotacoes,
    agenda de WhatsApp, produtos, plantio, aplicacoes, cultura), pra nao
    perder o que ja tinha sido cadastrado pra ela. Levanta
    sqlite3.IntegrityError se o novo nome ja for de outra fazenda
    virtual. Retorna o site_name final (igual ao antigo se o nome nao
    mudou)."""
    nome = nome.strip().replace('"', "")
    novo_site_name = f'"{nome}" - OneAgro'
    conn = get_db()
    if novo_site_name != site_name:
        ja_existe = conn.execute(
            "SELECT 1 FROM virtual_farms WHERE site_name = ?", (novo_site_name,)
        ).fetchone()
        if ja_existe:
            conn.close()
            raise sqlite3.IntegrityError(f"Ja existe uma fazenda virtual chamada '{nome}'")
    conn.execute(
        "UPDATE virtual_farms SET site_name=?, nome=?, lat=?, lon=?, raio_km=? WHERE site_name=?",
        (novo_site_name, nome, lat, lon, raio_km, site_name),
    )
    if novo_site_name != site_name:
        for tabela in (
            "sites", "recommendation_notes", "whatsapp_schedule",
            "farm_produtos", "farm_plantio", "farm_aplicacoes", "farm_espacamento_plantio",
            "farm_culturas", "weather_station_overrides",
        ):
            conn.execute(f"UPDATE {tabela} SET site_name=? WHERE site_name=?", (novo_site_name, site_name))
    conn.commit()
    conn.close()
    return novo_site_name


def delete_virtual_farm(site_name):
    """Apaga a fazenda virtual e tudo que foi cadastrado pra ela (mesmas
    tabelas usadas por uma fazenda de verdade -- permissoes, agenda de
    WhatsApp, produtos, anotacoes, etc.), ja que ela so existe dentro
    deste app (nao vem do CSV do BioScout, entao nao ha por que manter
    resíduo depois de excluida)."""
    conn = get_db()
    conn.execute("DELETE FROM virtual_farms WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM sites WHERE site_name = ?", (site_name,))  # cascade cuida das permissoes
    conn.execute("DELETE FROM recommendation_notes WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM whatsapp_schedule WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM farm_produtos WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM farm_plantio WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM farm_aplicacoes WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM farm_espacamento_plantio WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM farm_culturas WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM weather_station_overrides WHERE site_name = ?", (site_name,))
    conn.commit()
    conn.close()


def get_user_by_username(username):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return row


def get_user_by_id(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def get_all_users():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, username, is_admin, email, telefone FROM users ORDER BY username"
    ).fetchall()
    conn.close()
    return rows


DEFAULT_PASSWORD = "Oneagro01!"  # senha de todo usuario novo, e pra onde o botao "Redefinir senha" volta


def create_user(username, password, is_admin=False, email="", telefone=""):
    conn = get_db()
    conn.execute(
        "INSERT INTO users (username, password_hash, is_admin, email, telefone) VALUES (?, ?, ?, ?, ?)",
        (username, generate_password_hash(password), 1 if is_admin else 0, email, telefone),
    )
    conn.commit()
    conn.close()


def set_user_whatsapp(user_id, telefone):
    """Numero pessoal (aba 'Meu WhatsApp') pra onde vao os relatorios das
    fazendas marcadas pra esse usuario -- ver `get_site_whatsapp_recipients`.
    Desde que o envio passou a ser pelo WhatsApp do administrador (servico
    whatsapp-bridge), o destinatario so precisa do proprio numero -- nao
    tem mais API key pessoal (isso era exigencia do CallMeBot, que nao e'
    mais usado)."""
    conn = get_db()
    conn.execute("UPDATE users SET telefone = ? WHERE id = ?", (telefone, user_id))
    conn.commit()
    conn.close()


def get_site_whatsapp_recipients(site_name):
    """Todo usuario que deve receber os relatorios de WhatsApp daquela
    fazenda -- uma fazenda pode ter varios numeros, a lista e' gerada
    inteiramente a partir do cadastro de usuario: so entra quem tiver
    aquela fazenda marcada na coluna "Receber relatorios" (aba Usuarios,
    admin -- `user_report_permissions`, uma escolha independente do
    acesso normal a fazenda) E que ja tenha telefone cadastrado. Nao
    depende de ser admin nem de ter acesso pra VER a fazenda -- sao
    coisas separadas de proposito."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT DISTINCT u.id, u.username, u.telefone
        FROM users u
        JOIN user_report_permissions r ON r.user_id = u.id
        JOIN sites s ON s.id = r.site_id
        WHERE s.site_name = ?
          AND u.telefone IS NOT NULL AND u.telefone != ''
        ORDER BY u.username
        """,
        (site_name,),
    ).fetchall()
    conn.close()
    return rows


def delete_user(user_id):
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def set_user_password(user_id, password):
    conn = get_db()
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(password), user_id),
    )
    conn.commit()
    conn.close()


def set_user_contato(user_id, email, telefone, is_admin=None):
    """Edita email/telefone (e opcionalmente o status de admin) de um
    usuario ja existente -- usuarios criados antes desse campo existir
    (ou com dado errado) ficam sem jeito de corrigir sem isso."""
    conn = get_db()
    if is_admin is None:
        conn.execute(
            "UPDATE users SET email = ?, telefone = ? WHERE id = ?",
            (email, telefone, user_id),
        )
    else:
        conn.execute(
            "UPDATE users SET email = ?, telefone = ?, is_admin = ? WHERE id = ?",
            (email, telefone, 1 if is_admin else 0, user_id),
        )
    conn.commit()
    conn.close()


def get_user_permitted_site_names(user_id):
    conn = get_db()
    rows = conn.execute(
        """
        SELECT s.site_name FROM sites s
        JOIN user_site_permissions p ON p.site_id = s.id
        WHERE p.user_id = ?
        ORDER BY s.site_name
        """,
        (user_id,),
    ).fetchall()
    conn.close()
    return [r["site_name"] for r in rows]


def get_user_permitted_site_ids(user_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT site_id FROM user_site_permissions WHERE user_id = ?", (user_id,)
    ).fetchall()
    conn.close()
    return {r["site_id"] for r in rows}


def set_user_permissions(user_id, site_ids):
    """Substitui as permissoes do usuario pelo conjunto de site_ids informado."""
    conn = get_db()
    conn.execute("DELETE FROM user_site_permissions WHERE user_id = ?", (user_id,))
    for site_id in site_ids:
        conn.execute(
            "INSERT INTO user_site_permissions (user_id, site_id) VALUES (?, ?)",
            (user_id, site_id),
        )
    conn.commit()
    conn.close()


def get_user_report_site_ids(user_id):
    """Fazendas marcadas na coluna 'Receber relatorios' -- independente do
    acesso normal a fazenda (`user_site_permissions`); e' uma escolha a
    parte, feita fazenda por fazenda, de quem deve receber o relatorio de
    WhatsApp daquela fazenda."""
    conn = get_db()
    rows = conn.execute(
        "SELECT site_id FROM user_report_permissions WHERE user_id = ?", (user_id,)
    ).fetchall()
    conn.close()
    return {r["site_id"] for r in rows}


def set_user_report_permissions(user_id, site_ids):
    """Substitui as fazendas marcadas pra esse usuario receber relatorio."""
    conn = get_db()
    conn.execute("DELETE FROM user_report_permissions WHERE user_id = ?", (user_id,))
    for site_id in site_ids:
        conn.execute(
            "INSERT INTO user_report_permissions (user_id, site_id) VALUES (?, ?)",
            (user_id, site_id),
        )
    conn.commit()
    conn.close()
