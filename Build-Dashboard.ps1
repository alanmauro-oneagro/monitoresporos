<#
Cria (do zero) a planilha BioScoutDashboard.xlsx:
- Conecta via Power Query direto aos CSVs em .\data (sem copiar/colar dados)
- Dashboard com faixa de titulo, cartoes de indicadores (KPIs), grafico de
  tendencia de esporos e segmentacoes de dados (Site, Doenca, Mes)
- Tabela de status atual (ultima leitura por site/doenca) com formatacao condicional
- Aba de clima (agregado diario, para manter o arquivo leve)
- Base de dados combinada (esporos + clima por dia, cabecalhos em portugues) e
  um segundo dashboard (Analise Cruzada) para cruzar Fazenda, Doenca, Esporos,
  Status, Chuva e Umidade, agrupado por Mes/Ano e dia, com filtro de Mes/Ano
- Aba "Alertas do Dia": cartoes coloridos por fazenda/doenca (estilo BioScout)
  com a ultima leitura, status, umidade e chuva do dia

Construido em 5 fases, cada uma numa sessao separada do Excel. A automacao
COM do Excel com Power Query + Slicers e ocasionalmente instavel neste tipo
de ambiente, entao cada fase tenta novamente sozinha se falhar.

Rode este script de novo a qualquer momento para recriar o arquivo do zero.
Para so atualizar os dados de um arquivo ja existente, use Refresh-Dashboard.ps1.
#>

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$dataDir = Join-Path $root 'data'
$outputPath = Join-Path $root 'BioScoutDashboard.xlsx'

if (-not (Test-Path (Join-Path $dataDir 'spore_counts.csv'))) {
    throw "Nao ha dados em $dataDir. Rode Fetch-BioScoutData.ps1 primeiro."
}
if (Test-Path $outputPath) { Remove-Item $outputPath -Force }

function New-ExcelApp {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    return $excel
}

function Close-ExcelApp {
    param($excel, $wb)
    if ($wb) { try { $wb.Close($false) } catch {} }
    if ($excel) {
        try { $excel.Quit() } catch {}
        [System.Runtime.Interopservices.Marshal]::ReleaseComObject($excel) | Out-Null
    }
    Get-Process EXCEL -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    [System.GC]::Collect()
    [System.GC]::WaitForPendingFinalizers()
}

function Test-PhaseReallyFinished {
    param([string[]]$ExpectedSheets, [scriptblock]$Verify)
    if ((-not $ExpectedSheets -or $ExpectedSheets.Count -eq 0) -and -not $Verify) { return $true }
    if (-not (Test-Path $outputPath)) { return $false }
    $checkExcel = $null
    $checkWb = $null
    try {
        $checkExcel = New-ExcelApp
        $checkWb = $checkExcel.Workbooks.Open($outputPath, $false, $true)
        $names = @()
        foreach ($ws in $checkWb.Worksheets) { $names += $ws.Name }
        foreach ($expected in $ExpectedSheets) {
            if ($names -notcontains $expected) { return $false }
        }
        if ($Verify) {
            return [bool](& $Verify $checkWb)
        }
        return $true
    } catch {
        return $false
    } finally {
        Close-ExcelApp -excel $checkExcel -wb $checkWb
    }
}

function Invoke-Phase {
    param([string]$Name, [scriptblock]$Body, [string[]]$ExpectedSheets = @(), [scriptblock]$Verify = $null, [int]$MaxAttempts = 6)
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        Write-Host "$Name (tentativa $attempt/$MaxAttempts)"
        $excel = New-ExcelApp
        $wb = $null
        try {
            & $Body $excel ([ref]$wb)
            Write-Host "  ok." -ForegroundColor Green
            Close-ExcelApp -excel $excel -wb $wb
            return
        } catch {
            $errMsg = "$_"
            Close-ExcelApp -excel $excel -wb $wb
            # As vezes o Excel salva o arquivo com sucesso mas o COM ainda assim
            # reporta um erro na chamada seguinte (falso alarme). So confiamos
            # nisso se reabrirmos o arquivo e as abas que esta fase deveria criar
            # realmente estiverem la -- nao basta o arquivo ter mudado de tamanho.
            if (Test-PhaseReallyFinished -ExpectedSheets $ExpectedSheets -Verify $Verify) {
                Write-Warning "  erro pos-salvamento ignorado (conteudo esperado confirmado no arquivo): $errMsg"
                return
            }
            Write-Warning "  falhou: $errMsg"
            Start-Sleep -Seconds 12
        }
    }
    throw "$Name falhou apos $MaxAttempts tentativas."
}

function Add-QuerySafe {
    param($wb, [string]$Name, [string]$Formula)
    try { $wb.Queries.Item($Name).Delete() } catch {}
    $wb.Queries.Add($Name, $Formula) | Out-Null
}

function Add-WorksheetSafe {
    param($wb, [string]$Name)
    try { $wb.Worksheets.Item($Name).Delete() } catch {}
    $ws = $wb.Worksheets.Add()
    $ws.Name = $Name
    return $ws
}

function Load-Query {
    param($ws, [string]$queryName)
    $connString = "OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=`$Workbook`$;Location=$queryName;Extended Properties=`"`""
    $lo = $ws.ListObjects.Add(0, $connString, [System.Reflection.Missing]::Value, 1, $ws.Range("A1"))
    $lo.QueryTable.BackgroundQuery = $false
    $lo.QueryTable.CommandType = 2
    $lo.QueryTable.CommandText = "SELECT * FROM [$queryName]"
    $lo.QueryTable.Refresh($false) | Out-Null
    return $lo
}

function Add-StatusColors {
    param($range, $missing)
    $range.FormatConditions.Delete()
    $addr = $range.Cells.Item(1, 1).Address(0, 0)
    $c1 = $range.FormatConditions.Add(2, $missing, "=$addr=`"Perigo`"")
    $c1.Interior.Color = 0x6B6BFF
    $c2 = $range.FormatConditions.Add(2, $missing, "=$addr=`"Atencao`"")
    $c2.Interior.Color = 0x3BD4FF
    $c3 = $range.FormatConditions.Add(2, $missing, "=$addr=`"Normal`"")
    $c3.Interior.Color = 0x66CF51
}

function BGR([int]$r, [int]$g, [int]$b) { return $b * 65536 + $g * 256 + $r }

function ToNum($s) {
    if ([string]::IsNullOrWhiteSpace($s)) { return $null }
    try { return [double]($s -replace ',', '.') } catch { return $null }
}

function Get-Doenca([string]$displayName) {
    switch ($displayName) {
        "General Alternaria" { "Mancha de Alternaria" }
        "General Rust" { "Ferrugem do Milho" }
        "Target Spot" { "Mancha Alvo" }
        "Powdery Mildew" { "Oidio" }
        "Moniliophthora spp. BETA" { "Moniliophthora" }
        "Soybean Rust" { "Ferrugem da Soja" }
        "Anthracnose" { "Antracnose" }
        "Dry rot" { "Fusarium" }
        "Septoria" { "Septoriose" }
        default { $displayName }
    }
}

# Paleta de cores (BGR, para as propriedades .Color do Excel COM)
$colorBanner    = BGR 21 87 36     # verde escuro
$colorBannerTxt = BGR 255 255 255  # branco
$colorSubtitle  = BGR 198 224 180  # verde claro
$colorCardBg    = BGR 240 244 240  # cinza-esverdeado bem claro
$colorCardNum   = BGR 21 87 36     # verde escuro (numero neutro)
$colorCardDanger = BGR 41 41 201   # vermelho (BGR de #c92929)
$colorCardWarn   = BGR 12 130 232  # laranja (BGR de #e8820c)

# Cores de preenchimento para os cartoes de alerta do dia -- mesmas cores usadas
# na formatacao condicional da coluna Status (Add-StatusColors), para ficar
# consistente em toda a planilha.
$fillDanger  = 0x6B6BFF         # igual ao Status "Perigo"
$fillWarn    = 0x3BD4FF         # igual ao Status "Atencao"
$fillNormal  = 0x66CF51         # igual ao Status "Normal"
$fillNoData  = BGR 224 224 224  # cinza - sem leitura na data selecionada
$cardTextDark = BGR 40 40 40    # texto escuro sobre os cartoes

function Add-Banner {
    param($ws, [string]$Title, [string]$Subtitle, [int]$WidthCols = 10)
    for ($col = 1; $col -le $WidthCols; $col++) { $ws.Columns.Item($col).ColumnWidth = 13 }

    $titleRange = $ws.Range($ws.Cells.Item(1, 1), $ws.Cells.Item(2, $WidthCols))
    $titleRange.Merge() | Out-Null
    $titleRange.Interior.Color = $colorBanner
    $titleRange.Font.Color = $colorBannerTxt
    $titleRange.Font.Size = 20
    $titleRange.Font.Bold = $true
    $titleRange.Font.Name = "Segoe UI"
    $titleRange.VerticalAlignment = -4108
    $titleRange.HorizontalAlignment = -4131
    $titleRange.IndentLevel = 1
    $titleRange.Value2 = $Title
    $ws.Rows.Item(1).RowHeight = 30
    $ws.Rows.Item(2).RowHeight = 22

    $subRange = $ws.Range($ws.Cells.Item(3, 1), $ws.Cells.Item(3, $WidthCols))
    $subRange.Merge() | Out-Null
    $subRange.Interior.Color = $colorBanner
    $subRange.Font.Color = $colorSubtitle
    $subRange.Font.Size = 10
    $subRange.Font.Italic = $true
    $subRange.Font.Name = "Segoe UI"
    $subRange.HorizontalAlignment = -4131
    $subRange.IndentLevel = 1
    $subRange.Value2 = $Subtitle
    $ws.Rows.Item(3).RowHeight = 18
    $ws.Rows.Item(4).RowHeight = 8
}

function Add-KpiCard {
    param($ws, [int]$Row, [int]$ColStart, [int]$ColSpan, $Value, [string]$Label, $BgColor, $NumColor)
    $numRange = $ws.Range($ws.Cells.Item($Row, $ColStart), $ws.Cells.Item($Row, $ColStart + $ColSpan - 1))
    $numRange.Merge() | Out-Null
    $numRange.Interior.Color = $BgColor
    $numRange.Font.Color = $NumColor
    $numRange.Font.Size = 24
    $numRange.Font.Bold = $true
    $numRange.Font.Name = "Segoe UI"
    $numRange.HorizontalAlignment = -4108
    $numRange.VerticalAlignment = -4107
    $numRange.NumberFormat = "@"
    $numRange.Cells.Item(1, 1).Value2 = [string]$Value

    $lblRange = $ws.Range($ws.Cells.Item($Row + 1, $ColStart), $ws.Cells.Item($Row + 1, $ColStart + $ColSpan - 1))
    $lblRange.Merge() | Out-Null
    $lblRange.Interior.Color = $BgColor
    $lblRange.Font.Color = $NumColor
    $lblRange.Font.Size = 10
    $lblRange.Font.Name = "Segoe UI"
    $lblRange.HorizontalAlignment = -4108
    $lblRange.NumberFormat = "@"
    $lblRange.Value2 = $Label

    $ws.Rows.Item($Row).RowHeight = 36
    $ws.Rows.Item($Row + 1).RowHeight = 16
}

function Add-DiseaseCardLive {
    param($ws, [int]$Row, [int]$Col, [string]$ValorFormula, [string]$Doenca, [string]$Cientifico, [string]$ClimaFormula, [string]$Site, [string]$DoencaLit, $missing, [int]$BaseMaxRow, [int]$HelperCol)
    $r1 = $ws.Range($ws.Cells.Item($Row, $Col), $ws.Cells.Item($Row, $Col + 1)); $r1.Merge() | Out-Null
    $r2 = $ws.Range($ws.Cells.Item($Row + 1, $Col), $ws.Cells.Item($Row + 1, $Col + 1)); $r2.Merge() | Out-Null
    $r3 = $ws.Range($ws.Cells.Item($Row + 2, $Col), $ws.Cells.Item($Row + 2, $Col + 1)); $r3.Merge() | Out-Null
    $r4 = $ws.Range($ws.Cells.Item($Row + 3, $Col), $ws.Cells.Item($Row + 3, $Col + 1)); $r4.Merge() | Out-Null
    $full = $ws.Range($ws.Cells.Item($Row, $Col), $ws.Cells.Item($Row + 3, $Col + 1))

    foreach ($r in @($r1, $r2, $r3, $r4)) {
        $r.Interior.Color = $fillNoData
        $r.Font.Color = $cardTextDark
        $r.Font.Name = "Segoe UI"
        $r.HorizontalAlignment = -4131
        $r.IndentLevel = 1
    }

    $r1.Font.Size = 20
    $r1.Font.Bold = $true
    $r1.Cells.Item(1, 1).NumberFormat = "Geral"
    $r1.Cells.Item(1, 1).Formula = $ValorFormula

    $r2.Font.Size = 11
    $r2.Font.Bold = $true
    $r2.NumberFormat = "@"
    $r2.Cells.Item(1, 1).Value2 = [string]$Doenca

    $r3.Font.Size = 8
    $r3.Font.Italic = $true
    $r3.NumberFormat = "@"
    $cientificoTxt = if ($Cientifico) { $Cientifico } else { "" }
    $r3.Cells.Item(1, 1).Value2 = [string]$cientificoTxt

    $r4.Font.Size = 9
    $r4.Cells.Item(1, 1).NumberFormat = "Geral"
    $r4.Cells.Item(1, 1).Formula = $ClimaFormula

    $ws.Rows.Item($Row).RowHeight = 26
    $ws.Rows.Item($Row + 1).RowHeight = 16
    $ws.Rows.Item($Row + 2).RowHeight = 13
    $ws.Rows.Item($Row + 3).RowHeight = 14

    # SUMPRODUCT/COUNTIFS dentro de FormatConditions.Add() nao funcionam de forma
    # confiavel nesta automacao COM: COUNTIFS lanca "O valor nao recai no intervalo
    # esperado" e SUMPRODUCT nao lanca erro mas tambem nunca dispara a condicao
    # (confirmado testando =SUMPRODUCT((1=1)*1)>0 isoladamente). O que funciona e
    # comparar contra uma UNICA celula (igual ao Add-StatusColors) -- por isso o
    # status e calculado numa celula auxiliar oculta e a formatacao condicional so
    # compara texto contra essa celula.
    $colFazenda = "BaseDados!`$C`$2:`$C`$$BaseMaxRow"
    $colDoenca  = "BaseDados!`$E`$2:`$E`$$BaseMaxRow"
    $colData    = "BaseDados!`$A`$2:`$A`$$BaseMaxRow"
    $colStatus  = "BaseDados!`$G`$2:`$G`$$BaseMaxRow"
    $baseMatch = "($colFazenda=`"$Site`")*($colDoenca=`"$DoencaLit`")*($colData=DataSelecionada)"

    # $HelperCol e um ponto de partida -- deslocado por $Col para nao colidir com
    # outros cartoes que compartilham a mesma $Row (ate 6 cartoes por linha).
    $helperColActual = $HelperCol + [math]::Floor(($Col - 1) / 2)
    $helperCell = $ws.Cells.Item($Row, $helperColActual)
    $helperCell.Formula = "=IF(SUMPRODUCT($baseMatch*($colStatus=`"Perigo`"))>0,`"Perigo`",IF(SUMPRODUCT($baseMatch*($colStatus=`"Atencao`"))>0,`"Atencao`",IF(SUMPRODUCT($baseMatch)>0,`"Normal`",`"SemDados`")))"
    $helperAddr = $helperCell.Address(1, 1)

    $full.FormatConditions.Delete()
    $cPerigo = $full.FormatConditions.Add(2, $missing, "=$helperAddr=`"Perigo`"")
    $cPerigo.Interior.Color = $fillDanger
    $cAtencao = $full.FormatConditions.Add(2, $missing, "=$helperAddr=`"Atencao`"")
    $cAtencao.Interior.Color = $fillWarn
    $cNormal = $full.FormatConditions.Add(2, $missing, "=$helperAddr=`"Normal`"")
    $cNormal.Interior.Color = $fillNormal
}

Write-Host "Calculando indicadores (KPIs)..."
$sporeCsvData = Import-Csv (Join-Path $dataDir 'spore_counts.csv')
$sitesCsvData = Import-Csv (Join-Path $dataDir 'sites.csv')
$weatherCsvData = Import-Csv (Join-Path $dataDir 'weather.csv')

$kpiSites = $sitesCsvData.Count
$kpiDiseases = ($sporeCsvData | Select-Object -ExpandProperty displayName -Unique).Count
$kpiReadings = $sporeCsvData.Count

$latestPerGroup = $sporeCsvData | Group-Object siteName, displayName | ForEach-Object {
    $_.Group | Sort-Object { [datetime]$_.samplingStartTime } | Select-Object -Last 1
}
$kpiPerigo = ($latestPerGroup | Where-Object {
    (ToNum $_.dangerConcentrationThreshold) -gt 0 -and (ToNum $_.concentration) -ge (ToNum $_.dangerConcentrationThreshold)
}).Count
$kpiAtencao = ($latestPerGroup | Where-Object {
    -not ((ToNum $_.dangerConcentrationThreshold) -gt 0 -and (ToNum $_.concentration) -ge (ToNum $_.dangerConcentrationThreshold)) -and
    (ToNum $_.warningConcentrationThreshold) -gt 0 -and (ToNum $_.concentration) -ge (ToNum $_.warningConcentrationThreshold)
}).Count

$rainValues = $weatherCsvData | ForEach-Object { ToNum $_.rainFall } | Where-Object { $_ -ne $null }
$humidityValues = $weatherCsvData | ForEach-Object { ToNum $_.humidity } | Where-Object { $_ -ne $null }
$kpiChuvaTotal = if ($rainValues) { [math]::Round(($rainValues | Measure-Object -Sum).Sum, 0) } else { 0 }
$kpiUmidadeMedia = if ($humidityValues) { [math]::Round(($humidityValues | Measure-Object -Average).Average, 0) } else { 0 }

$dataInicio = ($sporeCsvData | ForEach-Object { [datetime]$_.samplingStartTime } | Measure-Object -Minimum).Minimum.ToString('dd/MM/yyyy')
$dataFim = ($sporeCsvData | ForEach-Object { [datetime]$_.samplingStartTime } | Measure-Object -Maximum).Maximum.ToString('dd/MM/yyyy')
$geradoEm = Get-Date -Format 'dd/MM/yyyy HH:mm'

Write-Host "  sites=$kpiSites doencas=$kpiDiseases leituras=$kpiReadings perigo=$kpiPerigo atencao=$kpiAtencao chuva=$kpiChuvaTotal umidade=$kpiUmidadeMedia"

Write-Host "Preparando cartoes de alerta (ligados ao seletor de data)..."
$comboRows = $sporeCsvData | Group-Object siteName, displayName | ForEach-Object {
    $first = $_.Group | Select-Object -First 1
    [PSCustomObject]@{
        Fazenda    = $first.siteName
        Doenca     = Get-Doenca $first.displayName
        Cientifico = $first.scientificName
    }
}
$uniqueDatesDesc = $sporeCsvData | ForEach-Object { ([datetime]$_.samplingStartTime).Date } | Sort-Object -Unique -Descending
$defaultAlertDate = $uniqueDatesDesc | Select-Object -First 1
Write-Host "  $($comboRows.Count) combinacoes fazenda/doenca, $($uniqueDatesDesc.Count) datas disponiveis (padrao: $($defaultAlertDate.ToString('dd/MM/yyyy')))"

$missing = [System.Reflection.Missing]::Value

$mSporeCounts = @"
let
    Source = Csv.Document(File.Contents("$dataDir\spore_counts.csv"),[Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    Typed = Table.TransformColumnTypes(Promoted,{{"siteName", type text}, {"displayName", type text}, {"warningConcentrationThreshold", type number}, {"dangerConcentrationThreshold", type number}, {"samplingStartTime", type datetimezone}, {"concentration", type number}}),
    AddedDay = Table.AddColumn(Typed, "day", each Date.From(DateTimeZone.RemoveZone([samplingStartTime])), type date),
    AddedYearMonth = Table.AddColumn(AddedDay, "yearMonth", each Date.ToText([day], "yyyy-MM"), type text),
    AddedDoenca = Table.AddColumn(AddedYearMonth, "doenca", each
        if [displayName] = "General Alternaria" then "Mancha de Alternaria"
        else if [displayName] = "General Rust" then "Ferrugem do Milho"
        else if [displayName] = "Target Spot" then "Mancha Alvo"
        else if [displayName] = "Powdery Mildew" then "Oidio"
        else if [displayName] = "Moniliophthora spp. BETA" then "Moniliophthora"
        else if [displayName] = "Soybean Rust" then "Ferrugem da Soja"
        else if [displayName] = "Anthracnose" then "Antracnose"
        else if [displayName] = "Dry rot" then "Fusarium"
        else if [displayName] = "Septoria" then "Septoriose"
        else [displayName], type text)
in
    AddedDoenca
"@

$mStatus = @"
let
    Source = SporeCounts,
    AddedStatus = Table.AddColumn(Source, "status", each if [dangerConcentrationThreshold] > 0 and [concentration] >= [dangerConcentrationThreshold] then "Perigo" else if [warningConcentrationThreshold] > 0 and [concentration] >= [warningConcentrationThreshold] then "Atencao" else "Normal", type text),
    Sorted = Table.Sort(AddedStatus,{{"siteName", Order.Ascending},{"doenca", Order.Ascending},{"samplingStartTime", Order.Descending}})
in
    Sorted
"@

$mWeather = @"
let
    Source = Csv.Document(File.Contents("$dataDir\weather.csv"),[Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    Typed = Table.TransformColumnTypes(Promoted,{{"deviceId", Int64.Type}, {"deviceUserFriendlyId", type text}, {"temperature", type number}, {"humidity", type number}, {"rainFall", type number}, {"dateMeasured", type datetimezone}}),
    AddedDay = Table.AddColumn(Typed, "day", each Date.From(DateTimeZone.RemoveZone([dateMeasured])), type date),
    Grouped = Table.Group(AddedDay, {"deviceUserFriendlyId", "day"}, {
        {"temperaturaMedia", each List.Average(List.RemoveNulls([temperature])), type nullable number},
        {"umidadeMedia", each List.Average(List.RemoveNulls([humidity])), type nullable number},
        {"chuvaTotal", each List.Sum(List.RemoveNulls([rainFall])), type nullable number}
    })
in
    Grouped
"@

$mBaseDados = @"
let
    SourceSpore = Csv.Document(File.Contents("$dataDir\spore_counts.csv"),[Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    PromotedSpore = Table.PromoteHeaders(SourceSpore, [PromoteAllScalars=true]),
    TypedSpore = Table.TransformColumnTypes(PromotedSpore,{{"siteName", type text}, {"displayName", type text}, {"deviceUserFriendlyId", type text}, {"warningConcentrationThreshold", type number}, {"dangerConcentrationThreshold", type number}, {"samplingStartTime", type datetimezone}, {"concentration", type number}}),
    AddedDaySpore = Table.AddColumn(TypedSpore, "day", each Date.From(DateTimeZone.RemoveZone([samplingStartTime])), type date),
    AddedYearMonthSpore = Table.AddColumn(AddedDaySpore, "yearMonth", each Date.ToText([day], "yyyy-MM"), type text),
    AddedDoencaSpore = Table.AddColumn(AddedYearMonthSpore, "doenca", each
        if [displayName] = "General Alternaria" then "Mancha de Alternaria"
        else if [displayName] = "General Rust" then "Ferrugem do Milho"
        else if [displayName] = "Target Spot" then "Mancha Alvo"
        else if [displayName] = "Powdery Mildew" then "Oidio"
        else if [displayName] = "Moniliophthora spp. BETA" then "Moniliophthora"
        else if [displayName] = "Soybean Rust" then "Ferrugem da Soja"
        else if [displayName] = "Anthracnose" then "Antracnose"
        else if [displayName] = "Dry rot" then "Fusarium"
        else if [displayName] = "Septoria" then "Septoriose"
        else [displayName], type text),
    AddedStatusSpore = Table.AddColumn(AddedDoencaSpore, "status", each if [dangerConcentrationThreshold] > 0 and [concentration] >= [dangerConcentrationThreshold] then "Perigo" else if [warningConcentrationThreshold] > 0 and [concentration] >= [warningConcentrationThreshold] then "Atencao" else "Normal", type text),

    SourceWeather = Csv.Document(File.Contents("$dataDir\weather.csv"),[Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    PromotedWeather = Table.PromoteHeaders(SourceWeather, [PromoteAllScalars=true]),
    TypedWeather = Table.TransformColumnTypes(PromotedWeather,{{"deviceUserFriendlyId", type text}, {"temperature", type number}, {"humidity", type number}, {"rainFall", type number}, {"dateMeasured", type datetimezone}}),
    AddedDayWeather = Table.AddColumn(TypedWeather, "day", each Date.From(DateTimeZone.RemoveZone([dateMeasured])), type date),
    GroupedWeather = Table.Group(AddedDayWeather, {"deviceUserFriendlyId", "day"}, {
        {"temperaturaMedia", each List.Average(List.RemoveNulls([temperature])), type nullable number},
        {"umidadeMedia", each List.Average(List.RemoveNulls([humidity])), type nullable number},
        {"chuvaTotal", each List.Sum(List.RemoveNulls([rainFall])), type nullable number}
    }),

    Joined = Table.NestedJoin(AddedStatusSpore, {"deviceUserFriendlyId", "day"}, GroupedWeather, {"deviceUserFriendlyId", "day"}, "Clima", JoinKind.LeftOuter),
    Expanded = Table.ExpandTableColumn(Joined, "Clima", {"temperaturaMedia", "umidadeMedia", "chuvaTotal"}, {"Temperatura", "Umidade", "QuantidadeChuva"}),
    Selected = Table.SelectColumns(Expanded, {"day","yearMonth","siteName","deviceUserFriendlyId","doenca","concentration","status","Temperatura","Umidade","QuantidadeChuva"}),
    Renamed = Table.RenameColumns(Selected, {
        {"day", "Data"},
        {"yearMonth", "Mes"},
        {"siteName", "Fazenda"},
        {"deviceUserFriendlyId", "Dispositivo"},
        {"doenca", "Doenca"},
        {"concentration", "Quantidade de Esporos"},
        {"status", "Status"}
    }),
    Sorted = Table.Sort(Renamed, {{"Data", Order.Descending},{"Fazenda", Order.Ascending},{"Doenca", Order.Ascending}})
in
    Sorted
"@

# Versoes das consultas que buscam direto na API do BioScout (em vez de ler os
# CSVs locais), para o botao "Atualizar Tudo" do Excel funcionar sozinho.
# Usuario/senha ficam em branco de proposito -- o usuario preenche uma vez no
# Editor do Power Query (Dados > Consultas e Conexoes > clique direito > Editar)
# antes do primeiro "Atualizar Tudo". So trocamos a formula da consulta DEPOIS
# que a planilha ja foi carregada com os dados do CSV (metodo comprovado nesta
# automacao); assim o arquivo abre com dados reais e fica pronto para atualizar
# direto na API quando o usuario configurar a credencial.
$doencaCaseWhen = @'
        if [displayName] = "General Alternaria" then "Mancha de Alternaria"
        else if [displayName] = "General Rust" then "Ferrugem do Milho"
        else if [displayName] = "Target Spot" then "Mancha Alvo"
        else if [displayName] = "Powdery Mildew" then "Oidio"
        else if [displayName] = "Moniliophthora spp. BETA" then "Moniliophthora"
        else if [displayName] = "Soybean Rust" then "Ferrugem da Soja"
        else if [displayName] = "Anthracnose" then "Antracnose"
        else if [displayName] = "Dry rot" then "Fusarium"
        else if [displayName] = "Septoria" then "Septoriose"
        else [displayName]
'@

$mAuthAndSitesApi = @'
    BioScoutUser = "",
    BioScoutPassword = "",
    LoginBody = Json.FromValue([UserName = BioScoutUser, Password = BioScoutPassword]),
    LoginResponse = Json.Document(Web.Contents("https://rest.bioscout.com.au/api/Auth/login", [Headers=[#"Content-Type"="application/json"], Content=LoginBody])),
    Token = LoginResponse[bearerToken],
    AuthHeader = [Authorization = "Bearer " & Token],
    SitesResponse = Json.Document(Web.Contents("https://rest.bioscout.com.au/api/Site/get?SiteRole=2&SiteRole=3&SiteRole=5&SiteRole=6", [Headers=AuthHeader])),
    SitesTable = Table.FromList(SitesResponse, Splitter.SplitByNothing(), null, null, ExtraValues.Error),
    SitesExpanded = Table.ExpandRecordColumn(SitesTable, "Column1", {"siteId", "siteName"}, {"siteId", "siteName"}),
    OneAgroSites = Table.SelectRows(SitesExpanded, each Text.StartsWith([siteName], "OneAgro")),
    SiteIdsList = List.Buffer(OneAgroSites[siteId]),
    FromDate = "2025-10-01T00:00:00Z",
    ToDate = DateTimeZone.ToText(DateTimeZone.UtcNow(), "yyyy-MM-ddTHH:mm:ssZ"),
'@

$mSporeCountsApi = @"
let
$mAuthAndSitesApi
    SiteIdsQs = Text.Combine(List.Transform(SiteIdsList, each "SiteIds=" & Text.From(_)), "&"),
    CountsUrl = "https://rest.bioscout.com.au/api/service-subscriptions/counts?From=" & FromDate & "&To=" & ToDate & "&" & SiteIdsQs,
    CountsResponse = Json.Document(Web.Contents(CountsUrl, [Headers=AuthHeader])),
    CountsTable = Table.FromList(CountsResponse, Splitter.SplitByNothing(), null, null, ExtraValues.Error),
    CountsExpanded = Table.ExpandRecordColumn(CountsTable, "Column1",
        {"siteName","displayName","deviceUserFriendlyId","scientificName","warningConcentrationThreshold","dangerConcentrationThreshold","samplingStartTime","concentration"},
        {"siteName","displayName","deviceUserFriendlyId","scientificName","warningConcentrationThreshold","dangerConcentrationThreshold","samplingStartTime","concentration"}),
    Typed = Table.TransformColumnTypes(CountsExpanded, {{"siteName", type text}, {"displayName", type text}, {"warningConcentrationThreshold", type number}, {"dangerConcentrationThreshold", type number}, {"samplingStartTime", type datetimezone}, {"concentration", type number}}),
    AddedDay = Table.AddColumn(Typed, "day", each Date.From(DateTimeZone.RemoveZone([samplingStartTime])), type date),
    AddedYearMonth = Table.AddColumn(AddedDay, "yearMonth", each Date.ToText([day], "yyyy-MM"), type text),
    AddedDoenca = Table.AddColumn(AddedYearMonth, "doenca", each
$doencaCaseWhen, type text)
in
    AddedDoenca
"@

$mWeatherApi = @"
let
$mAuthAndSitesApi
    FetchWeatherForSite = (siteId as number) as table =>
        let
            Url = "https://rest.bioscout.com.au/api/Weather/readings/sites?SiteId=" & Text.From(siteId) & "&StartDate=" & FromDate & "&EndDate=" & ToDate,
            Response = Json.Document(Web.Contents(Url, [Headers=AuthHeader])),
            Tbl = Table.FromList(Response, Splitter.SplitByNothing(), null, null, ExtraValues.Error),
            Expanded = Table.ExpandRecordColumn(Tbl, "Column1", {"deviceUserFriendlyId","dateMeasured","temperature","humidity","rainFall"}, {"deviceUserFriendlyId","dateMeasured","temperature","humidity","rainFall"})
        in
            Expanded,
    AllWeatherTables = List.Transform(SiteIdsList, FetchWeatherForSite),
    Combined = Table.Combine(AllWeatherTables),
    Typed = Table.TransformColumnTypes(Combined, {{"deviceUserFriendlyId", type text}, {"temperature", type number}, {"humidity", type number}, {"rainFall", type number}, {"dateMeasured", type datetimezone}}),
    AddedDay = Table.AddColumn(Typed, "day", each Date.From(DateTimeZone.RemoveZone([dateMeasured])), type date),
    Grouped = Table.Group(AddedDay, {"deviceUserFriendlyId", "day"}, {
        {"temperaturaMedia", each List.Average(List.RemoveNulls([temperature])), type nullable number},
        {"umidadeMedia", each List.Average(List.RemoveNulls([humidity])), type nullable number},
        {"chuvaTotal", each List.Sum(List.RemoveNulls([rainFall])), type nullable number}
    })
in
    Grouped
"@

$mBaseDadosApi = @"
let
    Result =
        let
$mAuthAndSitesApi
    SiteIdsQs = Text.Combine(List.Transform(SiteIdsList, each "SiteIds=" & Text.From(_)), "&"),
    CountsUrl = "https://rest.bioscout.com.au/api/service-subscriptions/counts?From=" & FromDate & "&To=" & ToDate & "&" & SiteIdsQs,
    CountsResponse = Json.Document(Web.Contents(CountsUrl, [Headers=AuthHeader])),
    CountsTable = Table.FromList(CountsResponse, Splitter.SplitByNothing(), null, null, ExtraValues.Error),
    CountsExpanded = Table.ExpandRecordColumn(CountsTable, "Column1",
        {"siteName","displayName","deviceUserFriendlyId","scientificName","warningConcentrationThreshold","dangerConcentrationThreshold","samplingStartTime","concentration"},
        {"siteName","displayName","deviceUserFriendlyId","scientificName","warningConcentrationThreshold","dangerConcentrationThreshold","samplingStartTime","concentration"}),
    TypedSpore = Table.TransformColumnTypes(CountsExpanded, {{"siteName", type text}, {"displayName", type text}, {"deviceUserFriendlyId", type text}, {"warningConcentrationThreshold", type number}, {"dangerConcentrationThreshold", type number}, {"samplingStartTime", type datetimezone}, {"concentration", type number}}),
    AddedDaySpore = Table.AddColumn(TypedSpore, "day", each Date.From(DateTimeZone.RemoveZone([samplingStartTime])), type date),
    AddedYearMonthSpore = Table.AddColumn(AddedDaySpore, "yearMonth", each Date.ToText([day], "yyyy-MM"), type text),
    AddedDoencaSpore = Table.AddColumn(AddedYearMonthSpore, "doenca", each
$doencaCaseWhen, type text),
    AddedStatusSpore = Table.AddColumn(AddedDoencaSpore, "status", each if [dangerConcentrationThreshold] > 0 and [concentration] >= [dangerConcentrationThreshold] then "Perigo" else if [warningConcentrationThreshold] > 0 and [concentration] >= [warningConcentrationThreshold] then "Atencao" else "Normal", type text),

    FetchWeatherForSite = (siteId as number, hdr as record, fD as text, tD as text) as table =>
        let
            Url = "https://rest.bioscout.com.au/api/Weather/readings/sites?SiteId=" & Text.From(siteId) & "&StartDate=" & fD & "&EndDate=" & tD,
            Response = Json.Document(Web.Contents(Url, [Headers=hdr])),
            Tbl = Table.FromList(Response, Splitter.SplitByNothing(), null, null, ExtraValues.Error),
            Expanded = Table.ExpandRecordColumn(Tbl, "Column1", {"deviceUserFriendlyId","dateMeasured","temperature","humidity","rainFall"}, {"deviceUserFriendlyId","dateMeasured","temperature","humidity","rainFall"})
        in
            Expanded,
    AllWeatherTables = List.Transform(SiteIdsList, each FetchWeatherForSite(_, AuthHeader, FromDate, ToDate)),
    CombinedWeather = Table.Combine(AllWeatherTables),
    TypedWeather = Table.TransformColumnTypes(CombinedWeather, {{"deviceUserFriendlyId", type text}, {"temperature", type number}, {"humidity", type number}, {"rainFall", type number}, {"dateMeasured", type datetimezone}}),
    AddedDayWeather = Table.AddColumn(TypedWeather, "day", each Date.From(DateTimeZone.RemoveZone([dateMeasured])), type date),
    GroupedWeather = Table.Group(AddedDayWeather, {"deviceUserFriendlyId", "day"}, {
        {"temperaturaMedia", each List.Average(List.RemoveNulls([temperature])), type nullable number},
        {"umidadeMedia", each List.Average(List.RemoveNulls([humidity])), type nullable number},
        {"chuvaTotal", each List.Sum(List.RemoveNulls([rainFall])), type nullable number}
    }),

    Joined = Table.NestedJoin(AddedStatusSpore, {"deviceUserFriendlyId", "day"}, GroupedWeather, {"deviceUserFriendlyId", "day"}, "Clima", JoinKind.LeftOuter),
    Expanded2 = Table.ExpandTableColumn(Joined, "Clima", {"temperaturaMedia", "umidadeMedia", "chuvaTotal"}, {"Temperatura", "Umidade", "QuantidadeChuva"}),
    Selected = Table.SelectColumns(Expanded2, {"day","yearMonth","siteName","deviceUserFriendlyId","doenca","concentration","status","Temperatura","Umidade","QuantidadeChuva"}),
    Renamed = Table.RenameColumns(Selected, {
        {"day", "Data"},
        {"yearMonth", "Mes"},
        {"siteName", "Fazenda"},
        {"deviceUserFriendlyId", "Dispositivo"},
        {"doenca", "Doenca"},
        {"concentration", "Quantidade de Esporos"},
        {"status", "Status"}
    }),
    Sorted = Table.Sort(Renamed, {{"Data", Order.Descending},{"Fazenda", Order.Ascending},{"Doenca", Order.Ascending}})
        in
            Sorted
in
    Result
"@

Invoke-Phase -Name "FASE 1/5: esporos + pivot + grafico + slicers" -ExpectedSheets @("Data_SporeCounts", "PivotData", "Dashboard") -Verify {
    param($checkWb)
    return $checkWb.SlicerCaches.Count -ge 3 -and $checkWb.Worksheets.Item("Dashboard").ChartObjects().Count -ge 1
} -Body {
    param($excel, $wbRef)
    $wb = $excel.Workbooks.Add()
    try { $wb.AutoSaveOn = $false } catch {}
    $wbRef.Value = $wb

    $wb.Queries.Add("SporeCounts", $mSporeCounts) | Out-Null

    $wsData = $wb.Worksheets.Item(1)
    $wsData.Name = "Data_SporeCounts"
    $loData = Load-Query -ws $wsData -queryName "SporeCounts"
    Write-Host "  Data_SporeCounts: $($loData.ListRows.Count) linhas"

    $wsPivot = $wb.Worksheets.Add()
    $wsPivot.Name = "PivotData"
    $pc = $wb.PivotCaches().Create(1, $loData.Range)
    $pt = $pc.CreatePivotTable($wsPivot.Range("A3"), "PT_Trend")
    $pt.PivotFields("day").Orientation = 1
    $pt.PivotFields("doenca").Orientation = 2
    $pt.AddDataField($pt.PivotFields("concentration"), "Media Concentracao", -4106) | Out-Null

    $wsDash = $wb.Worksheets.Add()
    $wsDash.Name = "Dashboard"

    Add-Banner -ws $wsDash -Title "BioScout | Monitoramento de Esporos" `
        -Subtitle "Periodo: $dataInicio a $dataFim  |  Atualizado em $geradoEm  |  $kpiSites fazendas no Brasil"

    Add-KpiCard -ws $wsDash -Row 5 -ColStart 1 -ColSpan 2 -Value $kpiSites -Label "FAZENDAS MONITORADAS" -BgColor $colorCardBg -NumColor $colorCardNum
    Add-KpiCard -ws $wsDash -Row 5 -ColStart 3 -ColSpan 2 -Value $kpiDiseases -Label "DOENCAS ACOMPANHADAS" -BgColor $colorCardBg -NumColor $colorCardNum
    Add-KpiCard -ws $wsDash -Row 5 -ColStart 5 -ColSpan 2 -Value $kpiReadings -Label "LEITURAS NO PERIODO" -BgColor $colorCardBg -NumColor $colorCardNum
    Add-KpiCard -ws $wsDash -Row 5 -ColStart 7 -ColSpan 2 -Value $kpiPerigo -Label "ALERTAS - PERIGO AGORA" -BgColor $colorCardBg -NumColor $colorCardDanger
    Add-KpiCard -ws $wsDash -Row 5 -ColStart 9 -ColSpan 2 -Value $kpiAtencao -Label "ALERTAS - ATENCAO AGORA" -BgColor $colorCardBg -NumColor $colorCardWarn

    $chartObj = $wsDash.ChartObjects().Add(230, 190, 830, 460)
    $chartObj.Chart.SetSourceData($pt.TableRange1)
    $chartObj.Chart.ChartType = 4
    $chartObj.Chart.ChartStyle = 227
    $chartObj.Chart.HasTitle = $true
    $chartObj.Chart.ChartTitle.Text = "Concentracao de esporos ao longo do tempo (media diaria, por doenca)"

    $scSite = $wb.SlicerCaches.Add2($pt, "siteName")
    $sl1 = $scSite.Slicers.Add($wsDash, $missing, "Slicer_Site", "Fazenda", 190, 10, 210, 170)
    try { $sl1.Style = "SlicerStyleLight6" } catch {}
    $scDisease = $wb.SlicerCaches.Add2($pt, "doenca")
    $sl2 = $scDisease.Slicers.Add($wsDash, $missing, "Slicer_Disease", "Doenca", 370, 10, 210, 170)
    try { $sl2.Style = "SlicerStyleLight6" } catch {}
    $scMonth = $wb.SlicerCaches.Add2($pt, "yearMonth")
    $sl3 = $scMonth.Slicers.Add($wsDash, $missing, "Slicer_Month", "Mes/Ano", 550, 10, 210, 150)
    try { $sl3.Style = "SlicerStyleLight6" } catch {}

    $wsPivot.Visible = 0

    $wb.SaveAs($outputPath, 51)
}

Invoke-Phase -Name "FASE 2/5: tabela de status atual" -ExpectedSheets @("Data_Status") -Body {
    param($excel, $wbRef)
    $wb = $excel.Workbooks.Open($outputPath)
    try { $wb.AutoSaveOn = $false } catch {}
    $wbRef.Value = $wb

    Add-QuerySafe -wb $wb -Name "LatestStatus" -Formula $mStatus

    $wsStatus = Add-WorksheetSafe -wb $wb -Name "Data_Status"
    $loStatus = Load-Query -ws $wsStatus -queryName "LatestStatus"
    Write-Host "  Data_Status: $($loStatus.ListRows.Count) linhas"

    $statusRange = $loStatus.ListColumns.Item("status").DataBodyRange
    Add-StatusColors -range $statusRange -missing $missing

    $wb.Save()
}

Invoke-Phase -Name "FASE 3/5: clima (agregado diario)" -ExpectedSheets @("Data_Weather") -Body {
    param($excel, $wbRef)
    $wb = $excel.Workbooks.Open($outputPath)
    try { $wb.AutoSaveOn = $false } catch {}
    $wbRef.Value = $wb

    Add-QuerySafe -wb $wb -Name "Weather" -Formula $mWeather

    $wsWeather = Add-WorksheetSafe -wb $wb -Name "Data_Weather"
    $loWeather = Load-Query -ws $wsWeather -queryName "Weather"
    Write-Host "  Data_Weather: $($loWeather.ListRows.Count) linhas"

    $wb.Save()
}

Invoke-Phase -Name "FASE 4/5: base de dados combinada + analise cruzada" -ExpectedSheets @("BaseDados", "PivotData2", "Analise Cruzada") -Verify {
    param($checkWb)
    try { $checkWb.Worksheets.Item("BaseDados").ListObjects.Item("TblBaseDados") | Out-Null } catch { return $false }
    return $checkWb.SlicerCaches.Count -ge 7 -and $checkWb.Worksheets.Item("Analise Cruzada").ChartObjects().Count -ge 1
} -Body {
    param($excel, $wbRef)
    $wb = $excel.Workbooks.Open($outputPath)
    try { $wb.AutoSaveOn = $false } catch {}
    $wbRef.Value = $wb

    Add-QuerySafe -wb $wb -Name "BaseDados" -Formula $mBaseDados

    $wsBase = Add-WorksheetSafe -wb $wb -Name "BaseDados"
    $loBase = Load-Query -ws $wsBase -queryName "BaseDados"
    $loBase.Name = "TblBaseDados"
    Write-Host "  BaseDados: $($loBase.ListRows.Count) linhas (tabela TblBaseDados)"
    Add-StatusColors -range $loBase.ListColumns.Item("Status").DataBodyRange -missing $missing

    $wsPivot2 = Add-WorksheetSafe -wb $wb -Name "PivotData2"
    $pc2 = $wb.PivotCaches().Create(1, $loBase.Range)
    $pt2 = $pc2.CreatePivotTable($wsPivot2.Range("A3"), "PT_Cruzado")
    $pt2.PivotFields("Mes").Orientation = 1
    $pt2.PivotFields("Data").Orientation = 1
    $pt2.AddDataField($pt2.PivotFields("Quantidade de Esporos"), "Esporos (media)", -4106) | Out-Null
    $pt2.AddDataField($pt2.PivotFields("Umidade"), "Umidade % (media)", -4106) | Out-Null
    $pt2.AddDataField($pt2.PivotFields("QuantidadeChuva"), "Chuva mm (soma)", -4157) | Out-Null
    Write-Host "  chk: pivot cruzado ok"

    $wsCross = Add-WorksheetSafe -wb $wb -Name "Analise Cruzada"
    Write-Host "  chk: aba criada"

    Add-Banner -ws $wsCross -Title "BioScout | Analise Cruzada: Esporos x Clima" `
        -Subtitle "Chuva acumulada no periodo: $kpiChuvaTotal mm  |  Umidade media: $kpiUmidadeMedia%  |  Atualizado em $geradoEm"
    Write-Host "  chk: banner ok"

    Add-KpiCard -ws $wsCross -Row 5 -ColStart 1 -ColSpan 2 -Value $kpiReadings -Label "LEITURAS DE ESPOROS" -BgColor $colorCardBg -NumColor $colorCardNum
    Write-Host "  chk: kpi1 ok"
    Add-KpiCard -ws $wsCross -Row 5 -ColStart 3 -ColSpan 2 -Value "$kpiChuvaTotal mm" -Label "CHUVA ACUMULADA" -BgColor $colorCardBg -NumColor $colorCardNum
    Write-Host "  chk: kpi2 ok"
    Add-KpiCard -ws $wsCross -Row 5 -ColStart 5 -ColSpan 2 -Value "$kpiUmidadeMedia%" -Label "UMIDADE MEDIA" -BgColor $colorCardBg -NumColor $colorCardNum
    Write-Host "  chk: kpi3 ok"
    Add-KpiCard -ws $wsCross -Row 5 -ColStart 7 -ColSpan 2 -Value $kpiPerigo -Label "ALERTAS - PERIGO AGORA" -BgColor $colorCardBg -NumColor $colorCardDanger
    Write-Host "  chk: kpi4 ok"
    Add-KpiCard -ws $wsCross -Row 5 -ColStart 9 -ColSpan 2 -Value $kpiAtencao -Label "ALERTAS - ATENCAO AGORA" -BgColor $colorCardBg -NumColor $colorCardWarn
    Write-Host "  chk: kpi5 ok"

    $chartObj2 = $wsCross.ChartObjects().Add(230, 190, 900, 460)
    $chartObj2.Chart.SetSourceData($pt2.TableRange1)
    $chartObj2.Chart.ChartType = 4
    $chartObj2.Chart.ChartStyle = 227
    $chartObj2.Chart.HasTitle = $true
    $chartObj2.Chart.ChartTitle.Text = "Esporos, Umidade e Chuva por dia (use os filtros para focar numa fazenda/doenca/mes)"
    Write-Host "  chk: grafico ok"

    foreach ($series in $chartObj2.Chart.SeriesCollection()) {
        if ($series.Name -like "*Chuva*") {
            $series.AxisGroup = 2
            $series.ChartType = 51
        }
    }
    Write-Host "  chk: eixo secundario ok"

    $sc1 = $wb.SlicerCaches.Add2($pt2, "Fazenda")
    $sl4 = $sc1.Slicers.Add($wsCross, $missing, "Slicer_Fazenda2", "Fazenda", 190, 10, 210, 130)
    try { $sl4.Style = "SlicerStyleLight6" } catch {}
    Write-Host "  chk: slicer fazenda ok"
    $sc2 = $wb.SlicerCaches.Add2($pt2, "Doenca")
    $sl5 = $sc2.Slicers.Add($wsCross, $missing, "Slicer_Doenca2", "Doenca", 330, 10, 210, 130)
    try { $sl5.Style = "SlicerStyleLight6" } catch {}
    Write-Host "  chk: slicer doenca ok"
    $sc3 = $wb.SlicerCaches.Add2($pt2, "Status")
    $sl6 = $sc3.Slicers.Add($wsCross, $missing, "Slicer_Status2", "Status", 470, 10, 210, 110)
    try { $sl6.Style = "SlicerStyleLight6" } catch {}
    Write-Host "  chk: slicer status ok"
    $sc4 = $wb.SlicerCaches.Add2($pt2, "Mes")
    $sl7 = $sc4.Slicers.Add($wsCross, $missing, "Slicer_Mes2", "Mes/Ano", 590, 10, 210, 150)
    try { $sl7.Style = "SlicerStyleLight6" } catch {}
    Write-Host "  chk: slicer mes ok"

    $wsPivot2.Visible = 0

    $wb.Save()
}

Invoke-Phase -Name "FASE 5/5: alertas do dia" -ExpectedSheets @("Alertas do Dia") -Verify {
    param($checkWb)
    try { $checkWb.Names.Item("DataSelecionada") | Out-Null } catch { return $false }
    return $true
} -Body {
    param($excel, $wbRef)
    $wb = $excel.Workbooks.Open($outputPath)
    try { $wb.AutoSaveOn = $false } catch {}
    $wbRef.Value = $wb

    $wsAlert = Add-WorksheetSafe -wb $wb -Name "Alertas do Dia"
    Add-Banner -ws $wsAlert -Title "BioScout | Alertas do Dia" `
        -Subtitle "Escolha uma data na caixa abaixo para ver o status, a umidade e a chuva daquele dia" -WidthCols 12

    $lblData = $wsAlert.Cells.Item(5, 1)
    $lblData.Value2 = "Data selecionada:"
    $lblData.Font.Bold = $true
    $lblData.Font.Name = "Segoe UI"
    $lblData.Font.Size = 11

    $dateCell = $wsAlert.Cells.Item(5, 2)
    $dateCell.NumberFormat = "dd/mm/aaaa"
    $dateCell.Value2 = $defaultAlertDate.ToOADate()
    $dateCell.Font.Bold = $true
    $dateCell.Font.Size = 12
    $dateCell.Font.Name = "Segoe UI"
    $dateCell.Interior.Color = $colorSubtitle
    $dateCell.HorizontalAlignment = -4108
    $wsAlert.Rows.Item(5).RowHeight = 22

    # Lista de datas disponiveis (coluna oculta, usada como fonte da caixa suspensa)
    $dateListCol = 20
    for ($i = 0; $i -lt $uniqueDatesDesc.Count; $i++) {
        $c = $wsAlert.Cells.Item($i + 1, $dateListCol)
        $c.NumberFormat = "dd/mm/aaaa"
        $c.Value2 = $uniqueDatesDesc[$i].ToOADate()
    }
    $wsAlert.Columns.Item($dateListCol).Hidden = $true

    $validationRange = "='Alertas do Dia'!`$T`$1:`$T`$$($uniqueDatesDesc.Count)"
    try { $dateCell.Validation.Delete() } catch {}
    $dateCell.Validation.Add(3, 1, 1, $validationRange) | Out-Null
    $dateCell.Validation.IgnoreBlank = $true
    $dateCell.Validation.InCellDropdown = $true

    try { $wb.Names.Item("DataSelecionada").Delete() } catch {}
    $dateCell.Name = "DataSelecionada"
    Write-Host "  seletor de data criado ($($uniqueDatesDesc.Count) datas disponiveis, padrao $($defaultAlertDate.ToString('dd/MM/yyyy')))"

    $baseMaxRow = $kpiReadings + 500
    $helperCol = 25
    $wsAlert.Range($wsAlert.Cells.Item(1, $helperCol), $wsAlert.Cells.Item(1, $helperCol + 5)).EntireColumn.Hidden = $true

    $rowCursor = 8
    $sitesOrdered = $comboRows | Select-Object -ExpandProperty Fazenda -Unique | Sort-Object

    foreach ($site in $sitesOrdered) {
        $siteCombos = $comboRows | Where-Object { $_.Fazenda -eq $site } | Sort-Object Doenca
        $siteEsc = $site -replace '"', '""'

        $hdr = $wsAlert.Range($wsAlert.Cells.Item($rowCursor, 1), $wsAlert.Cells.Item($rowCursor, 12))
        $hdr.Merge() | Out-Null
        $hdr.Interior.Color = $colorSubtitle
        $hdr.Font.Bold = $true
        $hdr.Font.Size = 13
        $hdr.Font.Name = "Segoe UI"
        $hdr.Font.Color = $colorBanner
        $hdr.HorizontalAlignment = -4131
        $hdr.IndentLevel = 1
        $hdr.NumberFormat = "@"
        $hdr.Cells.Item(1, 1).Value2 = [string]$site
        $wsAlert.Rows.Item($rowCursor).RowHeight = 24
        $rowCursor += 2

        $col = 1
        foreach ($combo in $siteCombos) {
            $doencaEsc = $combo.Doenca -replace '"', '""'

            $valorFormula = "=IFERROR(ROUND(AVERAGEIFS(TblBaseDados[Quantidade de Esporos],TblBaseDados[Fazenda],`"$siteEsc`",TblBaseDados[Doenca],`"$doencaEsc`",TblBaseDados[Data],DataSelecionada),1),`"-`")"
            $climaFormula = "=`"Umidade: `"&IFERROR(TEXT(AVERAGEIFS(TblBaseDados[Umidade],TblBaseDados[Fazenda],`"$siteEsc`",TblBaseDados[Doenca],`"$doencaEsc`",TblBaseDados[Data],DataSelecionada),`"0`"),`"-`")&`"%   Chuva: `"&IFERROR(TEXT(AVERAGEIFS(TblBaseDados[QuantidadeChuva],TblBaseDados[Fazenda],`"$siteEsc`",TblBaseDados[Doenca],`"$doencaEsc`",TblBaseDados[Data],DataSelecionada),`"0,0`"),`"-`")&`" mm`""

            Add-DiseaseCardLive -ws $wsAlert -Row $rowCursor -Col $col -ValorFormula $valorFormula -Doenca $combo.Doenca `
                -Cientifico $combo.Cientifico -ClimaFormula $climaFormula -Site $siteEsc -DoencaLit $doencaEsc -missing $missing -BaseMaxRow $baseMaxRow -HelperCol $helperCol

            $col += 2
            if ($col -gt 11) { $col = 1; $rowCursor += 5 }
        }
        if ($col -ne 1) { $rowCursor += 5 }
        $rowCursor += 1
    }

    # So agora, com todas as fases ja carregadas com os dados do CSV, trocamos a
    # "receita" das consultas para a versao ligada na API do BioScout. Fazer isso
    # antes (ex.: logo apos a FASE 1) quebra fases posteriores que dependem dessas
    # consultas por nome (ex.: LatestStatus depende de SporeCounts) -- ao reabrir
    # o arquivo, o Power Query tentaria reavaliar a consulta ja trocada e bateria
    # no bloqueio de Privacidade de Dados do Excel antes mesmo de a planilha estar
    # pronta. Os dados ja carregados nas tabelas nao sao afetados por essa troca.
    $wb.Queries.Item("SporeCounts").Formula = $mSporeCountsApi
    $wb.Queries.Item("Weather").Formula = $mWeatherApi
    $wb.Queries.Item("BaseDados").Formula = $mBaseDadosApi

    $wb.Save()
}

Write-Host ""
Write-Host "Planilha criada: $outputPath" -ForegroundColor Green
