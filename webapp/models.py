"""Esquema do banco (SQLite) e funcoes de acesso a usuarios/permissoes/fazendas."""
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from werkzeug.security import generate_password_hash

import data_reader


def _agora_cuiaba():
    """'YYYY-MM-DD HH:MM:SS' no horario de Cuiaba-MT (UTC-4, sem horario
    de verao -- por isso um deslocamento fixo, sem precisar de zoneinfo).
    Calculado em Python a partir do UTC real (`datetime.now(timezone.utc)`),
    NUNCA via um default `datetime('now', ...)` na definicao da tabela --
    esses defaults ficam "congelados" no valor de quando aquela tabela foi
    criada em cada banco (`CREATE TABLE IF NOT EXISTS` nao re-aplica o
    default numa tabela que ja existe), entao mudar o texto do default no
    codigo nunca corrigia um banco que ja tivesse a tabela -- foi
    exatamente isso que deixou o historico de envios de WhatsApp em UTC
    por muito tempo mesmo depois de duas tentativas de correcao via SQL."""
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")


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


def parse_data_flexivel(value):
    """Tenta reconhecer `value` como uma data (ISO `YYYY-MM-DD`, ou
    formato brasileiro `dd/mm/aaaa`/`dd/mm/aa`) e devolve sempre em ISO
    -- ou `None` se nao for nenhum desses formatos. A STRING INTEIRA
    precisa bater com o formato (nao extrai uma data de dentro de um
    texto maior, tipo "12/03 - comprado atrasado" -- de proposito, pra
    nao criar falso positivo). Usado em dois lugares: (1) normalizar pra
    ISO um valor antigo salvo como texto livre antes de mostrar num
    `<input type="date">` (que so' aceita ISO, senao fica em branco); e
    (2) decidir se uma linha de Plantio/Aplicacoes/Produtos entra na
    correlacao de atividades da aba "Relatorio Diario" do Excel (ver
    `export_excel._atividades_por_site_dia`) -- so' produtos com uma
    data DE VERDADE em `data_anotacao` entram (esse campo tambem aceita
    anotacao livre, de proposito, entao a maioria nao vai bater aqui, o
    que e' o comportamento certo). Ano de 2 digitos (`dd/mm/aa`) segue a
    regra padrao do Python: 00-68 vira 2000-2068, 69-99 vira 1969-1999."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt_in in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(text, fmt_in).date().isoformat()
        except ValueError:
            continue
    return None


def fmt_telefone_br(value):
    """Formata um telefone salvo como so' digitos (DDI+DDD+numero, ex.:
    '5566996319500') no padrao "(55)66-99631-9500" -- usado em toda
    tela/relatorio que mostra telefone de usuario ou subordinado
    (registrado como filtro Jinja "telefone_br" em app.py, e chamado
    direto aqui pelo export_excel.py). So' formata os dois tamanhos
    normais de numero brasileiro (13 digitos = DDI+DDD+9 digitos movel;
    12 digitos = DDI+DDD+8 digitos fixo/antigo) -- fora isso, devolve o
    valor original em vez de arriscar formatar errado."""
    if not value:
        return value
    digitos = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digitos) not in (12, 13):
        return value
    ddi, ddd, numero = digitos[:2], digitos[2:4], digitos[4:]
    meio = 5 if len(numero) == 9 else 4
    return f"({ddi}){ddd}-{numero[:meio]}-{numero[meio:]}"

# Local (padrao): banco fica do lado do codigo, em webapp/bioscout_web.db --
# nada muda pra quem ja usa assim. Hospedado (Railway/Render), o disco do
# container e' apagado a cada deploy -- BIOSCOUT_DB_PATH deve apontar pra
# um caminho dentro de um volume persistente (ex.: /data/bioscout_web.db),
# senao TODO usuario/permissao/fazenda virtual cadastrado pelo site some no
# proximo deploy.
DB_PATH = Path(os.environ.get("BIOSCOUT_DB_PATH", str(Path(__file__).parent / "bioscout_web.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL deixa leitura (paineis, mapa, etc) rolar junto com escrita
    # (autosave, agendador de WhatsApp) sem uma travar a outra -- sem isso,
    # duas conexoes gravando quase ao mesmo tempo podem colidir com
    # "database is locked" e a edicao do usuario se perde sem retry.
    conn.execute("PRAGMA journal_mode = WAL")
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
            whatsapp_apikey TEXT,  -- vestigio da epoca do CallMeBot, sem uso desde o whatsapp-bridge
            whatsapp_pausado INTEGER NOT NULL DEFAULT 0,
            temp_password_hash TEXT,  -- senha temporaria de "esqueci a senha" (ver set_user_temp_password) -- NAO substitui password_hash, so' um 2o jeito de entrar ate' ser usada ou expirar
            temp_password_expires_at TEXT
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
            criado_por TEXT,
            tipo TEXT NOT NULL DEFAULT 'doenca'
        );

        CREATE TABLE IF NOT EXISTS user_site_permissions (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, site_id)
        );

        CREATE TABLE IF NOT EXISTS subordinados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            nome TEXT NOT NULL,
            telefone TEXT NOT NULL,
            whatsapp_pausado INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS subordinado_report_permissions (
            subordinado_id INTEGER NOT NULL REFERENCES subordinados(id) ON DELETE CASCADE,
            site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            PRIMARY KEY (subordinado_id, site_id)
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

        CREATE TABLE IF NOT EXISTS site_climate_notes (
            site_name TEXT PRIMARY KEY,
            nota TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS disease_translations (
            display_name_en TEXT PRIMARY KEY,
            nome_pt TEXT NOT NULL,
            nome_cientifico TEXT,
            condicoes_germinacao TEXT,
            germ_temp_min REAL,
            germ_temp_max REAL,
            germ_ur_min REAL,
            germ_molhamento_horas REAL,
            germ_agua_livre_inibe INTEGER NOT NULL DEFAULT 0
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

        CREATE TABLE IF NOT EXISTS whatsapp_schedule_pdf (
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
            -- so' uma rede de seguranca -- `log_whatsapp_envio` sempre manda
            -- o valor certo explicito (`_agora_cuiaba()`), sem depender
            -- deste default (que, alias, so vale pra tabela NOVA -- um banco
            -- que ja tivesse essa tabela fica com o default antigo pra
            -- sempre, ver `_agora_cuiaba`).
            criado_em TEXT NOT NULL DEFAULT (datetime('now'))
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

        CREATE TABLE IF NOT EXISTS doenca_pais (
            doenca_en TEXT NOT NULL,
            country_code TEXT NOT NULL,
            PRIMARY KEY (doenca_en, country_code)
        );

        CREATE TABLE IF NOT EXISTS paises_doenca_slots (
            slot INTEGER PRIMARY KEY,
            nome TEXT
        );

        CREATE TABLE IF NOT EXISTS weather_station_overrides (
            site_name TEXT PRIMARY KEY,
            estacao_codigo TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS site_country_overrides (
            site_name TEXT PRIMARY KEY,
            country_code TEXT NOT NULL DEFAULT 'BR'
        );

        CREATE TABLE IF NOT EXISTS site_display_names (
            site_name TEXT PRIMARY KEY,
            nome_exibicao TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS farm_ndvi_area (
            site_name TEXT PRIMARY KEY,
            imagem BLOB,
            imagem_gerada_em TEXT
        );

        CREATE TABLE IF NOT EXISTS farm_ndvi_car (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT NOT NULL,
            nome TEXT NOT NULL,
            kml TEXT NOT NULL,
            criado_em TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_farm_ndvi_car_site ON farm_ndvi_car(site_name);

        CREATE TABLE IF NOT EXISTS farm_ndvi_historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT NOT NULL,
            data_alvo TEXT NOT NULL,
            imagem BLOB NOT NULL,
            gerado_em TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_farm_ndvi_historico_site ON farm_ndvi_historico(site_name);

        CREATE TABLE IF NOT EXISTS ndvi_agendamento (
            site_name TEXT PRIMARY KEY,
            frequencia TEXT NOT NULL,
            proxima_execucao TEXT NOT NULL,
            hora INTEGER NOT NULL DEFAULT 7
        );

        CREATE TABLE IF NOT EXISTS ndvi_whatsapp_ativos (
            site_name TEXT NOT NULL,
            telefone TEXT NOT NULL,
            PRIMARY KEY (site_name, telefone)
        );

        CREATE TABLE IF NOT EXISTS whatsapp_schedule_horarios (
            site_name TEXT PRIMARY KEY,
            hora_texto INTEGER NOT NULL DEFAULT 7,
            hora_pdf INTEGER NOT NULL DEFAULT 7
        );

        CREATE TABLE IF NOT EXISTS site_solo_cache (
            site_name TEXT PRIMARY KEY,
            classe TEXT,
            ordem TEXT,
            fonte TEXT,
            resolvido_em TEXT NOT NULL
        );
        """
    )
    try:
        conn.execute("ALTER TABLE ndvi_agendamento ADD COLUMN hora INTEGER NOT NULL DEFAULT 7")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao, ou pela CREATE TABLE acima)
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
        # condicoes_germinacao (texto livre): legado -- desde que os
        # limites numericos abaixo foram adicionados, o texto exibido na
        # aba Doencas/Manejo e' gerado a partir deles (uma coluna so' pra
        # nao arriscar o texto ficar dessincronizado do que a luz de risco
        # realmente usa). A coluna continua existindo no banco (dado
        # antigo, inofensivo) mas o app nao le nem escreve mais nela.
        conn.execute("ALTER TABLE disease_translations ADD COLUMN condicoes_germinacao TEXT")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao)
    for coluna, tipo in [
        ("germ_temp_min", "REAL"), ("germ_temp_max", "REAL"), ("germ_ur_min", "REAL"),
        ("germ_molhamento_horas", "REAL"), ("germ_agua_livre_inibe", "INTEGER NOT NULL DEFAULT 0"),
    ]:
        try:
            conn.execute(f"ALTER TABLE disease_translations ADD COLUMN {coluna} {tipo}")
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
    try:
        conn.execute("ALTER TABLE farm_ndvi_historico ADD COLUMN cobertura_nuvens REAL")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao)
    try:
        conn.execute("ALTER TABLE farm_ndvi_historico ADD COLUMN thumbnail BLOB")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao)
    try:
        # Estacao escolhida pode ser do INMET (Brasil) ou da DMC (Chile) --
        # sem essa coluna, um codigo de estacao chileno colidiria com a
        # numeracao do INMET. Default 'BR' preserva o comportamento de
        # sempre pras escolhas ja feitas antes dessa coluna existir.
        conn.execute("ALTER TABLE weather_station_overrides ADD COLUMN country_code TEXT NOT NULL DEFAULT 'BR'")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao)
    try:
        # 'doenca' (padrao/legado) interpola concentracao de doenca (IDW)
        # das fazendas reais no raio, igual sempre foi. 'clima' e' um
        # ponto que existe SO' pra dar ao cliente uma referencia de clima
        # (previsao/risco via Open-Meteo ou estacao oficial escolhida) --
        # nao tenta interpolar doenca nenhuma, e por isso aparece no mapa
        # mesmo sem fazenda real por perto (ver `mapa_interpolado` em
        # app.py, pedido explicito do usuario).
        conn.execute("ALTER TABLE virtual_farms ADD COLUMN tipo TEXT NOT NULL DEFAULT 'doenca'")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN whatsapp_pausado INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN temp_password_hash TEXT")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN temp_password_expires_at TEXT")
    except sqlite3.OperationalError:
        pass  # coluna ja existe (banco criado antes dessa versao)
    try:
        conn.execute("ALTER TABLE subordinados ADD COLUMN whatsapp_pausado INTEGER NOT NULL DEFAULT 0")
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

    # farm_ndvi_area guardava so' 1 contorno por fazenda (kml unico,
    # sobrescrito a cada salvamento) -- agora cada CAR anexado vira uma
    # linha propria em farm_ndvi_car (permite anexar varios CAR por
    # fazenda, cada um removivel independente; o NDVI gerado combina
    # TODOS os poligonos anexados, ver `pre_visualizar_ndvi` em app.py).
    # Se o banco foi criado antes dessa versao, farm_ndvi_area ainda tem
    # a coluna "kml" antiga -- migra o contorno existente como o
    # primeiro CAR de cada fazenda e reduz a tabela pro novo formato
    # (so' cache da ultima imagem gerada).
    farm_ndvi_area_cols = [r[1] for r in conn.execute("PRAGMA table_info(farm_ndvi_area)")]
    if "kml" in farm_ndvi_area_cols:
        conn.execute("ALTER TABLE farm_ndvi_area RENAME TO farm_ndvi_area_old")
        conn.execute(
            """
            CREATE TABLE farm_ndvi_area (
                site_name TEXT PRIMARY KEY,
                imagem BLOB,
                imagem_gerada_em TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO farm_ndvi_area (site_name, imagem, imagem_gerada_em)
            SELECT site_name, imagem, imagem_gerada_em FROM farm_ndvi_area_old
            """
        )
        conn.execute(
            """
            INSERT INTO farm_ndvi_car (site_name, nome, kml, criado_em)
            SELECT site_name, 'CAR 1', kml, atualizado_em FROM farm_ndvi_area_old
            WHERE kml IS NOT NULL AND kml != ''
            """
        )
        conn.execute("DROP TABLE farm_ndvi_area_old")

    if conn.execute("SELECT COUNT(*) c FROM culturas").fetchone()[0] == 0:
        for slot, nome in enumerate(data_reader.DEFAULT_CULTURAS):
            conn.execute("INSERT INTO culturas (slot, nome) VALUES (?, ?)", (slot, nome))
    # Slots 10/11 (12 culturas no total, eram 10) -- banco ja existente
    # (que ja tinha as 10 posicoes preenchidas antes, entao o bloco acima
    # nao roda de novo) so ganha as 2 posicoes novas, vazias, aqui.
    for slot in (10, 11):
        conn.execute("INSERT OR IGNORE INTO culturas (slot, nome) VALUES (?, '')", (slot,))

    if conn.execute("SELECT COUNT(*) c FROM doenca_cultura").fetchone()[0] == 0:
        for doenca_en, cultura in data_reader.DEFAULT_DOENCA_CULTURA.items():
            conn.execute(
                "INSERT INTO doenca_cultura (doenca_en, cultura) VALUES (?, ?)", (doenca_en, cultura)
            )

    if conn.execute("SELECT COUNT(*) c FROM paises_doenca_slots").fetchone()[0] == 0:
        # Nasce com os 12 paises ja cadastrados em `countries.py` (mesma
        # ordem do seletor de Pais das abas Fazendas/Mapa Interpolado),
        # mas a partir daqui vira texto livre editavel direto no
        # cabecalho da matriz Doencas x Pais (aba Doencas) -- igual
        # `culturas`/`doenca_cultura` -- pra poder cadastrar um pais fora
        # da America do Sul (fora do registro `countries.py`, que exige
        # fronteira/estacao de verdade) so' pra marcar doenca, se um dia
        # a OneAgro vender pra outro continente. Import local (nao no
        # topo do arquivo) so' pra esse seed pontual -- ver nota em
        # `bloquear_cultura_nova_em_todos_quimicos` sobre o mesmo padrao.
        import countries as _countries
        nomes_iniciais = [info["nome"] for info in _countries.COUNTRIES.values()]
        for slot, nome in enumerate(nomes_iniciais):
            conn.execute("INSERT INTO paises_doenca_slots (slot, nome) VALUES (?, ?)", (slot, nome))
        # `doenca_pais.country_code` guardava o CODIGO ISO (ex. 'BR') ate
        # aqui -- migra pro NOME (ex. 'Brasil') pra bater com o novo
        # esquema por texto livre (mesma chave que a UI agora usa).
        codigo_para_nome = {code: info["nome"] for code, info in _countries.COUNTRIES.items()}
        for codigo, nome in codigo_para_nome.items():
            conn.execute(
                "UPDATE OR IGNORE doenca_pais SET country_code = ? WHERE country_code = ?", (nome, codigo)
            )
            conn.execute("DELETE FROM doenca_pais WHERE country_code = ?", (codigo,))

    # Ponto "so clima" criado antes do padrao de nome dedicado
    # ('{nome} - Clima - OneAgro') ainda esta com o nome antigo (mesmo
    # padrao do ponto de doenca, '"{nome}" - OneAgro') -- corrige uma
    # vez so' aqui, com a mesma propagacao de rename do
    # `update_virtual_farm`, pra nao depender de alguem reabrir e salvar
    # o ponto na mao pra ele migrar.
    for row in conn.execute("SELECT site_name, nome FROM virtual_farms WHERE tipo = 'clima'").fetchall():
        esperado = _virtual_farm_site_name(row["nome"], "clima")
        if row["site_name"] != esperado:
            conn.execute(
                "UPDATE virtual_farms SET site_name=? WHERE site_name=?", (esperado, row["site_name"])
            )
            for tabela in _VIRTUAL_FARM_RENAME_TABLES:
                conn.execute(f"UPDATE {tabela} SET site_name=? WHERE site_name=?", (esperado, row["site_name"]))

    # `countries.estacoes_mais_proximas_global` tinha um bug real: fazenda
    # do Chile sem nenhuma estacao DMC disponivel (credencial
    # METEOCHILE_USUARIO/TOKEN nao configurada -> catalogo DMC sempre
    # vazio) acabava herdando a estacao INMET (Brasil) mais proxima
    # mesmo assim -- ja aconteceu de verdade com uma estacao a mais de
    # 1500km de distancia (Uruguaiana-RS). Limpa aqui qualquer
    # combinacao BR<->CL ja gravada por esse bug -- Brasil e Chile NUNCA
    # fazem fronteira entre si (Argentina/Bolivia ficam no meio), entao
    # essa combinacao especifica NUNCA e' uma escolha legitima de
    # "estacao de pais vizinho" (ao contrario de BR/UY ou BR/AR, que sao
    # fronteira de verdade) -- seguro rodar em toda subida, nunca vai
    # apagar uma escolha genuina. Limpa a linha inteira (nao so' zera o
    # codigo) pra `app._auto_detectar_estacoes_novas` tentar de novo com
    # a correcao (ou deixar em "coordenada propria" se nao achar nada
    # dentro da distancia maxima agora).
    site_countries_atual = {
        r["site_name"]: r["country_code"] for r in conn.execute("SELECT site_name, country_code FROM site_country_overrides")
    }
    for row in conn.execute(
        "SELECT site_name, country_code FROM weather_station_overrides WHERE estacao_codigo != ''"
    ).fetchall():
        pais_fazenda = site_countries_atual.get(row["site_name"], "BR")
        if {row["country_code"], pais_fazenda} == {"BR", "CL"}:
            conn.execute("DELETE FROM weather_station_overrides WHERE site_name = ?", (row["site_name"],))

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


_WHATSAPP_SEND_HOUR_KEY = "whatsapp_send_hour"
_WHATSAPP_SEND_HOUR_DEFAULT = "7"


def get_whatsapp_send_hour():
    """Hora do dia (0-23, como string) em que o envio agendado roda --
    relatorio (texto/PDF) e NDVI agendado usam o MESMO horario (ver
    `app._whatsapp_scheduler_loop`). Editavel na aba WhatsApp (admin);
    "7" e' o valor de sempre, mantido como default pra quem nunca mexeu
    nessa configuracao."""
    valor = get_setting(_WHATSAPP_SEND_HOUR_KEY, _WHATSAPP_SEND_HOUR_DEFAULT)
    try:
        hora = int(valor)
    except (TypeError, ValueError):
        return int(_WHATSAPP_SEND_HOUR_DEFAULT)
    return hora if 0 <= hora <= 23 else int(_WHATSAPP_SEND_HOUR_DEFAULT)


def set_whatsapp_send_hour(hora):
    try:
        hora_int = int(hora)
    except (TypeError, ValueError):
        return
    if 0 <= hora_int <= 23:
        set_setting(_WHATSAPP_SEND_HOUR_KEY, str(hora_int))


def get_all_whatsapp_schedule_horarios():
    """{site_name: {"texto": hora, "pdf": hora}} -- horario individual
    por fazenda do envio agendado de relatorio (texto e PDF podem ter
    horarios diferentes entre si, cada um do seu jeito -- pedido
    explicito do usuario, pra poder espalhar os envios ao longo do dia
    em vez de todas as fazendas mandarem no mesmo horario). So' tem
    linha pra fazenda que ja teve o horario customizado alguma vez --
    `get_whatsapp_schedule_horarios` cobre o default pra quem nunca
    mexeu."""
    conn = get_db()
    rows = conn.execute("SELECT site_name, hora_texto, hora_pdf FROM whatsapp_schedule_horarios").fetchall()
    conn.close()
    return {r["site_name"]: {"texto": r["hora_texto"], "pdf": r["hora_pdf"]} for r in rows}


def get_whatsapp_schedule_horarios(site_name):
    """Horario (0-23) do envio agendado de texto/PDF dessa fazenda --
    `get_whatsapp_send_hour()` (default global) pra quem nunca
    customizou, ver `get_all_whatsapp_schedule_horarios`."""
    default = get_whatsapp_send_hour()
    conn = get_db()
    row = conn.execute(
        "SELECT hora_texto, hora_pdf FROM whatsapp_schedule_horarios WHERE site_name = ?", (site_name,)
    ).fetchone()
    conn.close()
    if not row:
        return {"texto": default, "pdf": default}
    return {"texto": row["hora_texto"], "pdf": row["hora_pdf"]}


def set_whatsapp_schedule_hora(site_name, tipo, hora):
    """Atualiza SO' o horario de um tipo (`tipo` = 'texto' ou 'pdf') pra
    essa fazenda, sem mexer no outro -- os dois campos ficam em telas/
    forms separados (aba Fazendas), entao salvar um nao pode sobrescrever
    o outro com o default. Cria a linha com o default global
    (`get_whatsapp_send_hour()`) no campo ainda nao customizado, se for
    a primeira vez que essa fazenda tem qualquer horario customizado."""
    coluna = "hora_texto" if tipo == "texto" else "hora_pdf"
    default = get_whatsapp_send_hour()
    conn = get_db()
    conn.execute(
        f"""
        INSERT INTO whatsapp_schedule_horarios (site_name, hora_texto, hora_pdf) VALUES (?, ?, ?)
        ON CONFLICT(site_name) DO UPDATE SET {coluna} = excluded.{coluna}
        """,
        (site_name, hora if tipo == "texto" else default, hora if tipo == "pdf" else default),
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
    """Substitui os dias agendados (envio do TEXTO) para a fazenda pelo
    conjunto informado (0=Segunda ... 6=Domingo)."""
    conn = get_db()
    conn.execute("DELETE FROM whatsapp_schedule WHERE site_name = ?", (site_name,))
    for wd in weekdays:
        conn.execute(
            "INSERT INTO whatsapp_schedule (site_name, weekday) VALUES (?, ?)", (site_name, wd)
        )
    conn.commit()
    conn.close()


def get_whatsapp_days_pdf(site_name):
    conn = get_db()
    rows = conn.execute("SELECT weekday FROM whatsapp_schedule_pdf WHERE site_name = ?", (site_name,)).fetchall()
    conn.close()
    return {r["weekday"] for r in rows}


def get_all_whatsapp_days_pdf():
    conn = get_db()
    rows = conn.execute("SELECT site_name, weekday FROM whatsapp_schedule_pdf").fetchall()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault(r["site_name"], set()).add(r["weekday"])
    return result


def set_whatsapp_days_pdf(site_name, weekdays):
    """Substitui os dias agendados (envio do PDF) para a fazenda pelo
    conjunto informado (0=Segunda ... 6=Domingo) -- independente dos dias
    do texto (`set_whatsapp_days`), pra poder mandar cada formato em dias
    diferentes da semana."""
    conn = get_db()
    conn.execute("DELETE FROM whatsapp_schedule_pdf WHERE site_name = ?", (site_name,))
    for wd in weekdays:
        conn.execute(
            "INSERT INTO whatsapp_schedule_pdf (site_name, weekday) VALUES (?, ?)", (site_name, wd)
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
        "INSERT INTO whatsapp_envio_log (site_name, destinatario, telefone, ok, mensagem, criado_em) VALUES (?, ?, ?, ?, ?, ?)",
        (site_name, destinatario, telefone, 1 if ok else 0, mensagem, _agora_cuiaba()),
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


_DISEASE_INFO_COLUMNS = (
    "display_name_en, nome_pt, nome_cientifico, "
    "germ_temp_min, germ_temp_max, germ_ur_min, germ_molhamento_horas, germ_agua_livre_inibe"
)


def _disease_info_row_to_dict(r):
    return {
        "nome_pt": r["nome_pt"],
        "nome_cientifico": r["nome_cientifico"] or "",
        "germ_temp_min": r["germ_temp_min"],
        "germ_temp_max": r["germ_temp_max"],
        "germ_ur_min": r["germ_ur_min"],
        "germ_molhamento_horas": r["germ_molhamento_horas"],
        "germ_agua_livre_inibe": bool(r["germ_agua_livre_inibe"]),
    }


def get_all_disease_translations():
    """chave: display_name_en -> {nome_pt, nome_cientifico, germ_temp_min,
    germ_temp_max, germ_ur_min, germ_molhamento_horas, germ_agua_livre_inibe}
    -- os campos editaveis na aba Doencas, pra quem monta os cartoes de
    alerta (Painel/Mapa/Manejo) usar o que foi editado ali em vez do valor
    cru que vem da leitura do BioScout (ver `data_reader.get_dashboard_data`,
    que so cai pro valor cru quando nao ha nome cientifico salvo). Os
    campos germ_* sao a UNICA fonte dos dados de germinacao -- alimentam a
    luz de risco (verde/amarelo/vermelho) E o texto exibido na aba Manejo,
    gerado a partir deles (ver `_formatar_condicoes_germinacao` e
    `_calc_risco_germinacao` em app.py) -- assim o texto nunca fica
    dessincronizado do que a luz realmente calcula."""
    conn = get_db()
    rows = conn.execute(f"SELECT {_DISEASE_INFO_COLUMNS} FROM disease_translations").fetchall()
    conn.close()
    return {r["display_name_en"]: _disease_info_row_to_dict(r) for r in rows}


def get_all_disease_info():
    """Mesmo dado de `get_all_disease_translations` -- usado pela tela de
    admin Doencas (mostra e deixa editar todas as colunas)."""
    conn = get_db()
    rows = conn.execute(f"SELECT {_DISEASE_INFO_COLUMNS} FROM disease_translations").fetchall()
    conn.close()
    return {r["display_name_en"]: _disease_info_row_to_dict(r) for r in rows}


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


def merge_duplicate_disease_translations(canonico_por_atual):
    """canonico_por_atual: display_name_en atual (qualquer grafia ja
    vista, ver `data_reader.nome_canonico_para`) -> grafia canonica que
    deveria valer daqui pra frente. Antes de `data_reader` normalizar a
    capitalizacao do `displayName` do BioScout, a mesma doenca podia
    virar mais de uma linha em disease_translations (ex.: "Grey Mould" x
    "Grey mould", confirmado na pratica -- uma fazenda do Brasil, outra
    do Chile, mesmo fungo). Essa funcao junta essas linhas na canonica --
    preserva nome cientifico/germinacao ja preenchido de QUALQUER uma
    das duplicatas (nunca descarta dado, mesma logica de
    `aplicar_pesquisa_germinacao` em app.py) e remapeia
    doenca_cultura/fungicida_* pra' apontar pra' canonica -- e apaga a(s)
    linha(s) sobrando. Chamada toda vez que a aba Doencas carrega
    (`_load_translations` em app.py); barata quando nao ha' nada pra'
    juntar, se auto-cura sozinha sem precisar de migracao manual."""
    conn = get_db()
    rows = conn.execute(f"SELECT {_DISEASE_INFO_COLUMNS} FROM disease_translations").fetchall()
    por_atual = {r["display_name_en"]: dict(r) for r in rows}

    grupos = {}
    for atual in por_atual:
        canonico = canonico_por_atual.get(atual, atual)
        grupos.setdefault(canonico, []).append(atual)

    for canonico, variantes in grupos.items():
        sobras = [v for v in variantes if v != canonico]
        if not sobras:
            continue  # essa doenca nao tem duplicata -- nada a fazer
        if canonico not in por_atual:
            # a propria grafia canonica nunca virou linha (todas as
            # leituras ja vistas usaram outra grafia) -- promove a
            # primeira sobra pra virar a linha canonica em vez de so
            # apagar tudo.
            promovida, *sobras = sobras
            conn.execute(
                "UPDATE disease_translations SET display_name_en = ? WHERE display_name_en = ?",
                (canonico, promovida),
            )
            por_atual[canonico] = por_atual.pop(promovida)
            if not sobras:
                conn.commit()
                continue
        base = por_atual[canonico]
        for sobra in sobras:
            dup = por_atual[sobra]
            if not base.get("nome_cientifico") and dup.get("nome_cientifico"):
                base["nome_cientifico"] = dup["nome_cientifico"]
            for campo in ("germ_temp_min", "germ_temp_max", "germ_ur_min", "germ_molhamento_horas"):
                if base.get(campo) is None and dup.get(campo) is not None:
                    base[campo] = dup[campo]
            for tabela, coluna in (
                ("doenca_cultura", "doenca_en"), ("doenca_pais", "doenca_en"), ("fungicida_overrides", "doenca"),
                ("fungicida_ordem", "doenca"), ("fungicida_registro_bloqueado", "doenca"),
            ):
                # OR IGNORE: se a canonica ja tiver uma linha com a mesma
                # chave (tipo/idx/etc), mantem a dela -- o DELETE logo
                # abaixo limpa o que sobrar da duplicata sem colidir.
                conn.execute(f"UPDATE OR IGNORE {tabela} SET {coluna} = ? WHERE {coluna} = ?", (canonico, sobra))
                conn.execute(f"DELETE FROM {tabela} WHERE {coluna} = ?", (sobra,))
            conn.execute("DELETE FROM disease_translations WHERE display_name_en = ?", (sobra,))
        conn.execute(
            """
            UPDATE disease_translations
            SET nome_cientifico = ?, germ_temp_min = ?, germ_temp_max = ?, germ_ur_min = ?, germ_molhamento_horas = ?
            WHERE display_name_en = ?
            """,
            (
                base.get("nome_cientifico"), base.get("germ_temp_min"), base.get("germ_temp_max"),
                base.get("germ_ur_min"), base.get("germ_molhamento_horas"), canonico,
            ),
        )
    conn.commit()
    conn.close()


def delete_disease_translation(display_name_en):
    """Apaga a doenca (linha em disease_translations) e toda config
    dependente (doenca_cultura, fungicida_overrides/ordem/registro_bloqueado)
    -- usado pra excluir manualmente uma entrada duplicada (mesmo fungo,
    nome de exibicao diferente) que o merge automatico (so' pega grafias
    identicas exceto maiusculas/minusculas, ver
    `merge_duplicate_disease_translations`) nao junta sozinho -- decisao
    do admin de que duas entradas sao a mesma doenca. Se o BioScout
    continuar reportando essa mesma display_name_en depois, ela volta a
    aparecer (linha nova, em branco) no proximo sync -- e' uma exclusao
    pontual, nao um bloqueio permanente."""
    conn = get_db()
    for tabela, coluna in (
        ("doenca_cultura", "doenca_en"), ("doenca_pais", "doenca_en"), ("fungicida_overrides", "doenca"),
        ("fungicida_ordem", "doenca"), ("fungicida_registro_bloqueado", "doenca"),
    ):
        conn.execute(f"DELETE FROM {tabela} WHERE {coluna} = ?", (display_name_en,))
    conn.execute("DELETE FROM disease_translations WHERE display_name_en = ?", (display_name_en,))
    conn.commit()
    conn.close()


def save_disease_translation(display_name_en, nome_pt, nome_cientifico=""):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO disease_translations (display_name_en, nome_pt, nome_cientifico) VALUES (?, ?, ?)
        ON CONFLICT(display_name_en) DO UPDATE SET nome_pt = excluded.nome_pt, nome_cientifico = excluded.nome_cientifico
        """,
        (display_name_en, nome_pt, nome_cientifico),
    )
    conn.commit()
    conn.close()


def save_disease_germ_limits(display_name_en, temp_min, temp_max, ur_min, molhamento_horas, agua_livre_inibe):
    """Limites numericos de germinacao -- UNICA fonte tanto da luz de risco
    (verde/amarelo/vermelho, ver `_calc_risco_germinacao` em app.py) quanto
    do texto exibido na aba Manejo (gerado a partir destes numeros, ver
    `_formatar_condicoes_germinacao`). A linha ja precisa existir (toda
    doenca conhecida tem uma, via `ensure_disease_translations`)."""
    conn = get_db()
    conn.execute(
        """
        UPDATE disease_translations
        SET germ_temp_min = ?, germ_temp_max = ?, germ_ur_min = ?, germ_molhamento_horas = ?, germ_agua_livre_inibe = ?
        WHERE display_name_en = ?
        """,
        (temp_min, temp_max, ur_min, molhamento_horas, 1 if agua_livre_inibe else 0, display_name_en),
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
    """Quando uma cultura nova e' cadastrada (matriz Doencas x Culturas, aba Doencas), bloqueia
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
            INSERT INTO farm_culturas (site_name, safra, cultura, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(site_name, safra) DO UPDATE SET cultura = excluded.cultura, updated_at = excluded.updated_at
            """,
            (site_name, safra, cultura, _agora_cuiaba()),
        )
    else:
        conn.execute("DELETE FROM farm_culturas WHERE site_name = ? AND safra = ?", (site_name, safra))
    conn.commit()
    conn.close()


def get_all_weather_station_overrides():
    """site_name -> {"codigo": ..., "country_code": ...} da estacao
    escolhida na aba Fazendas pra alimentar a previsao (Open-Meteo passa a
    usar a coordenada dessa estacao em vez da coordenada da propria
    fazenda) -- `country_code` diz qual catalogo o codigo pertence
    ('BR'=INMET, 'CL'=DMC, ver countries.py). So os sites com escolha
    manual aparecem aqui -- sem entrada, o padrao e' usar a coordenada da
    fazenda."""
    conn = get_db()
    rows = conn.execute("SELECT site_name, estacao_codigo, country_code FROM weather_station_overrides").fetchall()
    conn.close()
    return {r["site_name"]: {"codigo": r["estacao_codigo"], "country_code": r["country_code"]} for r in rows}


def set_weather_station_override(site_name, estacao_codigo, country_code="BR"):
    """estacao_codigo = "" (ou None) e' a escolha explicita "coordenada da
    propria fazenda" -- GRAVA a linha mesmo assim (com codigo vazio) em vez
    de apagar. Se apagasse a linha, `app._auto_detectar_estacoes_novas`
    (que so age em site "ainda sem nenhuma linha") ia enxergar esse site
    como "nunca decidido" no proximo request e escolher uma estacao
    sozinha de novo, desfazendo a escolha do cliente sem ele mexer em
    nada -- bug real ja visto em producao. Todo lugar que le esse dict
    (`get_all_weather_station_overrides`) ja trata codigo vazio como "sem
    estacao escolhida, usa coordenada propria" (`if escolha["codigo"]:
    ...`), entao manter a linha nao muda nenhum comportamento visivel."""
    conn = get_db()
    conn.execute(
        """
        INSERT INTO weather_station_overrides (site_name, estacao_codigo, country_code) VALUES (?, ?, ?)
        ON CONFLICT(site_name) DO UPDATE SET estacao_codigo = excluded.estacao_codigo, country_code = excluded.country_code
        """,
        (site_name, estacao_codigo or "", country_code),
    )
    conn.commit()
    conn.close()


def get_all_site_countries():
    """site_name -> codigo do pais (ISO 3166-1 alpha-2, ex. 'BR'/'CL')
    escolhido na aba Fazendas -- decide qual fronteira/estacoes aparecem
    pra essa fazenda no Mapa/Mapa Interpolado. So os sites com escolha
    manual aparecem aqui -- sem entrada, o padrao e' 'BR' (ver
    `countries.DEFAULT_COUNTRY`), preservando o comportamento de sempre
    pras fazendas ja cadastradas antes dessa coluna existir."""
    conn = get_db()
    rows = conn.execute("SELECT site_name, country_code FROM site_country_overrides").fetchall()
    conn.close()
    return {r["site_name"]: r["country_code"] for r in rows}


def get_site_country(site_name):
    return get_all_site_countries().get(site_name, "BR")


def set_site_country(site_name, country_code):
    """country_code = "" (ou None) remove a escolha manual -- volta pro
    padrao 'BR'."""
    conn = get_db()
    if country_code:
        conn.execute(
            """
            INSERT INTO site_country_overrides (site_name, country_code) VALUES (?, ?)
            ON CONFLICT(site_name) DO UPDATE SET country_code = excluded.country_code
            """,
            (site_name, country_code),
        )
    else:
        conn.execute("DELETE FROM site_country_overrides WHERE site_name = ?", (site_name,))
    conn.commit()
    conn.close()


def get_all_site_solos():
    """site_name -> {"classe":, "ordem":, "fonte":} (tipo de solo
    RESOLVIDO SOZINHO pela coordenada da fazenda -- Embrapa/PronaSolos
    ou, fora do Brasil, SoilGrids; ver `app._auto_detectar_solos_novos`
    e `soil_service`/`soilgrids_service`). Diferente do pais/estacao,
    NAO e' escolhido a mao -- e' um fato fisico do local. `classe` None
    significa "ja tentou resolver, mas nao ha' dado disponivel" (fora do
    Brasil sem SoilGrids acessivel, ou caiu em area de agua) -- ainda
    assim aparece aqui (com `classe` None), pra nao tentar de novo a
    cada request."""
    conn = get_db()
    rows = conn.execute("SELECT site_name, classe, ordem, fonte FROM site_solo_cache").fetchall()
    conn.close()
    return {r["site_name"]: {"classe": r["classe"], "ordem": r["ordem"], "fonte": r["fonte"]} for r in rows}


def set_site_solo(site_name, resultado):
    """`resultado` e' o dict devolvido por `soil_service.tipo_solo_embrapa`/
    `soilgrids_service.tipo_solo_soilgrids` (classe/ordem/fonte), ou None
    quando nenhuma das duas fontes achou dado pra essa coordenada --
    grava mesmo assim (com campos None), pra marcar como ja' tentado."""
    resultado = resultado or {}
    conn = get_db()
    conn.execute(
        """
        INSERT INTO site_solo_cache (site_name, classe, ordem, fonte, resolvido_em) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(site_name) DO UPDATE SET classe = excluded.classe, ordem = excluded.ordem,
            fonte = excluded.fonte, resolvido_em = excluded.resolvido_em
        """,
        (site_name, resultado.get("classe"), resultado.get("ordem"), resultado.get("fonte"), _agora_cuiaba()),
    )
    conn.commit()
    conn.close()


def get_all_site_display_names():
    """site_name -> nome de exibicao escolhido na aba Fazendas, pra
    fazenda REAL (vinda do BioScout) -- so' os sites com escolha manual
    aparecem aqui; sem entrada, quem le isso cai no nome derivado do
    proprio site_name (ver `app._nome_exibicao`). NAO se aplica a fazenda
    virtual/estimada -- essa ja tem nome proprio editavel desde sempre
    (`nome` em `virtual_farms`, ver `update_virtual_farm`); essa tabela e'
    so' o equivalente pra fazenda real, cujo site_name (chave de casamento
    com o CSV do BioScout) nunca pode mudar."""
    conn = get_db()
    rows = conn.execute("SELECT site_name, nome_exibicao FROM site_display_names").fetchall()
    conn.close()
    return {r["site_name"]: r["nome_exibicao"] for r in rows}


def set_site_display_name(site_name, nome_exibicao):
    """nome_exibicao = "" (ou None) remove a escolha manual -- volta a
    usar o nome derivado do site_name (ver `app._nome_exibicao`)."""
    nome_exibicao = (nome_exibicao or "").strip()
    conn = get_db()
    if nome_exibicao:
        conn.execute(
            """
            INSERT INTO site_display_names (site_name, nome_exibicao) VALUES (?, ?)
            ON CONFLICT(site_name) DO UPDATE SET nome_exibicao = excluded.nome_exibicao
            """,
            (site_name, nome_exibicao),
        )
    else:
        conn.execute("DELETE FROM site_display_names WHERE site_name = ?", (site_name,))
    conn.commit()
    conn.close()


def get_farm_ndvi_cars(site_name):
    """Lista (sem o KML, que pode ser grande) dos CAR anexados a essa
    fazenda, do mais antigo pro mais novo -- usada pra listar os CARs ja
    cadastrados na aba NDVI."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, nome, criado_em FROM farm_ndvi_car WHERE site_name = ? ORDER BY criado_em, id",
        (site_name,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_farm_ndvi_cars():
    """site_name -> lista de CARs (sem KML) -- usada pra montar a tela
    NDVI de todas as fazendas de uma vez sem carregar todo KML."""
    conn = get_db()
    rows = conn.execute(
        "SELECT site_name, id, nome, criado_em FROM farm_ndvi_car ORDER BY site_name, criado_em, id"
    ).fetchall()
    conn.close()
    por_site = {}
    for r in rows:
        por_site.setdefault(r["site_name"], []).append(
            {"id": r["id"], "nome": r["nome"], "criado_em": r["criado_em"]}
        )
    return por_site


def get_farm_ndvi_cars_kml(site_name):
    """KML de TODOS os CAR anexados a essa fazenda, na ordem em que
    foram anexados -- usado na hora de gerar o NDVI (o poligono de todos
    eles e' combinado num so', ver `ndvi_service`/`pre_visualizar_ndvi`
    em app.py)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT kml FROM farm_ndvi_car WHERE site_name = ? ORDER BY criado_em, id", (site_name,)
    ).fetchall()
    conn.close()
    return [r["kml"] for r in rows]


def add_farm_ndvi_car(site_name, nome, kml):
    """Anexa mais um CAR a fazenda -- NAO substitui os anteriores, cada
    CAR fica numa linha propria, removivel independente (ver
    `delete_farm_ndvi_car`)."""
    conn = get_db()
    conn.execute(
        "INSERT INTO farm_ndvi_car (site_name, nome, kml, criado_em) VALUES (?, ?, ?, ?)",
        (site_name, nome, kml, _agora_cuiaba()),
    )
    # O CAR anexado muda o contorno combinado da fazenda -- a ultima
    # imagem cacheada (se tiver) nao vale mais.
    conn.execute(
        "UPDATE farm_ndvi_area SET imagem = NULL, imagem_gerada_em = NULL WHERE site_name = ?", (site_name,)
    )
    conn.commit()
    conn.close()


def delete_farm_ndvi_car(car_id, site_name):
    """Remove um CAR especifico da fazenda (`site_name` filtrado pelo
    mesmo motivo de `get_farm_ndvi_historico_imagem` -- fecha o vinculo
    entre o id pedido e uma fazenda que o usuario tem acesso)."""
    conn = get_db()
    conn.execute("DELETE FROM farm_ndvi_car WHERE id = ? AND site_name = ?", (car_id, site_name))
    conn.execute(
        "UPDATE farm_ndvi_area SET imagem = NULL, imagem_gerada_em = NULL WHERE site_name = ?", (site_name,)
    )
    conn.commit()
    conn.close()


def save_farm_ndvi_image(site_name, imagem_bytes):
    conn = get_db()
    conn.execute(
        """
        INSERT INTO farm_ndvi_area (site_name, imagem, imagem_gerada_em) VALUES (?, ?, ?)
        ON CONFLICT(site_name) DO UPDATE SET imagem = excluded.imagem, imagem_gerada_em = excluded.imagem_gerada_em
        """,
        (site_name, imagem_bytes, _agora_cuiaba()),
    )
    conn.commit()
    conn.close()


def get_farm_ndvi_image(site_name):
    """Bytes do PNG gerado mais recente, ou None se nunca gerado."""
    conn = get_db()
    row = conn.execute("SELECT imagem FROM farm_ndvi_area WHERE site_name = ?", (site_name,)).fetchone()
    conn.close()
    return row["imagem"] if row and row["imagem"] else None


NDVI_HISTORICO_MAX_POR_FAZENDA = 8


def add_farm_ndvi_historico(site_name, data_alvo, imagem_bytes, cobertura_nuvens=None, thumbnail_bytes=None):
    """Guarda mais uma imagem no historico (nao sobrescreve as anteriores)
    -- cada "Gerar NDVI" vira uma entrada nova na galeria da aba NDVI,
    permitindo comparar datas diferentes em vez de so' ver a mais recente.
    `data_alvo` e' a data REAL da cena Sentinel-2 escolhida (a mais proxima
    da data pedida com cobertura de nuvens aceitavel), nao necessariamente
    a data que o usuario digitou. Guarda no maximo
    `NDVI_HISTORICO_MAX_POR_FAZENDA` imagens por fazenda -- a mais antiga e'
    apagada automaticamente quando esse limite e' ultrapassado, senao o
    banco (um arquivo unico) cresce sem parar conforme a galeria e' usada."""
    conn = get_db()
    conn.execute(
        "INSERT INTO farm_ndvi_historico (site_name, data_alvo, imagem, gerado_em, cobertura_nuvens, thumbnail) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (site_name, data_alvo, imagem_bytes, _agora_cuiaba(), cobertura_nuvens, thumbnail_bytes),
    )
    excedentes = conn.execute(
        "SELECT id FROM farm_ndvi_historico WHERE site_name = ? ORDER BY data_alvo DESC, id DESC "
        "LIMIT -1 OFFSET ?",
        (site_name, NDVI_HISTORICO_MAX_POR_FAZENDA),
    ).fetchall()
    if excedentes:
        conn.executemany(
            "DELETE FROM farm_ndvi_historico WHERE id = ?", [(r["id"],) for r in excedentes]
        )
    conn.commit()
    conn.close()


def ndvi_historico_tem_data(site_name, data_alvo):
    """True se ja existe uma entrada no historico dessa fazenda pra essa
    `data_alvo` exata -- `farm_ndvi_historico` nao tem constraint unica
    nesse par, entao quem gera automaticamente (ver
    `app._run_scheduled_ndvi_sends`) precisa checar isso na mao antes de
    inserir, senao duplica a MESMA cena todo ciclo em que nenhuma cena
    nova aparecer (comum, dado o revisit de ~5 dias do Sentinel-2)."""
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM farm_ndvi_historico WHERE site_name = ? AND data_alvo = ? LIMIT 1",
        (site_name, data_alvo),
    ).fetchone()
    conn.close()
    return row is not None


_NDVI_FREQUENCIA_DIAS = {"semanal": 7, "quinzenal": 14, "mensal": 30}


def get_all_ndvi_agendamentos():
    """{site_name: {"frequencia":, "proxima_execucao": date, "hora": int}}
    -- so' fazendas com agendamento automatico ativo aparecem aqui (ver
    `set_ndvi_agendamento`). `hora` e' individual por fazenda (pedido
    explicito do usuario, pra poder espalhar os envios ao longo do dia
    em vez de todos saindo no mesmo horario -- ver
    `app._run_scheduled_ndvi_sends`)."""
    conn = get_db()
    rows = conn.execute("SELECT site_name, frequencia, proxima_execucao, hora FROM ndvi_agendamento").fetchall()
    conn.close()
    return {
        r["site_name"]: {
            "frequencia": r["frequencia"],
            "proxima_execucao": datetime.strptime(r["proxima_execucao"], "%Y-%m-%d").date(),
            "hora": r["hora"],
        }
        for r in rows
    }


def set_ndvi_agendamento(site_name, data_inicio, frequencia, hora=None):
    """Ativa (ou substitui) o agendamento automatico de NDVI dessa
    fazenda -- `data_inicio` (date) vira a primeira `proxima_execucao`.
    `frequencia` vazio/None APAGA a linha (desativa o agendamento),
    mesma semantica de `set_weather_station_override`. `hora` (0-23,
    individual por fazenda) usa `get_whatsapp_send_hour()` como default
    quando nao informado (mesmo valor que ja preenche o campo na tela)."""
    conn = get_db()
    if not frequencia:
        conn.execute("DELETE FROM ndvi_agendamento WHERE site_name = ?", (site_name,))
    else:
        if hora is None:
            hora = get_whatsapp_send_hour()
        conn.execute(
            """
            INSERT INTO ndvi_agendamento (site_name, frequencia, proxima_execucao, hora) VALUES (?, ?, ?, ?)
            ON CONFLICT(site_name) DO UPDATE SET frequencia = excluded.frequencia,
                proxima_execucao = excluded.proxima_execucao, hora = excluded.hora
            """,
            (site_name, frequencia, data_inicio.isoformat(), hora),
        )
    conn.commit()
    conn.close()


def avancar_ndvi_agendamento(site_name, a_partir_de):
    """Empurra `proxima_execucao` pra frente a partir de `a_partir_de`
    (a data REAL da tentativa, hoje -- nao a data agendada anterior),
    pelo intervalo da frequencia dessa fazenda. Ancorar em "hoje" (em vez
    de acumular a partir do ciclo perdido) evita uma rajada de tentativas
    em atraso se o servidor ficar fora do ar por varios dias -- so'
    retoma o ciclo normal a partir de quando rodar de novo. Nao faz nada
    se a fazenda nao tiver agendamento ativo (ja foi desativado no meio
    do processamento, por exemplo)."""
    conn = get_db()
    row = conn.execute("SELECT frequencia FROM ndvi_agendamento WHERE site_name = ?", (site_name,)).fetchone()
    if row:
        dias = _NDVI_FREQUENCIA_DIAS.get(row["frequencia"], 7)
        proxima = a_partir_de + timedelta(days=dias)
        conn.execute(
            "UPDATE ndvi_agendamento SET proxima_execucao = ? WHERE site_name = ?",
            (proxima.isoformat(), site_name),
        )
        conn.commit()
    conn.close()


def get_ndvi_ativos(site_name):
    """Telefones marcados como "ativos" pra NDVI dessa fazenda -- usado
    tanto pra pre-marcar a caixa no envio manual (aba NDVI) quanto pra
    decidir quem recebe o envio automatico agendado (ver
    `app._run_scheduled_ndvi_sends`). Vazio por padrao (nenhuma linha
    ainda) -- comeca desmarcado ate' o usuario marcar e salvar."""
    conn = get_db()
    rows = conn.execute("SELECT telefone FROM ndvi_whatsapp_ativos WHERE site_name = ?", (site_name,)).fetchall()
    conn.close()
    return {r["telefone"] for r in rows}


def set_ndvi_ativo(site_name, telefone, ativo):
    conn = get_db()
    if ativo:
        conn.execute(
            "INSERT OR IGNORE INTO ndvi_whatsapp_ativos (site_name, telefone) VALUES (?, ?)",
            (site_name, telefone),
        )
    else:
        conn.execute(
            "DELETE FROM ndvi_whatsapp_ativos WHERE site_name = ? AND telefone = ?", (site_name, telefone)
        )
    conn.commit()
    conn.close()


def get_farm_ndvi_historico(site_name):
    """Lista (sem os bytes da imagem, que pode ser grande) ordenada da mais
    recente pra mais antiga -- usada pra montar a galeria de miniaturas."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, data_alvo, gerado_em, cobertura_nuvens FROM farm_ndvi_historico "
        "WHERE site_name = ? ORDER BY data_alvo DESC, id DESC",
        (site_name,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_farm_ndvi_historico_item(historico_id, site_name):
    """Metadado (sem os bytes da imagem) de UMA entrada do historico --
    usado na pagina de visualizacao com zoom, pra mostrar data/nuvens."""
    conn = get_db()
    row = conn.execute(
        "SELECT id, data_alvo, gerado_em, cobertura_nuvens FROM farm_ndvi_historico WHERE id = ? AND site_name = ?",
        (historico_id, site_name),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_farm_ndvi_historico_imagem(historico_id, site_name):
    """`site_name` tambem filtrado (alem do id) pra garantir que a imagem
    pedida realmente pertence a uma fazenda que o usuario tem acesso --
    a rota ja' checa a permissao pro `site_name`, isso so' fecha o vinculo
    entre o id pedido e esse site."""
    conn = get_db()
    row = conn.execute(
        "SELECT imagem FROM farm_ndvi_historico WHERE id = ? AND site_name = ?", (historico_id, site_name)
    ).fetchone()
    conn.close()
    return row["imagem"] if row else None


def get_farm_ndvi_historico_thumbnail(historico_id, site_name):
    """Miniatura pra galeria (ver `ndvi_service.gerar_thumbnail`), como
    (bytes, mimetype) -- cai pra imagem cheia (PNG) se for um item salvo
    antes dessa coluna existir."""
    conn = get_db()
    row = conn.execute(
        "SELECT thumbnail, imagem FROM farm_ndvi_historico WHERE id = ? AND site_name = ?",
        (historico_id, site_name),
    ).fetchone()
    conn.close()
    if not row:
        return None, None
    if row["thumbnail"]:
        return row["thumbnail"], "image/jpeg"
    return row["imagem"], "image/png"


def delete_farm_ndvi_historico_item(historico_id, site_name):
    """Remove uma imagem especifica da galeria (`site_name` filtrado pelo
    mesmo motivo de `get_farm_ndvi_historico_imagem`)."""
    conn = get_db()
    conn.execute("DELETE FROM farm_ndvi_historico WHERE id = ? AND site_name = ?", (historico_id, site_name))
    conn.commit()
    conn.close()


def delete_farm_ndvi_historico_de_site(site_name):
    conn = get_db()
    conn.execute("DELETE FROM farm_ndvi_historico WHERE site_name = ?", (site_name,))
    conn.commit()
    conn.close()


def get_culturas():
    """Lista de 12 nomes na ordem dos slots (com "" nos ainda nao
    preenchidos) -- editavel direto no cabecalho da matriz Doencas x
    Culturas (aba Doencas)."""
    conn = get_db()
    rows = conn.execute("SELECT slot, nome FROM culturas ORDER BY slot").fetchall()
    conn.close()
    return [r["nome"] or "" for r in rows]


def get_culturas_ativas():
    """So os nomes ja preenchidos -- usado nos seletores de cultura atual
    por fazenda (Fazendas/Recomendacoes) e na matriz de doencas."""
    return [c for c in get_culturas() if c]


def set_culturas(nomes):
    """Substitui os 12 nomes (na ordem dos slots) -- editado direto no
    cabecalho da matriz Doencas x Culturas (aba Doencas), no lugar da
    antiga aba separada "Nome Culturas". Quando um slot que JA tinha nome
    recebe um nome DIFERENTE (renomear -- corrigir digitacao, por
    exemplo -- e nao um slot vazio virando novo), migra as referencias
    por NOME nas tabelas dependentes (`doenca_cultura`, marcacao da
    matriz; `fungicida_registro_bloqueado`, registro por cultura na
    Biblioteca de Fungicidas; `farm_culturas`, "Cultura atual" escolhida
    por fazenda) do nome antigo pro novo -- essas tabelas guardam a
    cultura pelo NOME, nao pelo slot, entao sem essa migracao um simples
    renomeio perderia toda marcacao/registro ja feito pra aquela cultura.
    Se o novo nome ja' e' usado por OUTRO slot (colisao rara, tipo
    digitar errado o nome de uma cultura ja existente), a marcacao antiga
    e' descartada em vez de duplicar (`UPDATE OR IGNORE` + `DELETE` da
    sobra, mesmo padrao de `merge_duplicate_disease_translations`).
    Devolve a lista dos nomes GENUINAMENTE novos (slot que estava vazio
    antes) -- um renomeio nao conta como novo, ja que o registro por
    quimico dele ja existia e foi migrado junto; o chamador usa essa
    lista pra decidir quem precisa do bloqueio de seguranca em todo
    quimico (`bloquear_cultura_nova_em_todos_quimicos`)."""
    antigos = get_culturas()
    genuinamente_novos = []
    conn = get_db()
    for slot, nome in enumerate(nomes[:12]):
        nome = (nome or "").strip()
        antigo = antigos[slot] if slot < len(antigos) else ""
        if not antigo and nome:
            genuinamente_novos.append(nome)
        elif antigo and nome and antigo != nome:
            for tabela in ("doenca_cultura", "fungicida_registro_bloqueado"):
                conn.execute(f"UPDATE OR IGNORE {tabela} SET cultura = ? WHERE cultura = ?", (nome, antigo))
                conn.execute(f"DELETE FROM {tabela} WHERE cultura = ?", (antigo,))
            conn.execute("UPDATE farm_culturas SET cultura = ? WHERE cultura = ?", (nome, antigo))
        conn.execute(
            """
            INSERT INTO culturas (slot, nome) VALUES (?, ?)
            ON CONFLICT(slot) DO UPDATE SET nome = excluded.nome
            """,
            (slot, nome),
        )
    conn.commit()
    conn.close()
    return genuinamente_novos


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


def get_doenca_paises():
    """chave: doenca_en -> set(nome do pais) -- matriz doenca x pais (aba
    Doencas), mesmo espirito de `get_doenca_culturas`: doenca ausente ou com
    set vazio nao e' filtrada por nenhum pais (sempre aparece na aba
    Graficos, mesmo com um pais especifico selecionado) -- usado pra
    melhorar a busca de produtos com registro por regiao sem esconder por
    engano uma doenca ainda nao classificada por pais. A coluna
    `country_code` guarda o NOME do pais (texto livre, ver
    `get_paises_doenca_slots`/`set_paises_doenca_slots`), nao mais o
    codigo ISO -- nome mantido por compatibilidade com dados ja salvos
    antes dessa mudanca."""
    conn = get_db()
    rows = conn.execute("SELECT doenca_en, country_code FROM doenca_pais").fetchall()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault(r["doenca_en"], set()).add(r["country_code"])
    return result


def set_doenca_paises(doenca_en, paises):
    """Substitui o conjunto de paises (por NOME, ver `get_doenca_paises`)
    marcados para aquela doenca."""
    conn = get_db()
    conn.execute("DELETE FROM doenca_pais WHERE doenca_en = ?", (doenca_en,))
    for nome_pais in paises:
        conn.execute("INSERT INTO doenca_pais (doenca_en, country_code) VALUES (?, ?)", (doenca_en, nome_pais))
    conn.commit()
    conn.close()


def get_paises_doenca_slots():
    """Lista de 12 nomes de pais na ordem dos slots (com "" nos ainda nao
    preenchidos) -- editavel direto no cabecalho da matriz Doencas x Pais
    (aba Doencas), mesmo padrao de `get_culturas`. Nasce com os 12 paises
    de `countries.py` (ver seed em `init_db`), mas e' texto livre daqui
    em diante -- pode virar qualquer nome (inclusive um pais fora da
    America do Sul, fora do registro `countries.py`, se um dia a OneAgro
    vender pra outro continente), sem depender de fronteira/estacao de
    verdade cadastrada."""
    conn = get_db()
    rows = conn.execute("SELECT slot, nome FROM paises_doenca_slots ORDER BY slot").fetchall()
    conn.close()
    return [r["nome"] or "" for r in rows]


def set_paises_doenca_slots(nomes):
    """Substitui os 12 nomes (na ordem dos slots) -- mesma migracao de
    `set_culturas` quando e' so' um renomeio (slot que ja tinha nome
    recebe um nome diferente): atualiza `doenca_pais` do nome antigo pro
    novo em vez de perder a marcacao ja feita. So' essa tabela depende do
    nome de pais (ao contrario de cultura, pais nao tem registro de
    fungicida nem "Cultura atual" de fazenda amarrado a ele)."""
    antigos = get_paises_doenca_slots()
    conn = get_db()
    for slot, nome in enumerate(nomes[:12]):
        nome = (nome or "").strip()
        antigo = antigos[slot] if slot < len(antigos) else ""
        if antigo and nome and antigo != nome:
            conn.execute("UPDATE OR IGNORE doenca_pais SET country_code = ? WHERE country_code = ?", (nome, antigo))
            conn.execute("DELETE FROM doenca_pais WHERE country_code = ?", (antigo,))
        conn.execute(
            """
            INSERT INTO paises_doenca_slots (slot, nome) VALUES (?, ?)
            ON CONFLICT(slot) DO UPDATE SET nome = excluded.nome
            """,
            (slot, nome),
        )
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
        VALUES (?, ?, ?, ?)
        ON CONFLICT(site_name, doenca) DO UPDATE SET nota = excluded.nota, updated_at = excluded.updated_at
        """,
        (site_name, doenca, nota, _agora_cuiaba()),
    )
    conn.commit()
    conn.close()


def get_all_site_climate_notes():
    """{site_name: nota} -- anotacao livre por ponto (hoje so' usada pelos
    pontos "so clima" da aba Alertas Clima, ver `app.alertas_clima`),
    replicada no relatorio de texto/PDF desse ponto. So' entra no dict
    quem tem nota preenchida (string vazia == sem entrada)."""
    conn = get_db()
    rows = conn.execute("SELECT site_name, nota FROM site_climate_notes").fetchall()
    conn.close()
    return {r["site_name"]: r["nota"] for r in rows if r["nota"]}


def get_site_climate_note(site_name):
    return get_all_site_climate_notes().get(site_name, "")


def set_site_climate_note(site_name, nota):
    """Substitui a anotacao do ponto -- nota vazia apaga a linha (mesma
    semantica de `set_weather_station_override` pro valor "vazio")."""
    nota = (nota or "").strip()
    conn = get_db()
    if nota:
        conn.execute(
            """
            INSERT INTO site_climate_notes (site_name, nota, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(site_name) DO UPDATE SET nota = excluded.nota, updated_at = excluded.updated_at
            """,
            (site_name, nota, _agora_cuiaba()),
        )
    else:
        conn.execute("DELETE FROM site_climate_notes WHERE site_name = ?", (site_name,))
    conn.commit()
    conn.close()


def sync_sites(site_names):
    """Garante que a tabela sites tenha uma linha para cada fazenda do CSV.
    Retorna os nomes que eram novos (ainda nao estavam na tabela) -- usado
    pra aplicar o agendamento padrao de WhatsApp so' nessas, sem
    sobrescrever fazenda ja conhecida (ver `seed_default_whatsapp_schedule`,
    chamado pelo `@app.before_request` em app.py com o retorno daqui)."""
    conn = get_db()
    existentes = {r["site_name"] for r in conn.execute("SELECT site_name FROM sites")}
    novos = [name for name in site_names if name not in existentes]
    for name in site_names:
        conn.execute(
            "INSERT OR IGNORE INTO sites (site_name) VALUES (?)", (name,)
        )
    conn.commit()
    conn.close()
    return novos


# Agenda padrao de envio automatico de WhatsApp pra fazenda nova (real,
# importada do BioScout, ou virtual/estimada) -- Texto Seg/Qua/Sex, PDF so'
# Sex, definido pelo usuario como o padrao adotado a partir de 28/08/2026.
WHATSAPP_DIAS_PADRAO_TEXTO = {0, 2, 4}
WHATSAPP_DIAS_PADRAO_PDF = {4}


def seed_default_whatsapp_schedule(site_name):
    """Aplica a agenda padrao acima pra uma fazenda -- so' deve ser chamada
    na primeira vez que ela aparece (fazenda nova), nunca pra sobrescrever
    uma que a pessoa ja configurou diferente."""
    set_whatsapp_days(site_name, WHATSAPP_DIAS_PADRAO_TEXTO)
    set_whatsapp_days_pdf(site_name, WHATSAPP_DIAS_PADRAO_PDF)


def get_all_sites():
    conn = get_db()
    rows = conn.execute("SELECT id, site_name FROM sites ORDER BY site_name").fetchall()
    conn.close()
    return rows


def get_all_virtual_farms():
    """Lista de fazendas virtuais/estimadas (ver `virtual_farms.py`) --
    cada uma vira `{"site_name", "nome", "lat", "lon", "raio_km",
    "criado_em", "criado_por", "tipo"}` ('doenca' interpola concentracao
    via IDW, como sempre; 'clima' e' so' referencia de clima pro
    cliente)."""
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


# Tabelas que guardam dado POR site_name de fazenda virtual -- usado
# tanto por `update_virtual_farm` (rename) quanto pela migracao de nome
# dos pontos "so clima" em `init_db` (ver `_virtual_farm_site_name`).
_VIRTUAL_FARM_RENAME_TABLES = (
    "sites", "recommendation_notes", "whatsapp_schedule", "whatsapp_schedule_pdf",
    "farm_produtos", "farm_plantio", "farm_aplicacoes", "farm_espacamento_plantio",
    "farm_culturas", "weather_station_overrides", "farm_ndvi_area", "farm_ndvi_car", "farm_ndvi_historico",
    "site_country_overrides", "site_climate_notes", "ndvi_agendamento", "ndvi_whatsapp_ativos",
    "whatsapp_schedule_horarios", "site_solo_cache",
)


def _virtual_farm_site_name(nome, tipo):
    """site_name (chave primaria em varias tabelas) de uma fazenda
    virtual/estimada, a partir do nome digitado e do tipo. 'doenca'
    (padrao) vira '"{nome}" - OneAgro' -- parecido com o das fazendas
    reais ("OneAgro - X"), mas na ordem invertida e entre aspas, pra dar
    pra notar de relance que foi criada aqui dentro, nao importada do
    BioScout. 'clima' (ponto so' de referencia de clima, sem doenca) vira
    '{nome} - Clima - OneAgro' -- SEM aspas, com "Clima" no meio -- padrao
    proprio pedido pelo usuario, pra distinguir de relance E pra poder
    filtrar esses pontos fora da aba Fazendas (ver `fazendas()`, que so'
    lista fazenda real + virtual tipo 'doenca')."""
    nome = nome.strip().replace('"', "")
    if tipo == "clima":
        return f"{nome} - Clima - OneAgro"
    return f'"{nome}" - OneAgro'


def create_virtual_farm(nome, lat, lon, raio_km, criado_por=None, country_code="BR", tipo="doenca"):
    """Cria uma fazenda virtual/estimada -- `site_name` vem de
    `_virtual_farm_site_name` (formato depende de `tipo`: 'doenca'
    interpola concentracao via IDW, 'clima' e' so' referencia de clima
    pro cliente, ver `get_all_virtual_farms`). Levanta
    sqlite3.IntegrityError se ja existir uma fazenda com esse nome+tipo
    (nome precisa ser unico dentro do mesmo site_name resultante).
    Tambem registra o site_name na tabela `sites`, pra poder aparecer na
    tela de permissoes igual uma fazenda de verdade, e o pais em
    `site_country_overrides` (mesma tabela usada pela fazenda real, aba
    Fazendas > Pais). Retorna o site_name criado."""
    nome = nome.strip().replace('"', "")
    site_name = _virtual_farm_site_name(nome, tipo)
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO virtual_farms (site_name, nome, lat, lon, raio_km, criado_em, criado_por, tipo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (site_name, nome, lat, lon, raio_km, _agora_cuiaba(), criado_por, tipo),
        )
        conn.execute("INSERT OR IGNORE INTO sites (site_name) VALUES (?)", (site_name,))
        conn.commit()
    finally:
        conn.close()
    seed_default_whatsapp_schedule(site_name)
    set_site_country(site_name, country_code)
    return site_name


def update_virtual_farm(site_name, nome, lat, lon, raio_km, country_code="BR", tipo="doenca"):
    """Atualiza nome/coordenada/raio/pais/tipo de uma fazenda virtual/estimada.
    Se o nome OU o tipo mudar, o site_name muda junto (mesmo padrao de
    `create_virtual_farm`, ver `_virtual_farm_site_name`) -- nesse caso
    propaga o novo site_name pra todas as tabelas que guardam dado por
    fazenda (sites, anotacoes, agenda de WhatsApp, produtos, plantio,
    aplicacoes, cultura), pra nao perder o que ja tinha sido cadastrado
    pra ela. Levanta sqlite3.IntegrityError se o novo nome ja for de
    outra fazenda virtual. Retorna o site_name final (igual ao antigo se
    nome e tipo nao mudaram)."""
    nome = nome.strip().replace('"', "")
    novo_site_name = _virtual_farm_site_name(nome, tipo)
    conn = get_db()
    if novo_site_name != site_name:
        ja_existe = conn.execute(
            "SELECT 1 FROM virtual_farms WHERE site_name = ?", (novo_site_name,)
        ).fetchone()
        if ja_existe:
            conn.close()
            raise sqlite3.IntegrityError(f"Ja existe uma fazenda virtual chamada '{nome}'")
    conn.execute(
        "UPDATE virtual_farms SET site_name=?, nome=?, lat=?, lon=?, raio_km=?, tipo=? WHERE site_name=?",
        (novo_site_name, nome, lat, lon, raio_km, tipo, site_name),
    )
    if novo_site_name != site_name:
        for tabela in _VIRTUAL_FARM_RENAME_TABLES:
            conn.execute(f"UPDATE {tabela} SET site_name=? WHERE site_name=?", (novo_site_name, site_name))
    conn.commit()
    conn.close()
    set_site_country(novo_site_name, country_code)
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
    conn.execute("DELETE FROM whatsapp_schedule_pdf WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM farm_produtos WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM farm_plantio WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM farm_aplicacoes WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM farm_espacamento_plantio WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM farm_culturas WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM weather_station_overrides WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM farm_ndvi_area WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM farm_ndvi_car WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM farm_ndvi_historico WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM site_country_overrides WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM site_climate_notes WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM ndvi_agendamento WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM ndvi_whatsapp_ativos WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM whatsapp_schedule_horarios WHERE site_name = ?", (site_name,))
    conn.execute("DELETE FROM site_solo_cache WHERE site_name = ?", (site_name,))
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
    """Admin primeiro (entre eles, ordem alfabetica), depois os demais
    (tambem em ordem alfabetica) -- pedido explicito do usuario, pra
    quem administra o site aparecer sempre no topo da aba Usuarios."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, username, is_admin, email, telefone, whatsapp_pausado FROM users ORDER BY is_admin DESC, username COLLATE NOCASE"
    ).fetchall()
    conn.close()
    return rows


DEFAULT_PASSWORD = "Oneagro01!"  # senha de todo usuario novo, e pra onde o botao "Redefinir senha" volta


def create_user(username, password, is_admin=False, email="", telefone=""):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, email, telefone) VALUES (?, ?, ?, ?, ?)",
            (username, generate_password_hash(password), 1 if is_admin else 0, email, telefone),
        )
        conn.commit()
    finally:
        conn.close()


def get_site_whatsapp_recipients(site_name):
    """Todo usuario (e subordinado) que deve receber os relatorios de
    WhatsApp daquela fazenda -- uma fazenda pode ter varios numeros, a
    lista e' gerada a partir de dois cadastros: usuario (so entra quem
    tiver aquela fazenda marcada na coluna "Receber relatorios", aba
    Usuarios -- `user_report_permissions`, uma escolha independente do
    acesso normal a fazenda) e subordinado (contato leve ligado a um
    usuario "dono", aba Usuarios > Subordinados --
    `subordinado_report_permissions`). Em ambos os casos so entra quem ja
    tenha telefone cadastrado E nao esteja com `whatsapp_pausado` ativo
    (pedido explicito do usuario -- pausa TODAS as fazendas de uma vez,
    sem apagar as marcacoes individuais de "Receber relatorios", ver
    `set_user_whatsapp_pausado`/`set_subordinado_whatsapp_pausado`). Nao
    depende de ser admin nem de ter acesso pra VER a fazenda -- sao
    coisas separadas de proposito."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT username, telefone FROM (
            SELECT DISTINCT u.username AS username, u.telefone AS telefone
            FROM users u
            JOIN user_report_permissions r ON r.user_id = u.id
            JOIN sites s ON s.id = r.site_id
            WHERE s.site_name = ?
              AND u.telefone IS NOT NULL AND u.telefone != ''
              AND u.whatsapp_pausado = 0
            UNION
            SELECT DISTINCT sub.nome AS username, sub.telefone AS telefone
            FROM subordinados sub
            JOIN subordinado_report_permissions sr ON sr.subordinado_id = sub.id
            JOIN sites s ON s.id = sr.site_id
            WHERE s.site_name = ?
              AND sub.telefone IS NOT NULL AND sub.telefone != ''
              AND sub.whatsapp_pausado = 0
        )
        ORDER BY username
        """,
        (site_name, site_name),
    ).fetchall()
    conn.close()
    return rows


def get_all_sites_whatsapp_recipients():
    """Versao em lote de `get_site_whatsapp_recipients`, pra telas que
    precisam do destinatario de TODAS as fazendas (Recomendacoes, relatorio
    admin de WhatsApp) -- uma unica consulta em vez de uma por fazenda.
    Retorna {site_name: [sqlite3.Row(username, telefone), ...]}."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT site_name, username, telefone FROM (
            SELECT DISTINCT s.site_name AS site_name, u.username AS username, u.telefone AS telefone
            FROM users u
            JOIN user_report_permissions r ON r.user_id = u.id
            JOIN sites s ON s.id = r.site_id
            WHERE u.telefone IS NOT NULL AND u.telefone != '' AND u.whatsapp_pausado = 0
            UNION
            SELECT DISTINCT s.site_name AS site_name, sub.nome AS username, sub.telefone AS telefone
            FROM subordinados sub
            JOIN subordinado_report_permissions sr ON sr.subordinado_id = sub.id
            JOIN sites s ON s.id = sr.site_id
            WHERE sub.telefone IS NOT NULL AND sub.telefone != '' AND sub.whatsapp_pausado = 0
        )
        ORDER BY site_name, username
        """
    ).fetchall()
    conn.close()
    por_site = {}
    for r in rows:
        por_site.setdefault(r["site_name"], []).append(r)
    return por_site


def delete_user(user_id):
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def set_user_whatsapp_pausado(user_id, pausado):
    """Pausa/retoma o recebimento de WhatsApp desse usuario em TODAS as
    fazendas de uma vez (`get_site_whatsapp_recipients` passa a ignorar
    ele enquanto pausado) -- sem apagar as marcacoes individuais de
    "Receber relatorios" (`user_report_permissions`), que continuam
    intactas pra' quando ele voltar a receber."""
    conn = get_db()
    conn.execute("UPDATE users SET whatsapp_pausado = ? WHERE id = ?", (1 if pausado else 0, user_id))
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


def set_user_temp_password(user_id, senha_temporaria, validade_minutos=30):
    """Senha de "esqueci a senha" (aba de login > Esqueci minha senha) --
    guardada em colunas SEPARADAS de `password_hash` de proposito: a
    senha de verdade do usuario continua valendo normalmente ate' essa
    temporaria ser de fato usada pra logar (`app.login`, que so' cai pra
    comparar com ela se a senha normal nao bater) ou expirar sozinha.
    Isso evita que pedir "esqueci a senha" pro username de outra pessoa
    derrube o acesso dela na hora -- so' manda uma mensagem de WhatsApp
    indesejada, que ela pode ignorar."""
    conn = get_db()
    expira_em = (datetime.now() + timedelta(minutes=validade_minutos)).isoformat()
    conn.execute(
        "UPDATE users SET temp_password_hash = ?, temp_password_expires_at = ? WHERE id = ?",
        (generate_password_hash(senha_temporaria), expira_em, user_id),
    )
    conn.commit()
    conn.close()


def clear_user_temp_password(user_id):
    """Apaga a senha temporaria -- chamado assim que ela e' usada pra
    logar com sucesso (`app.login`), garantindo uso unico (nao da' pra
    logar duas vezes com a mesma temporaria)."""
    conn = get_db()
    conn.execute(
        "UPDATE users SET temp_password_hash = NULL, temp_password_expires_at = NULL WHERE id = ?",
        (user_id,),
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


def get_owner_subordinados(owner_user_id):
    """Lista de subordinados de um usuario (aba Usuarios > Subordinados) --
    cada um e' so um contato leve (nome + telefone), sem login/senha, que
    recebe relatorio de WhatsApp de um subconjunto das fazendas que o
    proprio "dono" (owner_user_id) ja recebe -- pensado pra dividir uma
    equipe de campo entre varias fazendas do mesmo proprietario. Cada
    dict tem {id, nome, telefone, site_ids, whatsapp_pausado}."""
    conn = get_db()
    subs = conn.execute(
        "SELECT id, nome, telefone, whatsapp_pausado FROM subordinados WHERE owner_user_id = ? ORDER BY nome", (owner_user_id,)
    ).fetchall()
    resultado = []
    for s in subs:
        site_ids = {
            r["site_id"] for r in conn.execute(
                "SELECT site_id FROM subordinado_report_permissions WHERE subordinado_id = ?", (s["id"],)
            )
        }
        resultado.append({
            "id": s["id"], "nome": s["nome"], "telefone": s["telefone"], "site_ids": site_ids,
            "whatsapp_pausado": bool(s["whatsapp_pausado"]),
        })
    conn.close()
    return resultado


def create_subordinado(owner_user_id, nome, telefone):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO subordinados (owner_user_id, nome, telefone) VALUES (?, ?, ?)",
        (owner_user_id, nome, telefone),
    )
    conn.commit()
    novo_id = cur.lastrowid
    conn.close()
    return novo_id


def update_subordinado(subordinado_id, nome, telefone):
    conn = get_db()
    conn.execute(
        "UPDATE subordinados SET nome = ?, telefone = ? WHERE id = ?", (nome, telefone, subordinado_id)
    )
    conn.commit()
    conn.close()


def delete_subordinado(subordinado_id):
    conn = get_db()
    conn.execute("DELETE FROM subordinados WHERE id = ?", (subordinado_id,))
    conn.commit()
    conn.close()


def set_subordinado_whatsapp_pausado(subordinado_id, pausado):
    """Mesmo padrao de `set_user_whatsapp_pausado`, pra' subordinado --
    pausa em todas as fazendas dele de uma vez, sem apagar
    `subordinado_report_permissions`."""
    conn = get_db()
    conn.execute(
        "UPDATE subordinados SET whatsapp_pausado = ? WHERE id = ?", (1 if pausado else 0, subordinado_id)
    )
    conn.commit()
    conn.close()


def set_subordinado_report_sites(subordinado_id, site_ids):
    """Substitui as fazendas marcadas pra esse subordinado receber
    relatorio -- mesmo padrao de `set_user_report_permissions`, so' que
    pra tabela de subordinados."""
    conn = get_db()
    conn.execute("DELETE FROM subordinado_report_permissions WHERE subordinado_id = ?", (subordinado_id,))
    for site_id in site_ids:
        conn.execute(
            "INSERT INTO subordinado_report_permissions (subordinado_id, site_id) VALUES (?, ?)",
            (subordinado_id, site_id),
        )
    conn.commit()
    conn.close()
