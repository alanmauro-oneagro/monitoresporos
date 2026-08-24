<#
Le os CSVs em .\data e gera um relatorio HTML com:
- Tabela de ultima leitura por site/doenca, com status (Normal/Atencao/Perigo)
- Graficos de tendencia de concentracao de esporos por site

Parametros opcionais:
  -Sites      lista de siteName para filtrar (padrao: todos)
  -OutputPath caminho do HTML gerado (padrao: .\report.html)
#>
param(
    [string[]]$Sites = $null,
    [string]$OutputPath = $null
)

$root = $PSScriptRoot
$dataDir = Join-Path $root 'data'
$countsPath = Join-Path $dataDir 'spore_counts.csv'

if (-not (Test-Path $countsPath)) {
    throw "Nao ha dados em $countsPath. Rode Fetch-BioScoutData.ps1 primeiro."
}

$counts = Import-Csv $countsPath | ForEach-Object {
    $_.concentration = [double]$_.concentration
    $_.warningConcentrationThreshold = [double]$_.warningConcentrationThreshold
    $_.dangerConcentrationThreshold = [double]$_.dangerConcentrationThreshold
    $_
}

$sites = $counts | Select-Object -ExpandProperty siteName -Unique | Sort-Object
if ($Sites) {
    $sites = $sites | Where-Object { $Sites -contains $_ }
}

# ---- Tabela de status (leitura mais recente por site + doenca) ----
$latestRows = @()
foreach ($site in $sites) {
    $siteRows = $counts | Where-Object { $_.siteName -eq $site }
    $particulates = $siteRows | Select-Object -ExpandProperty displayName -Unique
    foreach ($p in $particulates) {
        $prows = $siteRows | Where-Object { $_.displayName -eq $p } | Sort-Object samplingStartTime
        $last = $prows[-1]
        $status = 'Normal'
        if ($last.concentration -ge $last.dangerConcentrationThreshold -and $last.dangerConcentrationThreshold -gt 0) {
            $status = 'Perigo'
        } elseif ($last.concentration -ge $last.warningConcentrationThreshold -and $last.warningConcentrationThreshold -gt 0) {
            $status = 'Atencao'
        }
        $latestRows += [PSCustomObject]@{
            site          = $site
            doenca        = $p
            concentracao  = [math]::Round($last.concentration, 1)
            atualizado    = $last.samplingStartTime
            status        = $status
        }
    }
}

# ---- Dados para os graficos (series por site/doenca) ----
$chartData = [ordered]@{}
foreach ($site in $sites) {
    $siteRows = $counts | Where-Object { $_.siteName -eq $site }
    $particulates = $siteRows | Select-Object -ExpandProperty displayName -Unique
    $series = @()
    foreach ($p in $particulates) {
        $prows = $siteRows | Where-Object { $_.displayName -eq $p } | Sort-Object samplingStartTime
        $series += [PSCustomObject]@{
            name   = $p
            points = $prows | ForEach-Object { [PSCustomObject]@{ x = $_.samplingStartTime; y = [math]::Round($_.concentration, 2) } }
        }
    }
    $chartData[$site] = $series
}

$chartJson = $chartData | ConvertTo-Json -Depth 8 -Compress

$statusRowsHtml = ($latestRows | Sort-Object site, doenca | ForEach-Object {
    $cls = switch ($_.status) { 'Perigo' { 'st-danger' } 'Atencao' { 'st-warn' } default { 'st-ok' } }
    "<tr><td>$($_.site)</td><td>$($_.doenca)</td><td>$($_.concentracao)</td><td>$($_.atualizado)</td><td class='$cls'>$($_.status)</td></tr>"
}) -join "`n"

$siteNamesJson = ($sites | ConvertTo-Json -Compress)

$html = @"
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>BioScout - Monitoramento de Esporos</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body { font-family: Arial, Helvetica, sans-serif; margin: 24px; background: #0b1220; color: #e6e9ef; }
  h1 { margin-bottom: 4px; }
  .meta { color: #9aa4b2; margin-bottom: 24px; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 40px; }
  th, td { padding: 6px 10px; border-bottom: 1px solid #26324a; text-align: left; }
  th { color: #9aa4b2; font-weight: 600; }
  .st-ok { color: #51cf66; font-weight: 600; }
  .st-warn { color: #ffd43b; font-weight: 600; }
  .st-danger { color: #ff6b6b; font-weight: 600; }
  canvas { background: #111a2b; border-radius: 8px; padding: 12px; margin-bottom: 40px; max-width: 100%; }
  h2 { border-bottom: 1px solid #26324a; padding-bottom: 6px; margin-top: 40px; }
</style>
</head>
<body>
<h1>BioScout - Monitoramento de Esporos</h1>
<p class="meta">Gerado em $(Get-Date -Format 'dd/MM/yyyy HH:mm')</p>

<h2>Situacao atual (ultima leitura por site e doenca)</h2>
<table>
<tr><th>Site</th><th>Doenca</th><th>Concentracao (esporos/m3)</th><th>Atualizado em</th><th>Status</th></tr>
$statusRowsHtml
</table>

<div id="charts"></div>

<script>
const data = $chartJson;
const siteOrder = $siteNamesJson;
const container = document.getElementById('charts');
const colors = ['#4dabf7','#ff922b','#51cf66','#ff6b6b','#845ef7','#ffd43b','#20c997','#f06595','#adb5bd'];

siteOrder.forEach(site => {
  const series = data[site];
  if (!series) return;
  const h = document.createElement('h2');
  h.textContent = site;
  container.appendChild(h);
  const canvas = document.createElement('canvas');
  canvas.height = 300;
  container.appendChild(canvas);

  const datasets = series.map((s, i) => ({
    label: s.name,
    data: s.points,
    borderColor: colors[i % colors.length],
    fill: false,
    tension: 0.2,
    pointRadius: 1,
    borderWidth: 2
  }));

  new Chart(canvas, {
    type: 'line',
    data: { datasets },
    options: {
      parsing: { xAxisKey: 'x', yAxisKey: 'y' },
      scales: {
        x: { type: 'category', ticks: { color: '#9aa4b2', maxTicksLimit: 12 } },
        y: { title: { display: true, text: 'Esporos / m3', color: '#9aa4b2' }, ticks: { color: '#9aa4b2' } }
      },
      plugins: { legend: { labels: { color: '#e6e9ef' } } }
    }
  });
});
</script>
</body>
</html>
"@

$reportPath = if ($OutputPath) { $OutputPath } else { Join-Path $root 'report.html' }
$html | Set-Content $reportPath -Encoding UTF8
Write-Host "Relatorio gerado: $reportPath" -ForegroundColor Green
