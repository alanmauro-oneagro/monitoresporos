# BioScout Monitor

Baixa os dados de monitoramento de esporos (BioScout, new.bioscout.com.au)
via API oficial, acumula em CSV e gera um relatório visual (report.html) e uma
planilha interativa. A atualização é sob demanda — veja "Atualização sob
demanda (ao abrir)" abaixo.

## O que é baixado

- `data/spore_counts.csv` — contagem/concentração de esporos por site, dispositivo e doença (desde 01/10/2025)
- `data/weather.csv` — leituras horárias de clima por site
- `data/spray_logs.csv` — registros de pulverização
- `data/site_reports.csv` — relatórios/notas de campo
- `data/sites.csv` — lista de sites da conta

## Configuração inicial (rodar uma vez)

1. Abra o PowerShell nesta pasta.
2. Rode:
   ```powershell
   .\Setup-Credentials.ps1
   ```
   Vai pedir seu e-mail e senha do BioScout. Ficam salvos criptografados
   (`bioscout_cred.xml`), só legíveis pelo seu usuário neste computador.

3. Rode a primeira busca completa (desde 01/10/2025 — pode demorar alguns minutos):
   ```powershell
   .\Run-Daily.ps1
   ```
4. Abra `report.html` no navegador para ver o resultado.

## Uso diário

Rodar `Run-Daily.ps1` novamente só busca o que mudou (mês atual) e o que é novo,
bem mais rápido que a primeira vez.

## Envio automático por e-mail

Além do `report.html` local (todos os sites), `Run-Daily.ps1` também envia um
e-mail diário resumido apenas dos sites configurados em `Send-Report.ps1`
(parâmetro `-Sites`), com o relatório completo em anexo.

Configuração (rodar uma vez):

```powershell
.\Setup-EmailCredentials.ps1
```

Peça o e-mail remetente e a **senha de aplicativo** (App Password) do Outlook —
não a senha normal da conta. Gere uma em https://account.live.com/proofs/AppPassword.
Fica salva criptografada em `email_cred.xml`, só legível pelo seu usuário neste computador.

Para alterar os sites monitorados ou o destinatário, edite os parâmetros padrão
no topo de `Send-Report.ps1`.

## Planilha interativa (BioScoutDashboard.xlsx)

Além do `report.html`, existe uma planilha Excel que se alimenta sozinha e tem
filtros interativos de verdade (Segmentações de Dados / Slicers):

- **Aba Dashboard**: gráfico de tendência de concentração de esporos (média
  diária por doença) com 3 slicers — **Site**, **Doença** e **Mês** — que
  filtram o gráfico ao clicar, sem precisar editar nada.
- **Aba Data_Status**: última leitura de cada site/doença, com cor
  (verde/amarelo/vermelho) e filtro nativo de tabela.
- **Aba Data_SporeCounts** e **Data_Weather**: dados brutos (clima agregado
  por dia) por trás do dashboard, conectados via Power Query direto aos CSVs.
- **Aba BaseDados**: tabela única combinando esporos + clima, cabeçalhos em
  português (Data, Mês, Fazenda, Dispositivo, Doença, Quantidade de Esporos,
  Status, Temperatura, Umidade, QuantidadeChuva), ordenada da data mais nova
  para a mais velha, com a formatação condicional do Status preservada.
- **Aba Analise Cruzada**: dashboard de apresentação (banners, cartões de
  KPI, gráfico com eixo secundário) para cruzar fazenda, esporos, status,
  chuva, umidade e data, com slicers de **Fazenda**, **Doença**, **Status**
  e **Mês/Ano** — pensado para apresentar a produtores e técnicos.
- **Aba Alertas do Dia**: um cartão colorido por fazenda e doença, no mesmo
  estilo da tela "Disease Alerts" do site do BioScout — mostra a leitura mais
  recente de cada doença (valor, nome em português, nome científico) e,
  dentro do próprio cartão, a **umidade e a chuva do dia**. A cor do cartão
  segue o status (verde/amarelo/vermelho).

A planilha é reconstruída em 5 fases (esporos/gráfico/slicers → status →
clima → base de dados/análise cruzada → alertas do dia), cada uma com
novas tentativas automáticas em caso de falha da automação do Excel.

Para criar ou recriar do zero:

```powershell
.\Build-Dashboard.ps1
```

Para só atualizar com os dados mais recentes (o que `Run-Daily.ps1` já faz
sozinho todo dia):

```powershell
.\Refresh-Dashboard.ps1
```

Internamente `Refresh-Dashboard.ps1` reconstrói o arquivo do zero — tentar
"atualizar" as conexões de um arquivo já aberto se mostrou instável nessa
automação (Power Query + Slicers via COM); reconstruir em fases, com novas
tentativas automáticas, é o que funciona de forma confiável.

## Atualização sob demanda (ao abrir)

Não há mais tarefa agendada rodando sozinha de manhã. Para ver os dados mais
recentes, use o atalho **"BioScout Dashboard"** na área de trabalho (ou rode
`.\Open-Dashboard.ps1`): ele busca o que houver de novo no BioScout, reconstrói
a planilha do zero e já abre o arquivo atualizado — tudo em um clique.

Abrir `BioScoutDashboard.xlsx` diretamente (sem passar pelo atalho) só mostra
os dados da última vez que alguém rodou o atalho/script, sem buscar nada novo.

Cada execução registra um log em `.\logs\open-dashboard-AAAA-MM-DD-HHmmss.log`.

`Run-Daily.ps1` (busca + relatório HTML + e-mail + planilha) continua
existindo caso você queira voltar a agendar ou rodar manualmente o fluxo
completo com envio de e-mail; ele só não é mais chamado automaticamente.

## Botão "Atualizar Tudo" do Excel (opcional)

As consultas `SporeCounts`, `Weather` e `BaseDados` já vêm configuradas para
buscar direto na API do BioScout (em vez de ler os CSVs locais) — assim, o
botão nativo **Dados > Atualizar Tudo** do Excel também busca dados novos,
sem precisar do atalho. Isso é opcional: o usuário/senha vêm **em branco de
propósito** (para a senha não ficar guardada em texto puro dentro do
arquivo), então antes do primeiro uso é preciso configurar uma vez:

1. Na planilha aberta, vá em **Dados > Consultas e Conexões**.
2. Clique com o botão direito em **SporeCounts** > **Editar** (abre o Editor
   do Power Query).
3. No painel de passos (direita), clique no primeiro passo (geralmente
   chamado algo como "Origem" ou o primeiro item da lista). No editor de
   fórmulas (barra `fx` acima da tabela), procure as linhas
   `BioScoutUser = ""` e `BioScoutPassword = ""` e troque as aspas vazias
   pelo seu e-mail e senha do BioScout, ex.: `BioScoutUser = "seu@email.com"`.
4. Repita o mesmo para as consultas **Weather** e **BaseDados** (usam os
   mesmos dois campos).
5. Feche o Editor do Power Query e clique em **Atualizar Tudo**.
6. Na primeira vez, o Excel deve pedir para definir o **Nível de
   Privacidade** da fonte de dados (rest.bioscout.com.au) — escolha uma
   opção (ex. "Organizacional") e confirme. Isso só aparece uma vez por
   fonte de dados neste computador.

Depois desse passo único, **Atualizar Tudo** passa a buscar dados novos
direto da API sempre que clicado, sem precisar do atalho — mas como a senha
fica salva na consulta a partir desse momento, o arquivo passa a conter uma
credencial em texto simples (e ele sincroniza com o OneDrive). Se preferir
não fazer isso, é só continuar usando o atalho "BioScout Dashboard", que
não exige guardar a senha no arquivo.

## Notas de segurança

- A senha nunca é exibida nem armazenada em texto puro — fica protegida por
  criptografia do Windows (DPAPI), atrelada à sua conta de usuário e a este
  computador. Copiar `bioscout_cred.xml` para outra máquina ou usuário não funciona.
- Se trocar a senha do BioScout, rode `Setup-Credentials.ps1` de novo.
- O mesmo vale para `email_cred.xml` e `Setup-EmailCredentials.ps1` caso a senha
  de aplicativo seja revogada.
