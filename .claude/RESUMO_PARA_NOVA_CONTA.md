# Contexto do projeto OneAgro Monitor — resumo para retomar em outra conta Claude

Cole esta mensagem inteira como a primeira mensagem numa conversa nova, dentro do
diretorio de trabalho `C:\Users\AlanMauro\OneDrive\OneAgro\BioScoutMonitor\webapp`,
para o Claude retomar com o contexto essencial. (Atualizado em 2026-09-21.)

## Quem sou / projeto

Sou Alan Mauro (alan.mauro@biotrop.com.br), mantenho sozinho o app Flask
"OneAgro Monitor" (monitoramento de esporos/clima/doencas em fazendas).

- Repo: github.com/alanmauro-oneagro/monitoresporos
- Deploy: Railway, auto-deploy a partir da branch `main`
- URL producao: https://monitoresporos-production.up.railway.app
- Diretorio de trabalho: `C:\Users\AlanMauro\OneDrive\OneAgro\BioScoutMonitor\webapp`
- Este arquivo fica em `..\.claude\RESUMO_PARA_NOVA_CONTA.md` (fora do `webapp`,
  na raiz do repo). Ha' tambem um `CLAUDE.md` na raiz do repo e uma pasta
  `.claude\memory\` com memorias especificas (ex. regra do admin unico,
  escalonamento de WhatsApp) — tudo isso e' lido automaticamente pelo Claude
  Code quando a sessao abre nesta pasta, independente da conta usada.
- **Sem acesso direto ao banco de producao/Railway nesta maquina** — qualquer
  verificacao e' feita rodando o servidor local (`preview_start`/Flask dev) com
  dados de teste descartaveis, nunca em producao.

## Regra de negocio permanente (importante)

**Somente o usuario "Alan Mauro" (esta conta) deve poder ajustar
configuracoes gerais do site.** Varias rotas/relatorios admin sao
propositalmente restritos a `current_user.username == 'Alan Mauro'`
especificamente, NAO apenas `current_user.is_admin` — ex.: painel "Conexao
WhatsApp" (antigo "WhatsApp" no dropdown de Configuracoes), relatorio de
envios do WhatsApp, relatorio de fungicidas, aba "Alertas Clima". Isso e
deliberado: outros usuarios marcados como admin NAO devem ver essas telas. Ao
criar novas rotas/relatorios/configuracoes gerais do site, seguir esse mesmo
padrao de gate por username, a menos que eu peca explicitamente paridade
para outros admins.

## Arquitetura / conceitos tecnicos principais

- Flask + Jinja2 + Bootstrap + Leaflet 1.9.4, SQLite (`models.get_db()`,
  modo WAL), openpyxl para exportacao Excel, `data_reader.py` para dados de
  clima/esporos via CSV (`data/sites.csv`, `data/spore_counts.csv`,
  `data/weather.csv`), `countries.py` para provedores de estacao por pais
  (Brasil/INMET + Argentina/Chile/Peru/Uruguai/Paraguai/Bolivia, cada um com
  seu proprio provedor oficial).
- **Fazenda REAL vem sincronizada de `data/sites.csv`**: `_ensure_sites_synced()`
  (`@app.before_request`) chama `models.sync_sites(read_sites())` a cada
  requisicao, que so' ADICIONA linha nova na tabela `sites` — NUNCA remove.
  Ou seja, uma fazenda que aparece no CSV sempre vai reaparecer sozinha,
  mesmo que a linha seja apagada do banco na mao. Ver "Fazenda
  ativa/desativada" abaixo pra' como esconder uma fazenda sem depender de
  DELETE.
- **3 mecanismos DIFERENTES de "listar fazendas" no codigo hoje** (nenhum
  choke point unico): (1) CSV cru (`data_reader.read_sites()`/
  `get_dashboard_data(None,...)`/`read_site_coordinates()`), usado pro ADMIN
  em varias telas; (2) `models.get_user_permitted_site_names(user_id)`
  (DB, JOIN com `user_site_permissions`), usado no branch NAO-admin de
  quase tudo; (3) `models.get_all_sites()` (DB, todas as linhas de `sites`),
  usado so' pelas 4 telas de permissao de admin. Ao adicionar um filtro novo
  que precisa valer "em toda fazenda, admin ou nao" (ex. ativo/desativado),
  e' preciso tocar nos 3 mecanismos — nao existe hoje um unico lugar que
  cubra tudo.
- **Fazenda ativa/desativada** (novo, 2026-09-16): coluna `sites.ativo`
  (default 1). `models.set_site_ativo(site_name, ativo)` /
  `models.get_deactivated_site_names()`. Desativar uma fazenda (real ou
  virtual) faz ela sumir de TODAS as telas (Painel, Mapa, Graficos,
  Recomendacoes, NDVI, Envios, Alertas Clima, Exportar, telas de permissao),
  sem apagar nada — existe justamente porque um DELETE de fazenda real seria
  desfeito sozinho na proxima sincronizacao do CSV (ver item acima). Toggle
  fica em **Manejos** (fazenda real e ponto virtual tipo "doenca") e em
  **Mapa Interpolado > Pontos Criados** (todo ponto virtual, ja que ponto
  "so' clima" nunca aparece em Manejos) — rota unica `save_site_ativo`,
  volta pro `request.referrer` (de onde o POST veio).
- **Fazenda virtual/estimada** (`virtual_farms`, tipo `'doenca'` interpola
  concentracao via IDW das fazendas reais no raio; tipo `'clima'` e' so'
  referencia de clima, sem doenca) — criada/editada/removida em **Mapa
  Interpolado**. `create_virtual_farm`/`update_virtual_farm`/
  `delete_virtual_farm` em `models.py` tambem mantem a linha espelho em
  `sites` (mesma tabela das fazendas reais).
- **Padrao de cache por requisicao via `flask.g`**: uma funcao wrapper
  checa `getattr(g, "_cache_key", None) is None` antes de chamar a funcao
  cara `models.get_all_*()`, guardando o resultado em `g` (reseta sozinho a
  cada requisicao). Usado para eliminar N+1 queries quando o mesmo "buscar
  tudo" e necessario uma vez por item num loop (ex.: loop de fazendas em
  `/recommendations`).
- **Scheduler do WhatsApp**: `_whatsapp_scheduler_loop()` roda numa
  `threading.Thread`, chamando `_run_scheduled_whatsapp_sends()` e
  `_run_scheduled_ndvi_sends()` a cada 60s, sempre dentro de
  `with app.app_context():` (sem isso, codigo tocando `flask.g` falha
  silenciosamente dentro do try/except do loop — ja foi bug real em
  producao).
- **Escalonamento de WhatsApp (retencao "Aguardando mensagem") — validado em
  producao, ver `.claude\memory\oneagro_whatsapp_stagger_validado.md`**:
  mandar pra varios destinatarios em rajada faz o WhatsApp reter a entrega.
  `RECIPIENT_STAGGER_HORAS = 1` (1h, nao segundos) resolve tanto (a) varios
  destinatarios da MESMA fazenda quanto (b) o MESMO numero recebendo de
  VARIAS fazendas (`_horarios_efetivos_por_telefone`, agrupa por telefone
  cruzando fazendas). Deduplicacao "ja enviado hoje" sempre por TELEFONE,
  nunca por indice/posicao na lista (lista pode reordenar no mesmo dia).
- **Aba "Envios" (antiga aba principal "WhatsApp", movida em 2026-09-16 pra
  dentro de Configuracoes -> "Envios", admin-only)**: consolida, por
  fazenda, a agenda de texto/PDF/NDVI (dias da semana + horario) e quem
  recebe — antes espalhado entre Manejos, NDVI e Usuarios. Rota
  `whatsapp_gerenciamento` (`/whatsapp`). Ponto "so' clima" tambem aparece
  la' (grupo separado, alfabetico, depois dos de "Monitorar Doencas") — a
  aba **Alertas Clima** NAO tem mais agenda propria (removida em
  2026-09-16 por ser duplicada; so' aponta pra Envios agora).
- **whatsapp-bridge** (`webapp/whatsapp-bridge/index.js`): fila sequencial
  unica, `MIN_DELAY_MS`/`JITTER_MS` = 15-25s entre mensagens (anti-deteccao
  de bot, so' cobre mensagens CONSECUTIVAS na fila do bridge — nao substitui
  o escalonamento por hora acima). A resposta HTTP so' volta depois do envio
  ser processado na fila, entao os timeouts do lado Python (`whatsapp.py`)
  precisam ser maiores que a pior profundidade de fila × delay por mensagem
  (hoje: 75s texto, 100s imagem/documento).
- **Autosave generico** (`templates/base.html`): listeners delegados de
  `input`/`change` no `document`, `data-autosave` dispara um `fetch` POST
  com header `X-Autosave: 1` (rota devolve JSON via `_save_response`, nao
  redirect); `data-autosave-reload` tambem recarrega a pagina apos salvar
  com sucesso. Campos de TEXTO em formularios `data-autosave-reload` so'
  salvam no `focusout`, nao no debounce — evita salvar/recarregar no meio da
  digitacao. Checkbox desmarcado simplesmente NAO manda o campo no
  `FormData` — no servidor, `request.form.get("campo") == "1"` ja vira
  `False` sozinho, sem precisar de campo hidden auxiliar.
- **Datas no Excel**: sempre escrever objetos `date`/`datetime`/`time` reais
  nas celulas (nunca string pre-formatada), com `cell.number_format` — senao
  o Excel trata como TEXTO e o sort/filtro nativo quebra. Mesmo padrao
  usado pro log de envios de WhatsApp (colunas Data/Hora separadas).
- `_agora_cuiaba()` em `models.py`: hora local de Cuiaba (UTC-4, sem
  horario de verao). Usado em todos os timestamps `criado_em`/`disparar_em`.
  **Nunca usar `datetime.now()` puro nem `datetime('now', ...)` do SQLite**
  pra comparar com essas colunas (bug real ja encontrado: fila manual de
  WhatsApp ficava ~4h fora por isso).
- `_nome_exibicao(site_name)`: nome de exibicao de qualquer fazenda (real
  ou virtual) — sempre usar essa funcao em vez do `site_name` cru ao
  mostrar pro usuario (site_name real vem tipo "OneAgro - X", virtual vem
  entre aspas ou com "- Clima -" no meio).

## Visao geral das abas (o que existe hoje, pra nao redigitar/duplicar)

- **Mapa**: pinos das fazendas + fazendas virtuais, fronteiras/estacoes por
  pais, camada opcional de previsao de chuva (raster interpolado, NAO soma
  pontos vizinhos — ver historico abaixo), rodovias federais estaticas.
- **Alertas** (Painel): status por doenca/fazenda, clima sempre via
  Open-Meteo.
- **Graficos**: series historicas de esporos/risco de infeccao por
  fazenda/estacao, filtro por pais.
- **Manejos** (antiga "Fazendas"): cadastro por fazenda — nome de exibicao,
  pais, estacao de referencia, tipo de solo (automatico), Produtos/
  Aplicacoes/Plantio por safra (com catalogo de autocompletar que aprende
  sozinho o que ja foi digitado, e preenche produto<->ingrediente ativo).
  Toggle "Fazenda ativa" (admin) fica aqui. Agenda de WhatsApp NAO fica
  mais aqui (foi pra Envios).
- **Recomendacoes Safra / 2a / 3a**: relatorio por fazenda/safra/cultura,
  com Risco de Infeccao, produtos, copiar/baixar PDF/enviar por WhatsApp na
  hora.
- **NDVI**: contorno (KML/Shapefile do SICAR) por fazenda, busca automatica
  de imagem Sentinel-2 (Copernicus), galeria historica, agendamento
  automatico de envio por WhatsApp.
- **BioScout**: link externo.
- **Envios** (Configuracoes, admin-only): central de agenda (texto/PDF/NDVI)
  e destinatarios por fazenda, ver acima.
- **Configuracoes (⋮, admin)**: Usuarios (+ Subordinados mesclados, com
  pausar WhatsApp em 1 clique), Doencas, Fungicidas, Mapa Interpolado (criar/
  editar/remover/ocultar ponto virtual), Mapa de Solos, Envios, e (so' "Alan
  Mauro"): Alertas Clima, Conexao WhatsApp (QR/status do bridge, horario
  padrao global), Exportar (Excel completo do site).
- **Login**: "Manter conectado" + "Esqueci minha senha" (senha temporaria
  de uso unico enviada por WhatsApp, forca trocar senha no proximo login).

## Como eu gosto de trabalhar (licoes ja aprendidas, nao repetir)

- **Sempre testar de ponta a ponta antes de considerar concluido**: testes
  unitarios Python + testes HTTP via `test_client` + verificacao ao vivo no
  navegador quando fizer sentido (mudanca visual/UX). So' reportar como
  pronto depois de rodar tudo isso.
- **Nunca usar fazenda real para teste** (nem no banco local nem no
  navegador) — usar sempre uma fazenda/usuario descartavel, com nome unico
  e obviamente de teste (ex. "Zzz Teste ..."), e limpar ao final (conferir
  zero orfaos). Ja aconteceu de fazenda real ser alterada sem querer por
  selecao posicional (`querySelectorAll(...)[0]`) — sempre selecionar o
  elemento certo explicitamente.
- Antes de mexer numa fazenda/dado que parece real ("Faz Mourao" etc.),
  confirmar se e' mesmo dado de producao (normalmente e') antes de propor
  qualquer exclusao — preferir desativar (reversivel) a apagar.
- Ao corrigir um bug de UX, preferir um fix ESCOPADO (so' onde o problema
  existe) em vez de mudar o comportamento geral para todo mundo.
- Antes de qualquer operacao destrutiva do git (reset, checkout, clean),
  rodar `git status` primeiro. Sempre criar commits novos (nunca `--amend`),
  nunca pular hooks. Ao comitar, dar `git add` so' nos arquivos do trabalho
  em questao — o working tree normalmente tem arquivos de sincronizacao de
  dados (`data/*.csv`, `BioScoutDashboard.xlsx`, `report.html`) e arquivos
  locais (`.claude/`, imagens, `*.db`) que NAO fazem parte de nenhuma
  entrega e nao devem ser comitados junto.
- Nao adicionar funcionalidades/abstracao alem do pedido; nao adicionar
  comentarios obvios no codigo; nao criar documentos de planejamento a
  menos que eu peca.
- Sem acesso a producao: pra "limpar" dado ruim ja gravado em producao (ex.
  logs de falha errados), a solucao e' uma migracao que roda sozinha no
  proximo deploy (dentro de `init_db()`), nao um script manual.

## Trabalho mais recente concluido (setembro/2026, para referencia)

Nao existe mais nenhum item do "plano salvo" antigo pendente — os 5 itens
que apareciam aqui (catalogo de autocompletar, correlacionar atividades no
Relatorio Diario, raster de chuva interpolado, mapa de chuva no Mapa
Interpolado, "esqueci a senha") foram TODOS implementados e estao em
producao. Destaques do trabalho mais recente:

- **Retencao de WhatsApp ("Aguardando mensagem") resolvida** — ver secao de
  arquitetura acima e a memoria dedicada. Confirmado funcionando em
  producao pelo usuario.
- **Falsas falhas no log de envios**: fazenda sem destinatario cadastrado
  parou de contar como "falha"; historico antigo com esse padrao foi limpo
  via migracao automatica no `init_db()`.
- **Relatorio de envios (aba Exportar)**: colunas Data/Hora separadas (na
  tela e no Excel), com objetos de data/hora reais.
- **Aba "Envios" criada** consolidando agenda + destinatarios (ver
  arquitetura acima); rodape do relatorio de TEXTO do WhatsApp enxugado
  (mantido so' o aviso "nao substitui avaliacao de um agronomo").
  Renomeada aba principal "Fazendas" -> "Manejos" e "Manejo" -> "Recomendacoes".
  Menu reordenado: Mapa, Alertas, Graficos, Manejos.
- **Fazenda ativa/desativada** (ver arquitetura acima) — motivado por uma
  fazenda real que precisava sumir do site sem ser apagada (viria de volta
  sozinha na proxima sincronizacao do CSV).
- **Limpeza da aba Alertas Clima**: removida a tabela de agenda de WhatsApp
  duplicada (ja' coberta pela aba Envios); Mapa Interpolado ganhou o botao
  de ocultar/reativar fazenda tambem.

Historico mais antigo (agosto/inicio de setembro) inclui: expansao pra
America do Sul (Argentina/Chile/Peru/Uruguai/Paraguai/Bolivia, estacoes
oficiais de cada pais), aba NDVI inteira (Sentinel-2/Copernicus, contorno
KML/Shapefile, galeria, agendamento), aba Solo, mapa de chuva (raster
interpolado bilinear, sem o bug de "soma" do Leaflet.heat dependendo do
zoom), varias correcoes de performance (N+1, cache de CSV por mtime),
Excel exportado com varias abas novas (Relatorio Diario correlacionando
Plantio/Aplicacoes/Produtos, Fazendas - Cadastro, etc.), catalogo de
produtos/variedades com autocompletar. Pra detalhes de qualquer item
especifico, `git log --oneline` no repo tem a lista completa (300+
commits) com mensagens descritivas em portugues.

## Nenhum plano pendente no momento

Nao ha' nenhum plano salvo aguardando implementacao nesta virada de conta.
Se eu pedir pra continuar algo, comecar pela investigacao do codigo atual
(o app muda rapido, qualquer suposicao sobre o estado de uma tela deve ser
conferida antes de agir).
