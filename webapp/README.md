# BioScout Web

Painel movel (site responsivo) com login e permissoes por fazenda, lendo os
mesmos dados que o `Fetch-BioScoutData.ps1` ja mantem em `..\data\*.csv`.
Nao busca nada da API sozinho -- rode o atalho "BioScout Dashboard" ou
`Fetch-BioScoutData.ps1` normalmente para manter os dados frescos antes de
abrir o painel.

## Primeira vez (rodar uma vez)

1. Instale as dependencias:
   ```powershell
   C:\Users\AlanMauro\AppData\Local\Programs\Python\Python312\python.exe -m pip install -r requirements.txt
   ```
2. Crie seu usuario administrador (a senha e digitada de forma oculta, nunca
   aparece na tela nem fica em texto puro):
   ```powershell
   C:\Users\AlanMauro\AppData\Local\Programs\Python\Python312\python.exe setup_admin.py
   ```
3. Instale as dependencias do servico de WhatsApp (Node.js portatil ja vem
   em `node-runtime/`, nao precisa instalar nada global na maquina):
   ```powershell
   $env:PATH = "$PSScriptRoot\node-runtime\node-v22.14.0-win-x64;" + $env:PATH
   cd whatsapp-bridge
   npm install
   cd ..
   ```
4. Pareie o WhatsApp: abra o app, va em **Configuracoes > WhatsApp** e
   escaneie o QR code com o celular que vai ser o remetente dos relatorios
   (Aparelhos conectados > Conectar um aparelho, no proprio WhatsApp). So
   precisa fazer isso uma vez -- a sessao fica salva em
   `whatsapp-bridge/auth_info/`.

## Usar

Prefira o atalho `Start-WebApp.ps1` -- alem do app, ele ja sobe o
`whatsapp-bridge` sozinho (numa janela minimizada):
```powershell
.\Start-WebApp.ps1
```
Se preferir rodar so o Flask direto (sem o WhatsApp), ainda funciona:
```powershell
C:\Users\AlanMauro\AppData\Local\Programs\Python\Python312\python.exe app.py
```

Abre em `http://localhost:5000` neste computador. Para acessar pelo celular
**na mesma rede Wi-Fi**, use o endereço de rede que aparece no terminal ao
iniciar (algo como `http://192.168.0.205:5000` -- o numero exato aparece
quando voce roda o comando acima). Pelo celular, abra esse endereço no
navegador e pode "Adicionar a tela inicial" para funcionar como um app.

Isso funciona só dentro de casa/escritório (mesma rede). Para acessar de
qualquer lugar (fora da rede local), é necessário hospedar em um servidor
público — ainda não configurado, combinamos deixar para depois.

## Como funciona

- **Login proprio**: usuario e senha ficam guardados com hash (nunca em texto
  puro) num banco SQLite local (`bioscout_web.db`).
- **Senha padrao e troca de senha**: todo usuario novo criado na aba
  Usuarios (admin) ja nasce com a senha padrao `One1234`
  (`models.DEFAULT_PASSWORD`) -- o formulario de criacao nem pede senha
  mais, so usuario + o checkbox de Admin/Usuario. Qualquer usuario logado
  pode trocar a propria senha no botao **"🔑 Trocar senha"** no menu, do
  lado de "Sair" (visivel em qualquer aba) -- pede a senha atual, a nova
  (minimo 6 caracteres) e a confirmacao (`/trocar-senha`, rota
  `change_password` em `app.py`). Se
  alguem esquecer a senha, um admin pode clicar em **"Redefinir senha"**
  ao lado do nome dele na aba Usuarios, o que volta a senha pra
  `One1234` de novo (`admin_reset_password`) sem precisar saber a senha
  antiga.
- **Email e telefone no cadastro**: ao criar um usuario novo (aba
  Usuarios, admin), email e telefone (com codigo do pais, ex.:
  `5511999999999`) sao obrigatorios e validados no servidor
  (`_is_valid_email`/`_is_valid_phone` em `app.py`) -- telefone aceita
  qualquer formatacao digitada (parenteses, tracos, espacos, +) e guarda
  so os digitos. Aparecem como colunas na lista de Usuarios. Esse
  telefone e' o numero que recebe os relatorios de WhatsApp das fazendas
  marcadas pra essa pessoa (ver abaixo); ela mesma pode corrigi-lo depois
  em **"📱 Meu WhatsApp"** (no menu, do lado de "Trocar senha"). O botao
  **"Editar cadastro"** na lista de Usuarios abre uma tela pra corrigir ou
  preencher email/telefone de um usuario que ja existe (mesma validacao
  da criacao) -- necessario pros usuarios criados antes desse campo
  existir, que ficaram sem email nem telefone (rota `admin_edit_user`,
  `models.set_user_contato`).
- **Envio de WhatsApp pelo numero do administrador (`whatsapp-bridge`)**:
  os relatorios saem de um WhatsApp de verdade (o do administrador),
  nao de um bot de terceiro -- pasta `whatsapp-bridge/` (servico Node.js
  separado, usando a biblioteca **Baileys**, que fala o protocolo
  multi-device do WhatsApp direto, sem precisar de navegador). Pareamento
  e' feito uma unica vez em **Configuracoes > WhatsApp** (admin): a
  pagina mostra um QR code (gerado pelo `whatsapp-bridge` e exposto via
  `whatsapp.get_status()`) pra escanear com o celular que vai ser o
  remetente, em Aparelhos conectados > Conectar um aparelho -- igual
  conectar um WhatsApp Web. A sessao fica salva em
  `whatsapp-bridge/auth_info/`, entao sobrevive a reinicios do app (so
  precisa escanear de novo se desconectar o aparelho pelo proprio
  celular). A pagina atualiza sozinha a cada 10s enquanto nao conectar, e
  tem um campo pra mandar uma mensagem de teste pra qualquer numero assim
  que conectar. O servico sobe sozinho numa janela minimizada junto com o
  app (`Start-WebApp.ps1`, usa o Node.js portatil em `node-runtime/` --
  no precisa instalar nada global na maquina). **Trocamos o CallMeBot por
  isso**: antes cada destinatario precisava da propria API key (o
  CallMeBot so manda mensagem PRA quem autorizou aquela chave, nunca
  serve pra mandar DE um numero PRA outros); com o WhatsApp de verdade
  pareado, o app manda pra qualquer numero cadastrado sem essa exigencia.
  Risco a ter em mente: automatizar o WhatsApp assim nao e' oficialmente
  suportado pela Meta, e o numero pode ser bloqueado por uso automatizado
  -- o servico espaca os envios (`MIN_DELAY_MS` em `whatsapp-bridge/index.js`)
  pra nao mandar em rajada, mas isso reduz o risco, nao elimina.
- **Uma fazenda pode ter varios numeros de WhatsApp**: a lista de quem
  recebe o relatorio de uma fazenda e' gerada por uma escolha explicita
  no cadastro de usuario, **independente** do acesso normal a fazenda
  ("Editar fazendas") e independente de ser Adm ou nao. Na aba Usuarios,
  a coluna **"Receber relatorios"** (do lado de "Permissoes") abre uma
  lista com todas as fazendas -- marque so as que aquela pessoa deve
  receber por WhatsApp (`user_report_permissions`, rota
  `admin_user_reports`, template `admin_report_permissions.html`). So
  entra na lista de destinatarios de uma fazenda quem estiver marcado ali
  PARA aquela fazenda especifica E que ja tenha telefone cadastrado --
  ter acesso pra VER a fazenda (ou ser admin) nao coloca ninguem na lista
  sozinho, sao duas coisas de proposito separadas
  (`models.get_site_whatsapp_recipients`). Uma fazenda com 3 usuarios
  marcados manda o relatorio pros 3 numeros de uma vez (uma chamada ao
  `whatsapp-bridge` por numero). Cada botao "📲 Enviar por WhatsApp"
  mostra entre parenteses quantos numeros vao receber aquele envio (ex.:
  "Enviar por WhatsApp (2)"), e fica desabilitado se a fazenda ainda nao
  tiver nenhum destinatario. O aviso no topo da pagina so aparece se
  NENHUMA fazenda tiver destinatario nenhum. O resultado do envio (manual
  ou "Enviar selecionados") mostra um resumo tipo "2/3 numero(s) -- falha
  em: fulano (motivo)" quando nem todos os envios dao certo -- inclusive
  quando o `whatsapp-bridge` ainda nao foi pareado (a falha vem descrita
  por numero). Os **envios agendados** (dias da semana marcados por
  fazenda) usam a mesma lista de destinatarios da fazenda.
- **Relatorio de WhatsApp em uma unica mensagem de texto, agrupada por
  doenca** (`_format_whatsapp_message`): cada envio (manual ou agendado)
  manda **uma so mensagem** pra cada numero cadastrado, nessa ordem:
  1. Cabecalho -- titulo `"{FAZENDA EM MAIUSCULO} - OneAgro , powered by
     BioScout"` (`_whatsapp_titulo`, tira o "OneAgro - " de dentro do
     `site_name` e poe em destaque so o nome da fazenda), uma linha em
     branco e logo em seguida (uma linha abaixo do nome, sem nada no
     meio) o bloco **🌤️ Clima agora** -- temperatura/umidade/chuva atual e
     a previsao dos proximos dias, com data em **dd/mm** (ex.: "25/08:
     0.6mm (22.0-35.2°C)" -- antes vinha cru do forecast como "08-25",
     agora sempre no mesmo padrao dd/mm usado no resto do relatorio).
  2. Se a fazenda tiver uma cultura definida na aba Manejo (nao estiver
     em "(vazio)"), uma linha **Cultura: *{NOME EM MAIUSCULO E
     NEGRITO}*** (`_cultura_label` em `app.py` -- com `safra` definida
     usa so a cultura daquela safra; no envio agendado, sem `safra`,
     junta os nomes diferentes das safras com " / ", ex. "Soja / Milho").
     Sem cultura definida, a linha simplesmente nao aparece.
  3. **📋 Resumo** -- contagem de quantas doencas estao em PERIGO e em
     ATENCAO (ex.: "1 em PERIGO, 2 em ATENCAO"), sempre logo antes da
     primeira doenca, pra dar o tamanho do problema antes de entrar no
     detalhe.
  4. **Um bloco por doenca em Atencao/Perigo** (🔴/🟡 conforme o status,
     Perigo sempre antes de Atencao) com tudo daquela doenca junto, em
     vez de espalhado em secoes separadas que repetiam o nome dela.
     Status, nome e contagem ficam **numa linha so**: `🔴 PERIGO —
     *FUSARIUM* - Contagem: 66.1 esporos/m³` (status e nome da doenca em
     maiuscula, so o **nome da doenca em negrito** -- nao mais duas
     linhas separadas, e a unidade agora e' "esporos/m³" em vez de so
     "esporos"; sem mais a data da leitura do lado -- ela so aparece uma
     vez no rodape/na tela, ver **Ajustar as datas de leitura** no
     historico). Depois, os ingredientes recomendados (🧪 Biologicos /
     ⚗️ Quimicos, so os **3 primeiros** ingredientes de cada grupo da
     biblioteca -- mesmo limite `[:3]` que a caixa "Recomendacoes das
     Instituicoes de Pesquisa" ja usa na tela -- separados por `//`, so
     aparecem se aquele grupo tiver algo) e uma linha **📝 Obs.** com a
     anotacao manual daquela doenca -- **sempre aparece, mesmo sem
     anotacao** (mostra só um `*` nesse caso), pra manter o mesmo formato
     de relatorio em toda mensagem.
  5. **📦 Produtos ja disponiveis na fazenda** -- os produtos que a
     propria fazenda ja tem comprado (aba Manejo > "Produtos
     Fazenda", `_farm_produtos_estoque`), agrupados em
     Quimicos/Biologicos -- **sempre aparece**, mesmo sem nenhum produto
     cadastrado (mostra `*` no lugar da lista).
  6. Rodape -- "Fonte do clima: Open-Meteo" (so quando tem clima) e,
     sempre por ultimo, "Atualizado em DD/MM/AAAA HH:MM" + " · cidade/UF"
     da estacao de referencia (quando tiver clima com cidade) --
     `rodape_data` em `_format_whatsapp_message`.

  Quando a fazenda **nao tem nenhuma doenca em Atencao/Perigo**, a
  mensagem fica so titulo + linha em branco + Cultura (se tiver) + linha
  em branco + aviso "Nenhuma doenca..." + linha em branco + o mesmo
  rodape "Atualizado em ..." (sem clima nem "Fonte do clima", ja que essa
  mensagem curta nao inclui o bloco de clima).

  Sem `safra` (envio agendado), o texto usa a uniao das doencas e dos
  produtos de todas as safras (`models.SAFRAS`); a partir da tela de
  Manejo de uma safra especifica, usa so os dados daquela safra/cultura.

  Quando a fazenda **nao tem nenhuma doenca em Atencao/Perigo** no
  momento, a mensagem vira so o aviso "Nenhuma doenca em Atencao ou
  Perigo nessa fazenda no momento." (mesmo texto do aviso que aparece na
  tela) -- o envio (manual, agendado, ou o botao "📋 Copiar recomendacao")
  continua funcionando normalmente, so que com essa mensagem no lugar do
  relatorio de doencas.
- **Aviso e bloqueio por estacao defasada** (`_dias_sem_leitura`,
  `_nivel_dados_defasados`): a contagem de esporos de uma fazenda pode
  ficar velha se o equipamento fisico parar de mandar leitura nova pra API
  do BioScout (sem sinal, sem fita, sem energia etc.) -- os **valores**
  ficam parados, mas continuam aparecendo normalmente (inclusive em
  Atencao/Perigo, com dado de meses atras), entao sem um aviso da pra
  mandar uma recomendacao baseada em dado morto sem perceber. O aviso
  visual fica so no **Painel de Alertas** (`dashboard.html`,
  `dados_status_by_site`) -- a tela de Recomendacoes nao mostra nada
  disso, mas continua respeitando o bloqueio (botoes desabilitados com
  tooltip explicando o motivo). Tres faixas, pela cor de fundo do
  cabecalho de cada fazenda no Painel (`site-heading-atencao` /
  `site-heading-bloqueado` em `base.html`):
  - **Ate 7 dias sem leitura** (`DADOS_AVISO_DIAS`): verde -- normal, sem
    badge nenhum.
  - **8 a 15 dias sem leitura**: amarelo, com um badge pequeno alinhado a
    direita no cabecalho ("📡⚠ sem leitura ha Xd") -- so um alerta, envio
    continua liberado.
  - **16+ dias sem leitura** (acima de `DADOS_BLOQUEIO_DIAS = 15`):
    vermelho, badge "🚫 sem leitura ha Xd" -- **bloqueia** o botao "📲
    Enviar por WhatsApp", o "📋 Copiar recomendacao" (fica vazio) e o
    proprio envio agendado (`_send_site_whatsapp` recusa mandar, pra
    qualquer fazenda nessas condicoes, nao importa por onde foi chamado).

  Os dois limites contam a partir da leitura mais recente entre TODAS as
  doencas da fazenda (nao so as que estao em Atencao/Perigo no momento).
- **🧮 Mapa Interpolado -- pontos estimados** (`virtual_farms.py`, menu
  Configuracoes, so admin): cria um ponto em qualquer coordenada (sem
  estacao fisica) e estima a concentracao de cada doenca ali por **IDW**
  (Inverse Distance Weighting -- media ponderada pelo inverso da
  distancia ao quadrado) usando so as fazendas reais dentro de um
  **raio de corte** escolhido na hora de criar o ponto -- fazenda fora do
  raio nunca entra na conta, e se nenhuma fazenda real estiver dentro do
  raio o ponto fica sem estimativa (nao aparece em lugar nenhum ate ter
  uma fazenda por perto ou o raio aumentar). Os limiares de Perigo/Atencao
  usados sao os mesmos da doenca (`data_reader.compute_status`, ja que
  sao os mesmos em toda fazenda), so a concentracao e' que muda. A data de
  leitura de cada doenca interpolada e' a **mais recente** entre as
  fazendas reais que entraram naquela conta -- assim uma fazenda distante
  com a estacao parada nao arrasta a data (nem o bloqueio por dado velho,
  `DADOS_BLOQUEIO_DIAS`) das outras fazendas mais proximas que estao
  atualizadas.

  **É so uma estimativa, nunca leitura real** -- contagem de esporo
  depende muito de condicao hiperlocal (cultura, microclima), entao
  interpolar entre fazendas a dezenas/centenas de km de distancia e' bem
  menos confiavel que uma leitura de verdade. Ainda assim, em todo lugar
  onde um ponto estimado aparece junto das fazendas reais (Painel,
  Recomendacoes, Fazendas, WhatsApp), ele e' tratado como uma fazenda de
  verdade -- sem badge, sem aviso na mensagem de WhatsApp. No **Mapa**
  (normal e Interpolado), o pino e' o mesmo formato do pino de uma
  fazenda real, so que **amarelo claro** (`#fff3bf`) em vez de branco --
  unica pista visual extra de que e' um ponto estimado, direto no mapa.
  Fora disso, a diferenca e' so o padrao de nome: `"{Nome da Fazenda}" -
  OneAgro`
  (`models.create_virtual_farm` -- entre aspas e na ordem invertida da
  fazenda real "OneAgro - X", pra dar pra notar de relance;
  `models.virtual_farm_site_names()` e' quem sabe quais "sites" sao
  estimados, sem depender do formato do nome -- uma fazenda virtual
  antiga, criada antes dessa convencao, continua reconhecida do mesmo
  jeito). No **Painel de Alertas**, o cabecalho de uma fazenda virtual
  mostra so o nome limpo (`nome`, sem o `"..." - OneAgro` em volta) --
  fica mais facil de ler no dia a dia. So na tela **Mapa Interpolado**
  em si (admin, ver abaixo) e' que o ponto continua marcado como
  estimativa, ja que essa tela existe justamente pra gerenciar esses
  pontos.

  Apesar do aviso, um ponto estimado se comporta como uma fazenda de
  verdade no resto do sistema -- aparece no **Painel de Alertas** (com
  clima real da Open-Meteo pra coordenada dele, ja que nao tem sensor de
  umidade/chuva proprio como uma fazenda de verdade), nas
  **Recomendacoes** (com botao de WhatsApp e "Copiar recomendacao") e na
  aba **Fazendas** (produtos comprados, dados de plantio/aplicacoes e a
  agenda de envio automatico por WhatsApp -- tudo junto com as fazendas
  reais, mesmo formulario e mesmas rotas). Pode disparar envio agendado e
  fica sujeito ao mesmo bloqueio por dado defasado (se as fazendas reais
  usadas na conta estiverem velhas, o ponto tambem fica). Quem pode ver o
  ponto se define normalmente na aba **Usuarios** (permissoes), ja que
  criar o ponto ja cadastra ele na tabela `sites` igual uma fazenda do
  CSV.

  Na propria tela do Mapa Interpolado (formulario + lista de pontos
  primeiro, **mapa por ultimo, no final da pagina** -- so pra marcar
  coordenada e ver a cobertura do raio, sem duplicar dado que ja aparece
  em outras abas): o campo **Raio de interpolacao** vem primeiro no
  formulario, numa caixa separada dos outros (Nome da Fazenda/
  coordenadas), com o valor padrao de 120 km ja preenchido, ja que e' um
  parametro padronizado, nao especifico de cada ponto. O ultimo raio
  digitado fica lembrado (`localStorage`, so nesse navegador) -- ao
  cadastrar varios pontos seguidos com o mesmo raio custom, o campo nao
  volta pro padrao de 120 km a cada recarregamento da pagina (ex.: logo
  apos adicionar um ponto). Ao digitar/mudar o raio, um **circulo
  amarelo aparece ao redor de CADA fazenda real**
  (nao so onde vai o ponto novo) mostrando, ao vivo, ate onde o raio
  escolhido alcança -- ajuda a ver antes de cadastrar se aquele raio
  realmente cobre alguma estacao de verdade. Clicar no mapa (ou digitar a
  coordenada direto) so posiciona uma **previa** -- nao cria nada ainda,
  so preenche lat/lon do formulario; o ponto vira fazenda de verdade so
  ao clicar em **"💾 Salvar Ponto"**. Pra evitar criar sem querer, Enter
  em qualquer campo do formulario (nome, raio, coordenadas) e' ignorado
  (`keydown` no `<form id="form-novo-ponto">`), so o clique no botao
  submete. Clicar no mapa marca o pino
  do ponto novo (da pra arrastar depois), que ganha o mesmo tipo de
  circulo, tambem amarelo, pra ficar no mesmo padrao visual das fazendas
  reais. Todo circulo (fazenda real ou ponto novo sendo posicionado) usa
  o mesmo estilo -- **veu branco com 80% de transparencia**
  (`fillColor: '#ffffff', fillOpacity: 0.2`) por cima do mapa de
  satelite, borda amarela marcando o limite exato do raio -- so
  aparecem durante o processo de escolher onde colocar um ponto; uma vez
  cadastrado, o ponto usa o pino amarelo claro (mesmo formato do pino da
  fazenda real, cor diferente) e nao guarda mais nenhum circulo/raio
  visivel no mapa. O mapa tambem tem as mesmas camadas do Mapa normal:
  divisas de pais e estado do IBGE em amarelo e a rodovia BR-163 em
  laranja (via Overpass/OpenStreetMap).

  Cada ponto cadastrado mostra so o essencial pra gerenciar o ponto em
  si: nome, quando/por quem foi criado, um botao "✏️ Editar" (abre um
  formulario com nome/coordenada/raio pre-preenchidos -- `models.
  update_virtual_farm`; mudar o nome renomeia o site_name e migra junto
  tudo que ja tinha sido cadastrado pra aquele ponto: permissoes,
  anotacoes, agenda de WhatsApp, produtos, plantio, aplicacoes, cultura),
  um botao "🗑️ Remover" que apaga o ponto e tudo que foi cadastrado pra
  ele, e (se o raio nao cobrir nenhuma fazenda real) um aviso explicando
  que o ponto ainda nao gera estimativa. A concentracao por doenca em si,
  os produtos, plantio/aplicacoes e a agenda de WhatsApp ficam so no
  **Painel de Alertas**, nas **Recomendacoes** e na aba **Fazendas**
  (mesmo lugar das fazendas reais), pra essa tela ficar focada em
  gerenciar o ponto (onde fica, qual o raio), nao em mostrar dado
  duplicado.
- **Cidade/estacao de referencia (INMET)** (`inmet_stations.py`): pra cada
  fazenda, acha a estacao meteorologica automatica oficial do INMET mais
  proxima da coordenada (catalogo publico `apitempo.inmet.gov.br/estacoes`,
  com cache de 24h) -- so usa o nome da cidade/UF da estacao como
  referencia geografica em cada linha de clima (tela de Recomendacoes e
  mensagem de WhatsApp). Os **valores** de clima continuam vindo 100% da
  Open-Meteo -- a API publica do INMET pra ler o dado medido de uma
  estacao parou de funcionar (o site atual busca isso por uma URL com hash
  gerado por sessao, sem rota fixa pra chamar direto), entao nao da pra
  puxar o numero real do INMET sem simular um navegador. Aparecem como
  pino azul 📡 (distinto do pino da fazenda), mas com escopo diferente em
  cada mapa:
  - No **Mapa** (tela normal): so as estacoes que estao **realmente em
    uso** como referencia de alguma fazenda -- a mais proxima de cada
    uma, ou a escolhida manualmente na aba Fazendas (ver "Escolha da
    estacao de referencia" abaixo), quando houver. Passe o mouse pra ver
    o codigo e a distancia ate a fazenda que ela serve.
  - No **Mapa Interpolado** (admin): **todas** as estacoes automaticas do
    INMET em operacao nos estados cobertos por qualquer coordenada do
    sistema -- fazenda real ou ponto estimado, mesmo um ponto ainda sem
    estimativa (`estacoes_por_uf` em `inmet_stations.py`, chamada por
    `_estacoes_regiao` em `app.py`; o estado de cada coordenada e'
    descoberto pela estacao mais proxima dela). Da uma visao completa da
    rede de referencia disponivel na regiao, util pra decidir onde
    colocar um novo ponto estimado -- por ter muito mais pino nessa tela,
    o icone e' 40% menor que no Mapa normal, pra nao poluir.
- **Escolha da estacao de referencia por fazenda** (aba **Fazendas**): logo
  abaixo dos dados de cada fazenda, um seletor mostra as 2 estacoes do
  INMET mais pertas dela (`inmet_stations.estacoes_mais_proximas`) e a
  opcao "Coordenada da propria fazenda" (padrao). A escolha fica guardada
  em `weather_station_overrides` (`models.py`, chave `site_name`) e passa
  a valer pra qualquer busca de previsao daquele site -- Recomendacoes,
  mensagem de WhatsApp e o fallback de clima de fazenda virtual no Painel
  de Alertas (`_weather_coords_all` em `app.py`, que troca a coordenada da
  fazenda pela da estacao escolhida so pra essa busca, sem afetar a
  posicao dela no mapa nem a interpolacao). Util quando a coordenada
  cadastrada da fazenda fica numa area sem boa cobertura da grade da
  Open-Meteo e uma estacao vizinha da uma leitura mais representativa. Se
  a fazenda virtual mudar de nome/for excluida, a escolha e' migrada ou
  apagada junto (mesmo padrao das outras tabelas por `site_name`).
- **Botao "📋 Copiar recomendacao"**: do lado do "📲 Enviar por WhatsApp",
  em cada fazenda -- copia o **mesmo texto completo** que o envio
  automatico manda, pra colar manualmente numa conversa qualquer. Serve
  de alternativa enquanto o `whatsapp-bridge` nao estiver pareado, ou pra
  mandar pra alguem que nao esta cadastrado como destinatario. Um so
  clique: tenta `navigator.clipboard.writeText`, com fallback pra
  `document.execCommand('copy')` via textarea escondida se o navegador
  bloquear a API moderna, e mostra "⚠️ Copie manualmente" no botao se nem
  isso funcionar. Fica desabilitado so quando a estacao da fazenda esta
  bloqueada por dado defasado (ver **Aviso e bloqueio por estacao
  defasada** acima) -- sem doenca em Atencao/Perigo, copia normalmente a
  mensagem de "tudo tranquilo".
- **Link "📊 BioScout"**: link direto pra
  `https://new.bioscout.com.au/#Spore%20Graphs` (Spore Graphs, rota
  interna da SPA do BioScout), abre numa aba nova (`target="_blank"`),
  mesmo padrao do link da logo OneAgro. E' uma conta separada da do
  OneAgro -- quem acessar loga com o proprio usuario do BioScout na aba
  nova, sem nenhuma senha passando pelo app OneAgro. (Ja existiu uma
  versao embutida em iframe com duas paginas -- Devices Summary e Spore
  Graphs, rota `/bioscout/<pagina>` -- removida a pedido do OneAgro em
  favor de um unico link direto.)
- **Logo no topo**: clicar na logo da OneAgro (`base.html`) abre
  https://www.oneagro.com.br/ numa aba nova (`target="_blank"`), sem
  perder a sessao/pagina atual do app.
- **Administrador**: ao criar um usuario na aba Usuarios (admin), um
  checkbox define o papel dele -- marcado = **Adm** (acesso a tudo, todas
  as fazendas, sem precisar marcar nada); desmarcado = **Usuario** (so as
  fazendas que um admin liberar depois, em "Editar fazendas" na mesma
  lista, com caixas de selecao por fazenda). O icone "⋮" (tres pontinhos,
  configuracoes) ao lado de "Sair" no menu -- que da acesso as abas
  administrativas Usuarios, Doencas, Fungicidas, WhatsApp e Nome Culturas
  -- so aparece pra quem e' Adm (`{% if current_user.is_admin %}` em
  `base.html`); um Usuario comum nem ve o botao, e tentar acessar essas
  rotas direto pela URL da' 403 (`@admin_required` em `app.py`, checagem
  dupla: nao mostra o botao E bloqueia a rota mesmo assim).
- **Painel de Alertas** (rotulo do menu -- rota continua `/dashboard`,
  endpoint `dashboard`): mostra, por fazenda liberada, um cartao por doenca com a
  ultima leitura, cor por status (vermelho/amarelo/verde -- mesmas cores da
  planilha) e o nome da doenca com um veu branco translucido por tras
  (`.doenca` em `base.html`) pra destacar o texto sobre a cor forte do
  fundo. A umidade/chuva NAO fica repetida em cada caixa -- aparece uma
  unica vez ao lado do nome da fazenda (badge branco), usando a leitura
  mais recente entre as doencas daquela fazenda (`weather_by_site` em
  `app.py`, calculado com `max(cards, key=lambda c: c["data"])`). Essa
  umidade/chuva vem do proprio dado do sensor da fazenda (dado historico
  do dia da leitura, arquivo/API do BioScout), e nao de uma fonte externa
  como NOAA -- a previsao do tempo externa (Open-Meteo, ver abaixo) so
  aparece nas abas Manejo. Mostra SEMPRE todas as doencas
  monitoradas naquela fazenda, sem filtro de cultura e sem nenhum rotulo
  de cultura (o filtro e o rotulo de cultura so existem nas abas
  Recomendacoes). Nao ha mais data de leitura nem botao de atualizar
  dentro da caixa -- pagina ficou mais enxuta, so concentracao + status +
  nome cientifico.

  **Ordem das fazendas**: por data da leitura mais recente (real ou
  virtual/estimada), da mais nova pra mais velha -- usa o mesmo `data`
  ja calculado em `weather_by_site`. Reordenado no fim de `dashboard()`
  (`app.py`), depois de montar `cards_by_site`.
- **Atualizacao automatica**: toda vez que o Mapa, Painel de Alertas ou
  Recomendacoes e' aberto -- seja abrindo o site pela primeira vez (cai no
  Mapa) ou dando F5/clicando no botao de recarregar do navegador em
  qualquer uma dessas abas -- se os dados tiverem mais de 15 minutos, uma
  busca nova comeca sozinha em segundo plano na API do BioScout (nao trava
  a pagina, e nao dispara duas buscas ao mesmo tempo -- `_maybe_auto_refresh`
  / `_start_fetch_if_not_running` em `app.py`, chamado em `mapa()`,
  `dashboard()` e `recommendations()`). Esse intervalo de 15 min esta
  fixo em `AUTO_REFRESH_MAX_AGE_SECONDS`, ajuste se quiser. O Painel de
  Alertas tambem tem um botao manual "🔄 Forcar atualizacao"
  (`dashboard_atualizar` em `app.py`) pra nao esperar os 15 min -- em
  ambos os casos, so funciona onde o PowerShell existe (Windows); no
  site hospedado (Linux) a busca nunca completaria de verdade, entao o
  botao avisa isso em vez de fingir que funcionou.
- **Aba Mapa**: e' a **aba inicial** -- login e a rota `/` redirecionam pra
  ca (`url_for("mapa")` em `login()`/`index()` no `app.py`), e o link
  "Mapa" e' o primeiro no menu (depois vem "Fazendas", "Painel de
  Alertas", "Manejo Safra", "Manejo 2ª Safra", "Manejo 3ª Safra" -- um
  link por entrada de `models.SAFRAS`, gerado pelo context processor
  `_inject_nav_safras` em `app.py`, sem precisar tocar em `base.html`
  se um dia mudar de novo). Mapa de
  satelite (visual "Google Earth") com um pino por fazenda, na coordenada
  cadastrada em `spore_counts.csv` (`data_reader.read_site_coordinates`)
  -- fazenda sem coordenada simplesmente nao aparece no mapa (mas
  continua normal nas outras abas).
  Usa **Leaflet** (biblioteca JS gratuita, sem conta/API key) com a camada
  de satelite **Esri World Imagery**, entao nao depende do Google Maps
  nem de nenhuma chave paga. Cada pino e' um icone de localizacao branco
  com contorno azul-marinho (cor do texto da marca) e a **logo oficial da
  OneAgro** (`static/oneagro-logo.png`) dentro, recortada so na folha --
  o arquivo tem a folha + o texto "oneagro" lado a lado, entao o `<image>`
  dentro do SVG usa uma caixa quadrada com
  `preserveAspectRatio="xMinYMid slice"`, que corta exatamente o quadrado
  da esquerda (a folha) sem precisar editar a imagem (`PIN_SVG` em
  `mapa.html`). Passar o mouse sobre um pino mostra um tooltip com o nome
  da fazenda, a data da leitura mais recente entre as doencas daquela
  fazenda ("Leitura de AAAA-MM-DD", mesmo calculo de `max(...)` usado no
  `weather_by_site` do Painel de Alertas) e a mesma lista de doencas
  (bolinha colorida por status + nome + valor), sem precisar clicar --
  `L.marker(...).bindTooltip(...)` com `sticky: true`. **Clicar** no pino
  (em vez de so passar o mouse) navega direto pra `/fazendas?site=...`,
  que abre e da scroll ate o card daquela fazenda (`data-site` no card +
  script no fim de `fazendas.html` que le o `?site=` da URL) -- mesmo
  comportamento no Mapa Interpolado. Rota `/mapa`, mesma logica de
  permissao por fazenda das outras abas.
  Tambem desenha, direto de fontes oficiais (sem nenhum efeito visual
  inventado):
  - **Divisas de pais e estado em amarelo** e **divisas de municipio em
    branco**, da **API do IBGE** (`servicodados.ibge.gov.br/api/v3/malhas`,
    gratis, sem chave). Pais e estados sao leves (um pedido cada, ficam
    sempre visiveis); municipio e' pesado (so 1 estado ja passa de 4 MB),
    entao SO busca a malha municipal dos estados onde existe pelo menos 1
    fazenda -- descobre quais estados sao esses fazendo um "point in
    polygon" no proprio navegador (funcao `pointInGeometry` em
    `mapa.html`) contra o geojson de estados que ja foi baixado pra
    desenhar as divisas estaduais, sem precisar de nenhum servico externo
    de geocoding.
  - **Rodovia BR-163 em laranja**, ponta a ponta, vinda do **OpenStreetMap**
    via **Overpass API** (`overpass-api.de`, gratis, sem chave) -- busca
    todo trecho de via com `ref=BR-163` no Brasil inteiro (`way["ref"="BR-163"]`)
    e desenha cada segmento como uma linha, sem precisar de nenhum arquivo
    de rota pronto.

  Se alguma dessas APIs externas (IBGE ou Overpass) cair ou nao tiver
  internet, a camada correspondente simplesmente nao aparece (falha
  silenciosa, no `.catch` de cada uma) -- o mapa e os pinos continuam
  funcionando normalmente.
- **Abas Manejo Safra / Manejo 2ª Safra / Manejo 3ª Safra** (nomes exibidos
  -- rota e' `/recommendations/<safra>`, endpoint/template continuam
  chamados `recommendations` internamente, so o texto visivel pro usuario
  mudou pra "Manejo"): a mesma tela duplicada pra acompanhar tres safras
  em paralelo (ex.: soja na Safra, milho safrinha na 2ª Safra, uma
  terceira cultura na 3ª Safra) -- `safra` = `safra1`, `safra2` ou
  `safra3` (`models.SAFRAS`), cada uma com seu proprio filtro de cultura.
  Acrescentar uma 4a safra e' so adicionar uma tupla em `models.SAFRAS`
  (o menu, os formularios de Produtos/Plantio/Aplicacoes da aba Fazendas
  e o filtro de cultura em Recomendacoes/Manejo todos leem essa lista, sem
  nenhum outro lugar pra sincronizar manualmente). **Todas as fazendas
  liberadas aparecem em todas as abas** (nao so as que estiverem em
  alerta no momento -- fazenda sem
  nada em Atencao/Perigo mostra um aviso "Nenhuma doenca..." dentro do
  conteudo expandido). Cada fazenda aparece recolhida por padrao, mas o
  checkbox de selecao, o nome, o seletor **"Cultura atual"** e o botao
  **"Enviar por WhatsApp"** daquela fazenda ficam sempre visiveis lado a
  lado (mesma logica de "clique para expandir" da aba Fazendas, so que
  essa linha toda nao se esconde) -- clique no nome para abrir (▼) so o
  restante daquela fazenda (miniaturas, clima, doencas, Produtos Fazenda,
  Anotacoes, agenda de WhatsApp). O seletor de cultura define a cultura so
  daquela safra, sem afetar as outras abas; o padrao pra fazenda que
  ainda nao foi configurada (recem importada do BioScout ou recem criada
  pelo site, real ou virtual) e' "(vazio)" -- so muda quando alguem
  escolher manualmente ali. Acima da lista de fazendas tem
  um checkbox **"Selecionar todas"** (marca/desmarca o checkbox de todas
  as fazendas de uma vez, e se desmarcar uma fazenda manualmente ele
  volta a ficar desmarcado) e um botao **"Enviar selecionados por
  WhatsApp"** que dispara o envio, em sequencia, so das fazendas
  marcadas -- rota `/recommendations/whatsapp/selecionados`, que reusa a
  mesma logica de montagem de mensagem do botao individual (`safra` da
  aba atual, checagem de permissao por fazenda) e junta os resultados
  numa unica mensagem: uma lista das que enviaram com sucesso e outra das
  que falharam com o motivo de cada uma (WhatsApp nao configurado, sem
  nada em Atencao/Perigo, etc). Dentro do conteudo expandido, os blocos
  aparecem nessa ordem:
  1. Por doenca, uma caixa **"Recomendacoes das Instituicoes de Pesquisa"**
     com so os 3 primeiros ingredientes ativos de cada grupo da biblioteca
     (aba Fungicidas) -- **Biologicos na primeira linha, Quimicos na
     segunda**, cada uma so aparece se aquele grupo tiver ingrediente
     cadastrado (senao a linha inteira some, nao fica um "Quimicos:"
     vazio). Dentro de cada linha, os ingredientes sao separados por
     " // " (ex.: `Biologicos: item1 // item2 // item3`), sem link de
     fonte (a lista completa com fonte fica so na aba Fungicidas).
     Doenca sem nada cadastrado ainda nao mostra essa caixa.
  2. **Produtos Fazenda** -- 2 blocos lado a lado (Quimicos / Biologicos),
     com Data/Anotacao + Nome do produto por linha, para um lembrete
     rapido do que a fazenda ja tem comprado NAQUELA safra. Cada bloco
     comeca com **2 linhas** e cresce sozinho conforme a ultima vai sendo
     preenchida, sem limite fixo -- mesma logica (e mesmo JS, adaptado
     pra `<div>` em vez de `<table>`) da grade da aba Fazendas, ver
     `.estoque-rapido-rows` em `recommendations.html`. Usa a mesma tabela
     `farm_produtos` da aba Fazendas, mas com seu proprio "momento"
     (`geral`, ver `models.MOMENTO_ESTOQUE_RAPIDO`) dentro da safra da
     pagina, entao nao se mistura com o cadastro por TS/Folha feito na
     aba Fazendas nem com a outra safra.
  3. **Anotacoes** -- um campo de anotacao editavel por doenca
     (recomendacao do agronomo, dose, etc.), com 5 linhas visiveis que
     crescem sozinhas conforme a pessoa digita (classe CSS
     `auto-grow-textarea`), salvo por fazenda+doenca (compartilhado entre
     todas as safras, ja que e' a mesma doenca/leitura).

  A agenda de envio automatico por WhatsApp (dias da semana) fica na aba
  **Fazendas**, nao aqui -- ver abaixo -- ja que e' uma configuracao da
  fazenda, nao da safra (o mesmo agendamento vale pras duas). O envio
  manual clicado numa dessas abas respeita a cultura da safra de onde
  partiu o clique.

  No topo de cada fazenda
  aparecem miniaturas de todas as doencas monitoradas ali (mesmas cores do
  Painel), nao so as em alerta -- da' uma visao geral rapida antes dos
  detalhes. Logo abaixo, mostra o clima atual (temperatura, umidade, chuva
  na hora) e a previsao de chuva/temperatura dos proximos 3 dias, usando a
  latitude/longitude de cada fazenda (vem do `spore_counts.csv`) contra a
  API gratuita [Open-Meteo](https://open-meteo.com/) (`weather_forecast.py`,
  sem necessidade de conta ou chave). O resultado fica em cache por 30
  minutos por fazenda (`WEATHER_CACHE_TTL_SECONDS` em `app.py`) para nao
  bater na API a cada carregamento da pagina.
- **Aba Fungicidas (admin)**: biblioteca de ingredientes ativos (quimico e
  biologico) por doenca, com a fonte usada em cada grupo -- cobre TODAS as
  doencas que ja apareceram na aba Doencas, nao so as de soja. 4 doencas
  (Ferrugem da Soja, Mancha Alvo, Oidio, Septoriose/DFC) vem da ferramenta
  de eficacia da Embrapa/UFV (tem classe E/MB/B/R/F, ate 10 quimicos e ate 5
  biologicos por doenca quando a ferramenta tem esse tanto disponivel); as
  demais (Mancha de Alternaria, Ferrugem do Milho, Moniliophthora,
  Antracnose, Fusarium) nao tem uma ferramenta de classificacao dedicada --
  os ingredientes foram levantados via pesquisa na internet (Agrolink/
  Agrofit-MAPA, Embrapa, CEPLAC, Adapar, artigos academicos, ver
  `fungicida_data.py` para as fontes completas de cada doenca), com a meta
  de ate 10 quimicos + 5 biologicos, mas SEM inventar entradas para bater
  esse numero -- doencas onde a literatura realmente so sustenta menos
  (ex.: Mancha de Alternaria com 5 quimicos/1 biologico, Oidio sem nenhum
  biologico registrado) ficam com menos linhas mesmo, e por isso nao tem
  uma classe de eficacia comparavel. Doenca nova que ainda nao tem nada
  pesquisado aparece marcada "Sem dados ainda" -- pesquise e adicione uma
  entrada em `fungicida_data.py`. Cada linha pode ser editada quando algo
  nao fizer sentido para a sua realidade -- a mudanca vale para todas as
  fazendas que tiverem aquela doenca em alerta (nao e' por fazenda); so
  grava uma edicao no banco (`fungicida_overrides`) quando o texto
  realmente muda, entao atualizar `fungicida_data.py` no futuro nao fica
  "preso" em edicoes antigas que ninguem fez de verdade. Cada linha do
  formulario usa um campo com nome unico (`ingrediente__<row_id>` etc, nao
  uma lista posicional) para nao correr risco de misturar valores entre
  linhas ao salvar uma pagina com muitas doencas. Como so os 3 primeiros
  ingredientes de cada grupo (quimico/biologico) aparecem nas abas
  Recomendacoes, cada linha tem botoes ▲/▼ ("mover para cima"/"mover para
  baixo") para reordenar a lista e colocar o melhor tratamento da regiao
  no topo -- essa ordem fica salva por doenca+tipo na tabela
  `fungicida_ordem` (guarda so a posicao de cada indice original, entao
  sobrevive a edicoes de texto e a novas doencas em `fungicida_data.py`
  sem se confundir). Como essa pagina pode ficar bem longa (uma doenca
  atras da outra, cada uma com varias linhas), mover um item com ▲/▼
  guarda a posicao do scroll em `sessionStorage` antes de enviar o
  formulario e volta pra ela depois que a pagina recarrega -- sem isso,
  cada clique jogava a tela de volta pro topo, obrigando a rolar tudo de
  novo pra continuar mexendo na mesma doenca. IMPORTANTE: a biblioteca
  e' indexada pelo nome ORIGINAL em ingles da doenca (o mesmo do BioScout /
  `DOENCA_MAP`), nunca pelo nome em portugues -- esse e' editavel na aba
  Doencas e ja foi renomeado antes (Ferrugem da Soja -> Ferrugem Asiatica,
  Septoriose -> DFC), entao indexar pelo nome em portugues quebraria a
  recomendacao silenciosamente a cada renomeacao.
- **Aba Doencas (admin)**: o BioScout reporta os nomes das doencas em
  ingles -- aqui voce define o nome em portugues que aparece no site.
  Doencas novas que o BioScout passar a reportar entram sozinhas nessa
  lista (com o nome em ingles como padrao) assim que aparecerem nos dados,
  prontas para traduzir -- nada quebra se o BioScout adicionar ou renomear
  uma doenca (a biblioteca de Fungicidas usa o nome em ingles por baixo dos
  panos, entao renomear aqui nao afeta as recomendacoes). Tem tambem uma
  coluna **Nome cientifico do fungo**, preenchida sozinha com o
  `scientificName` que o BioScout ja manda em cada leitura (`spore_counts.csv`)
  na primeira vez que a doenca aparece -- edite se estiver faltando ou
  incompleto (fica salvo em `disease_translations.nome_cientifico`).
- **Aba Fazendas**: cadastro dos fungicidas e biologicos que cada fazenda
  ja tem comprado, com uma sub-aba por safra -- **Safra**, **2ª Safra**,
  **3ª Safra** (`models.SAFRAS`) -- dentro de cada fazenda -- um botao
  alterna qual delas esta visivel, cada uma com seu proprio
  formulario/botao de salvar.
  Dentro de cada safra, separado por momento de aplicacao -- **TS**
  (tratamento de sementes) e **Folha** (aplicacao foliar) -- e dentro de
  cada momento, por tipo (Fungicidas quimicos / Biologicos). Cada grade
  (Data/Anotacao | Nome do produto | Ingrediente ativo) comeca com **1
  linha**, sem limite fixo -- ao digitar em qualquer campo da ultima
  linha da tabela, uma nova linha em branco aparece sozinha no navegador
  (JS puro em `fazendas.html`, delegacao de evento `input` no `tbody`,
  marca a linha com `dataset.grown` pra so crescer 1 vez por linha), e
  assim sucessivamente sem fim. Ao reabrir a pagina, a grade mostra todas
  as linhas ja preenchidas mais 1 linha em branco extra pra continuar
  digitando (minimo de 1 no total) -- calculado em `fazendas()` no
  `app.py`. Linhas em branco nao sao salvas. Cada fazenda
  aparece recolhida por padrao (so o nome, com um icone ▶) -- clique no
  nome para expandir (▼) e ver/editar o estoque, sem precisar rolar a
  pagina toda com todas as fazendas abertas. Guardado em `farm_produtos`
  (coluna `safra`); qualquer usuario com permissao naquela fazenda pode
  editar (admin ve e edita todas) -- quando tiver mais usuarios, o dono da
  fazenda preenche os produtos e a mudanca aparece pra todo mundo com
  permissao naquela fazenda, e vice-versa (nao e' um cadastro por usuario,
  e' por fazenda). Logo abaixo de Folha, dentro da mesma safra, tem mais
  duas grades independentes (formulario e botao de salvar proprios,
  tabelas `farm_plantio` e `farm_aplicacoes`):
  - **Dados de Plantio** -- Data plantio | Talhao | Variedade | Ciclo
    (Dias).
  - **Dados de Aplicacoes** -- Data Aplicacao | Talhao | Fungicidas
    Quimicos | Fungicidas Biologicos (colunas separadas, mesmo padrao
    quimico/biologico das outras grades -- `fungicidas_quimicos` e
    `fungicidas_biologicos` em `farm_aplicacoes`).

  As duas ja comecam com **1 linha** (igual as grades de TS/Folha acima)
  e usam a mesma logica de crescimento automatico (mesma classe
  `produtos-table`, o JS generico ja cobre qualquer tabela com essa
  classe) -- ao preencher a ultima, a proxima aparece sozinha, sem
  limite.
- **Agenda de envio automatico por WhatsApp**: checkboxes de dias da
  semana por fazenda, logo abaixo das grades de Plantio/Aplicacoes (uma
  so vez por fazenda, nao repete por safra, ja que e' configuracao da
  fazenda -- `save_whatsapp_days`, tabela `whatsapp_days`) -- um
  agendador roda sozinho em segundo plano e envia automaticamente nos
  dias marcados, as 7h (`WHATSAPP_SEND_HOUR` em `app.py`). Isso so
  funciona enquanto o `app.py` estiver rodando naquele horario -- se o
  processo estiver fechado as 7h, o envio daquele dia nao acontece. Fica
  na aba Fazendas desde a ultima revisao (era duplicado nas abas de
  Manejo antes).
- **Cultura atual (filtro por safra)**: como as doencas monitoradas nao sao
  todas da mesma cultura e uma fazenda pode ter culturas diferentes em
  cada safra (ex.: soja na Safra, milho na 2ª Safra), cada aba Manejo
  (Safra / 2ª Safra / 3ª Safra) tem um seletor "Cultura atual" na frente
  do nome de cada fazenda, valendo so para aquela safra. Ao definir uma
  cultura, aquela aba de Manejo passa a mostrar so as doencas
  marcadas para aquela cultura na matriz Doencas x Culturas (aba Doencas,
  admin) -- doenca sem nenhuma marcacao na matriz continua aparecendo
  sempre, pra nao esconder um alerta novo/desconhecido por engano. O
  Painel nao filtra por cultura (mostra tudo sempre, ver secao Painel
  acima). Deixar em "(vazio)" (o padrao de fazenda nova) remove o filtro
  daquela safra -- mostra todas as doencas, sem nenhuma cultura definida.
  Guardado em `farm_culturas` (chave site+safra) com `updated_at`; se
  fizer mais de 120 dias desde a ultima troca, aparece um aviso
  "⚠ ha Xd" do lado do seletor lembrando de confirmar se a safra mudou.
- **Nome Culturas (menu ⋮, admin)**: ate 10 caixas de texto com os nomes
  das culturas disponiveis para o seletor "Cultura atual" e para as
  colunas da matriz Doencas x Culturas -- vem pre-preenchidas com Soja,
  Milho, Algodao, Feijao, Citrus, Cana, Batata (as 3 ultimas em branco,
  para completar depois). Todas editaveis a qualquer momento; guardado em
  `culturas` (10 posicoes fixas, slot 0-9).
- **Doencas x Culturas (aba Doencas, admin)**: matriz com uma linha por
  doenca e uma coluna por cultura cadastrada em Nome Culturas -- marque
  quais culturas cada doenca deve aparecer quando o filtro estiver ativo.
  Uma doenca pode valer para mais de uma cultura (ex.: Oidio em Soja e
  Algodao). Guardado em `doenca_cultura` (muitos-para-muitos); vem
  pre-preenchida na primeira vez que o banco e' criado com as doencas ja
  conhecidas de soja/milho, e depois disso e' 100% editavel por aqui.
- **Envio por WhatsApp**: usa o `whatsapp-bridge` (Node.js + Baileys, ver
  secao acima) -- o WhatsApp de verdade do administrador manda os
  relatorios, pareado uma vez em **Configuracoes > WhatsApp**.
  - Botao **"📲 Enviar por WhatsApp"** em cada fazenda na aba
    Recomendacoes -- envio manual, na hora.
  - Checkboxes de dias da semana em cada fazenda, na aba **Fazendas** --
    um agendador roda sozinho em segundo plano e envia automaticamente
    nos dias marcados, as 7h (`WHATSAPP_SEND_HOUR` em `app.py`). Isso so
    funciona enquanto o `app.py` estiver rodando naquele horario -- se o
    processo estiver fechado as 7h, o envio daquele dia nao acontece.
  - A mensagem e' um texto unico com Dados de Clima, Fungos em alta
    quantidade, Recomendacoes das Instituicoes de Pesquisa, Produtos
    Fazenda e Anotacoes (ver `_format_whatsapp_message`).
- As fazendas na tela de permissoes se atualizam sozinhas conforme o
  `sites.csv` muda (novas fazendas aparecem automaticamente).

## Adicionar/remover usuarios depois

Login como administrador > menu **Usuarios** > formulario "Novo usuario" ou
botao **Excluir** na lista.
