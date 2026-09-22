# OneAgro Monitor (BioScoutMonitor)

Contexto persistente deste projeto. Carregado automaticamente sempre que uma
sessão do Claude Code trabalha nesta pasta, independente de qual conta/usuario
Claude esta sendo usada.

Mantido por: Alan Mauro (alan.mauro@biotrop.com.br), unico responsavel pelo app.

- App: Flask "OneAgro Monitor" — monitoramento de esporos/clima/doencas em
  fazendas.
- Repo: github.com/alanmauro-oneagro/monitoresporos
- Deploy: Railway, auto-deploy a partir da branch `main`.
- URL producao: https://monitoresporos-production.up.railway.app
- Codigo do app fica em `webapp/` dentro deste repo.

## Regra de negocio permanente (nao mudar sem confirmar com o Alan)

**Somente o usuario com username exato "Alan Mauro" pode ajustar configuracoes
gerais do site.** Varias rotas/relatorios admin sao propositalmente restritos
a `current_user.username == 'Alan Mauro'`, NAO apenas `current_user.is_admin`
— ex.: painel admin do WhatsApp, relatorio de envios do WhatsApp, relatorio de
fungicidas (dropdown de configuracoes em `webapp/templates/base.html` e rotas
correspondentes em `webapp/app.py`). E' deliberado: outros usuarios marcados
como admin nao devem ver essas telas. Ao criar novas rotas/relatorios/
configuracoes gerais do site, seguir o mesmo padrao de gate por username, a
menos que seja pedido explicitamente paridade para outros admins.

## Arquitetura / conceitos tecnicos principais

- Flask + Jinja2 + Bootstrap + Leaflet 1.9.4, SQLite (`models.get_db()`, modo
  WAL), openpyxl para exportacao Excel, `data_reader.py` para dados de
  clima/esporos via CSV, `countries.py` para provedores de estacao por pais.
- **Cache por requisicao via `flask.g`**: uma funcao wrapper checa
  `getattr(g, "_cache_key", None) is None` antes de chamar a funcao cara
  `models.get_all_*()`, guardando o resultado em `g` (reseta sozinho a cada
  requisicao). Usar esse padrao para evitar N+1 queries quando o mesmo
  "buscar tudo" e necessario uma vez por item num loop (ex.: loop de fazendas
  em `/recommendations`). Ja aplicado a: `_nome_exibicao`, ordem de
  fungicidas, overrides/bloqueios de fungicidas, info de doenca, produtos de
  fazenda.
- **Scheduler do WhatsApp**: `_whatsapp_scheduler_loop()` roda numa
  `threading.Thread`, chamando `_run_scheduled_whatsapp_sends()` e
  `_run_scheduled_ndvi_sends()` a cada 60s, sempre dentro de
  `with app.app_context():` (sem isso, qualquer codigo tocando `flask.g`
  falha silenciosamente dentro do try/except do loop — ja foi bug em
  producao, todos os envios agendados falhavam sem erro visivel).
- Cada fazenda tem horario (hora+minuto) configuravel para envio de
  texto/PDF via WhatsApp; horario e sugerido automaticamente para fazendas
  novas (comecando as 5:00, intervalo de 3 minutos entre fazendas) e so'
  quando ha' pelo menos um dia de envio marcado. Pontos "so' clima" (sem
  fazenda associada) sao sempre processados DEPOIS de todas as recomendacoes
  de fazenda, em cada tick do scheduler.
- **whatsapp-bridge** (`webapp/whatsapp-bridge/index.js`): fila sequencial
  unica, 15-25s entre mensagens (anti-deteccao de bot) — a resposta HTTP so'
  volta apos o envio ser processado na fila, entao os timeouts do lado Python
  (`webapp/whatsapp.py`) precisam ser maiores que a pior profundidade de fila
  possivel × delay por mensagem (hoje: 75s texto, 100s imagem/documento).
- **Autosave generico** (`webapp/templates/base.html`): `data-autosave`
  dispara salvamento via fetch; `data-autosave-reload` tambem recarrega a
  pagina apos salvar. Para campos de TEXTO em formularios
  `data-autosave-reload`, o salvamento so' dispara no `focusout` (perder o
  foco), nunca so' por debounce — evita salvar/recarregar a pagina no meio da
  digitacao. Campos de escolha (select/checkbox/radio) continuam no debounce
  normal (1200ms).
- **Renomear fazenda virtual**: `models.update_virtual_farm(...)` muda o
  `nome`, que muda o proprio `site_name` (cascata em varias tabelas).
  Acessivel tanto por Mapa Interpolado quanto pela aba Manejos (campo "Nome
  de exibicao").
- **Datas no Excel**: sempre escrever objetos `date`/`datetime` reais nas
  celulas (nunca string pre-formatada), usando
  `cell.number_format = "DD/MM/YYYY"` para exibicao — senao o Excel trata
  como TEXTO e o proprio sort/filtro do Excel quebra.
- `_agora_cuiaba()` em `models.py`: hora local de Cuiaba (UTC-4, sem horario
  de verao). Usado em todos os timestamps `criado_em`. **Nunca usar
  `datetime('now', ...)` do proprio SQLite** para comparar com essas colunas
  (retorna UTC, 4h de diferenca) — qualquer logica de corte por data deve
  calcular em Python com a mesma formula.
- **Fazenda REAL vem sincronizada de `data/sites.csv`** via `_ensure_sites_synced()`
  (`models.sync_sites`, roda a cada requisicao) — so' ADICIONA linha nova na
  tabela `sites`, NUNCA remove. Uma fazenda no CSV sempre reaparece sozinha,
  mesmo apagada na mao do banco.
- **Fazenda ativa/desativada** (`sites.ativo`, `models.set_site_ativo`/
  `get_deactivated_site_names`): esconde fazenda (real ou virtual) de TODAS
  as telas sem apagar — existe por causa do ponto acima (delete nao
  resolveria). Toggle em Manejos e em Mapa Interpolado > Pontos Criados
  (rota `save_site_ativo`, redireciona pro `request.referrer`). Preferir
  desativar a apagar quando o pedido for "sumir com uma fazenda".
- **3 mecanismos diferentes de "listar fazendas"** (sem choke point unico):
  CSV cru (`read_sites()`/`get_dashboard_data(None,...)`) pro admin em
  varias telas, `models.get_user_permitted_site_names()` (DB) pro
  nao-admin, `models.get_all_sites()` (DB) nas 4 telas de permissao. Um
  filtro que precisa valer "sempre, admin ou nao" tem que tocar nos 3.
- **Aba "Envios"** (Configuracoes -> Envios, admin-only, rota
  `whatsapp_gerenciamento`/`/whatsapp`): consolida agenda de texto/PDF/NDVI
  + destinatarios por fazenda (inclusive pontos "so' clima"). Substituiu a
  agenda que antes ficava espalhada em Manejos/NDVI/Alertas Clima — ao
  mexer em agenda de envio, e' aqui que fica agora.
- **Escalonamento de WhatsApp** (retencao "Aguardando mensagem", validado em
  producao — ver `.claude\memory\oneagro_whatsapp_stagger_validado.md`):
  `RECIPIENT_STAGGER_HORAS = 1` (horas, nao segundos) entre destinatarios da
  mesma fazenda E entre fazendas diferentes pro mesmo numero
  (`_horarios_efetivos_por_telefone`). Dedup "ja enviado hoje" sempre por
  TELEFONE, nunca por indice/posicao na lista.

## Como trabalhar neste projeto (licoes ja aprendidas — nao repetir)

- **Sempre testar de ponta a ponta antes de considerar concluido**: testes
  unitarios Python + testes HTTP via `test_client` + verificacao ao vivo no
  navegador quando fizer sentido (mudanca visual/UX). So' reportar como
  pronto depois de rodar tudo isso.
- **Nunca usar fazenda real para teste no navegador** — usar sempre uma
  fazenda descartavel, com nome unico e obviamente de teste (ex. "Zzz Teste
  ..."), e limpar ao final. Nunca selecionar elemento por posicao/primeiro-
  match (`querySelectorAll(...)[0]`) — ja causou alteracao acidental de
  fazenda real. Selecionar sempre explicitamente pelo elemento certo.
- Ao corrigir um bug de UX, preferir um fix ESCOPADO (so' onde o problema
  existe) em vez de mudar o comportamento geral para todo mundo.
- Antes de qualquer operacao destrutiva do git (reset, checkout, clean),
  rodar `git status` primeiro. Sempre criar commits novos (nunca `--amend`),
  nunca pular hooks.
- Nao adicionar funcionalidades/abstracao alem do pedido; nao adicionar
  comentarios obvios no codigo; nao criar documentos de planejamento a menos
  que seja pedido.

## Plano salvo, ainda nao implementado

Nenhum no momento (atualizado 2026-09-21) — o plano anterior (catalogo de
autocompletar, correlacionar atividades no Relatorio Diario, raster de
chuva interpolado, mapa de chuva no Mapa Interpolado, "esqueci a senha")
foi TODO implementado e ja esta em producao. Resumo completo do trabalho
recente em `.claude\RESUMO_PARA_NOVA_CONTA.md`.

## Notas / historico arquivado

Arquivos de memoria antigos (ligados a uma conta/sessao anterior) ficavam em
`C:\Users\AlanMauro\.claude\projects\...\memory\` — esse local foi
descontinuado. A partir de agora, este `CLAUDE.md` (raiz do repo) e a pasta
`.claude\memory\` DENTRO deste projeto sao a fonte de verdade. Novas
memorias/decisoes relevantes de longo prazo devem ser gravadas aqui ou em
`.claude\memory\`, nao mais no caminho antigo.
