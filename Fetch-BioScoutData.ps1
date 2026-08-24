<#
Busca dados do BioScout (contagem de esporos, clima, spraylogs, relatorios de site)
desde $SinceDate ate hoje, e acumula em arquivos CSV dentro de .\data.
Roda de forma incremental: em execucoes seguintes, so refaz o mes atual
(que pode ter sido atualizado) e busca meses novos.
#>
param(
    [string]$SinceDate = '2025-10-01',
    [switch]$SkipExtras   # pula spraylogs/relatorios de site -- usado pelo botao "Atualizar dados" do painel web, que so precisa de esporos+clima e fica bem mais rapido assim
)

$ErrorActionPreference = 'Stop'

$root     = $PSScriptRoot
$dataDir  = Join-Path $root 'data'
$credPath = Join-Path $root 'bioscout_cred.xml'
$statePath = Join-Path $dataDir 'sync_state.json'
$ApiBase  = 'https://rest.bioscout.com.au'

if (-not (Test-Path $dataDir)) { New-Item -ItemType Directory -Path $dataDir | Out-Null }

if (-not (Test-Path $credPath)) {
    throw "Credenciais nao encontradas em $credPath. Rode Setup-Credentials.ps1 primeiro."
}
$cred = Import-Clixml -Path $credPath

function Find-JwtToken {
    param($obj)
    if ($null -eq $obj) { return $null }
    if ($obj -is [string]) {
        if ($obj -match '^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$') { return $obj }
        return $null
    }
    if ($obj -is [System.Collections.IEnumerable]) {
        foreach ($item in $obj) {
            $found = Find-JwtToken $item
            if ($found) { return $found }
        }
        return $null
    }
    if ($obj -is [System.Management.Automation.PSCustomObject]) {
        foreach ($prop in $obj.PSObject.Properties) {
            $found = Find-JwtToken $prop.Value
            if ($found) { return $found }
        }
    }
    return $null
}

function Get-AuthToken {
    $plainPassword = $cred.GetNetworkCredential().Password
    $body = @{ UserName = $cred.UserName; Password = $plainPassword } | ConvertTo-Json
    $resp = Invoke-RestMethod -Uri "$ApiBase/api/Auth/login" -Method Post -Body $body -ContentType 'application/json'
    $token = Find-JwtToken $resp
    if (-not $token) { throw "Nao foi possivel extrair o token de autenticacao da resposta de login." }
    return $token
}

function Get-RowKey {
    param($row, [string[]]$KeyProps)
    $names = $row.PSObject.Properties.Name
    $missing = $false
    $vals = foreach ($k in $KeyProps) {
        if ($names -contains $k -and $null -ne $row.$k -and $row.$k -ne '') {
            $row.$k
        } else {
            $missing = $true
        }
    }
    if ($missing) {
        return ($row | ConvertTo-Json -Compress -Depth 5)
    }
    return ($vals -join '|')
}

function Merge-Csv {
    param($NewRows, [string]$Path, [string[]]$KeyProps)
    if (-not $NewRows -or $NewRows.Count -eq 0) { return }
    $map = [ordered]@{}
    if (Test-Path $Path) {
        foreach ($row in (Import-Csv $Path)) {
            $map[(Get-RowKey $row $KeyProps)] = $row
        }
    }
    foreach ($row in $NewRows) {
        $map[(Get-RowKey $row $KeyProps)] = $row
    }
    $map.Values | Export-Csv $Path -NoTypeInformation -Encoding UTF8
}

Write-Host "Autenticando..."
$token = Get-AuthToken
$headers = @{ Authorization = "Bearer $token" }
Write-Host "OK."

if (Test-Path $statePath) {
    $state = Get-Content $statePath -Raw | ConvertFrom-Json
} else {
    $state = [PSCustomObject]@{ lastCompletedMonth = $null }
}

$allSites = Invoke-RestMethod -Uri "$ApiBase/api/Site/get?SiteRole=2&SiteRole=3&SiteRole=5&SiteRole=6" -Headers $headers -Method Get
$sites = $allSites | Where-Object { $_.siteName -like 'OneAgro*' }
$sites | Select-Object siteId, siteName | Export-Csv (Join-Path $dataDir 'sites.csv') -NoTypeInformation -Encoding UTF8
$siteIds = $sites | ForEach-Object { $_.siteId }
Write-Host "Sites OneAgro/Brasil: $($siteIds.Count) (de $($allSites.Count) totais na conta)"

Write-Host "Limpando dados de sites fora do escopo (OneAgro/Brasil)..."
$sporeCountsPath = Join-Path $dataDir 'spore_counts.csv'
$weatherPath = Join-Path $dataDir 'weather.csv'
$validSiteNames = $sites | ForEach-Object { $_.siteName }

if (Test-Path $sporeCountsPath) {
    $spore = Import-Csv $sporeCountsPath
    $before = $spore.Count
    $sporeFiltered = $spore | Where-Object { $validSiteNames -contains $_.siteName }
    if ($sporeFiltered.Count -lt $before) {
        $sporeFiltered | Export-Csv $sporeCountsPath -NoTypeInformation -Encoding UTF8
        Write-Host "  spore_counts.csv: $before -> $($sporeFiltered.Count) linhas"
    }
    $validDeviceIds = $sporeFiltered | Select-Object -ExpandProperty deviceUserFriendlyId -Unique
} else {
    $validDeviceIds = @()
}

if ((Test-Path $weatherPath) -and $validDeviceIds.Count -gt 0) {
    $weather = Import-Csv $weatherPath
    $before = $weather.Count
    $weatherFiltered = $weather | Where-Object { $validDeviceIds -contains $_.deviceUserFriendlyId }
    if ($weatherFiltered.Count -lt $before) {
        $weatherFiltered | Export-Csv $weatherPath -NoTypeInformation -Encoding UTF8
        Write-Host "  weather.csv: $before -> $($weatherFiltered.Count) linhas"
    }
}

$start = Get-Date $SinceDate
$end = Get-Date
$months = @()
$cursor = Get-Date -Year $start.Year -Month $start.Month -Day 1 -Hour 0 -Minute 0 -Second 0
while ($cursor -le $end) {
    $months += $cursor
    $cursor = $cursor.AddMonths(1)
}

$startIdx = 0
if ($state.lastCompletedMonth) {
    $lastCompleted = Get-Date $state.lastCompletedMonth
    for ($i = 0; $i -lt $months.Count; $i++) {
        if ($months[$i] -gt $lastCompleted) { $startIdx = $i; break }
        $startIdx = $i + 1
    }
}
$monthsToFetch = $months[$startIdx..($months.Count - 1)]
Write-Host "Meses a buscar: $($monthsToFetch.Count)"

foreach ($month in $monthsToFetch) {
    $monthStart = $month
    $monthEnd = $month.AddMonths(1)
    if ($monthEnd -gt $end) { $monthEnd = $end }
    $fromIso = $monthStart.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $toIso = $monthEnd.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')

    Write-Host "Mes $($monthStart.ToString('yyyy-MM')) ($fromIso -> $toIso)"

    try {
        $qs = ($siteIds | ForEach-Object { "SiteIds=$_" }) -join '&'
        $url = "$ApiBase/api/service-subscriptions/counts?From=$fromIso&To=$toIso&$qs"
        $counts = Invoke-RestMethod -Uri $url -Headers $headers -Method Get
        Merge-Csv -NewRows $counts -Path (Join-Path $dataDir 'spore_counts.csv') -KeyProps @('tapeScanId', 'particulateId')
        Write-Host "  contagem de esporos: $($counts.Count) registros"
    } catch {
        Write-Warning "  erro contagem de esporos: $_"
    }

    # Junta os dados de todos os sites em memoria e faz UM merge por mes
    # (em vez de reescrever o CSV inteiro a cada site, que e muito mais lento).
    $monthWeather = @()
    $monthSprayLogs = @()
    $monthSiteReports = @()

    foreach ($siteId in $siteIds) {
        try {
            $w = Invoke-RestMethod -Uri "$ApiBase/api/Weather/readings/sites?SiteId=$siteId&StartDate=$fromIso&EndDate=$toIso" -Headers $headers -Method Get
            if ($w) { $monthWeather += $w }
        } catch {
            Write-Warning "  erro clima site ${siteId}: $_"
        }

        if (-not $SkipExtras) {
            try {
                $pageNumber = 1
                do {
                    $sl = Invoke-RestMethod -Uri "$ApiBase/api/SprayLogs?SiteId=$siteId&SprayDateFrom=$fromIso&SprayDateTo=$toIso&PageSize=100&PageNumber=$pageNumber" -Headers $headers -Method Get
                    if ($sl.items -and $sl.items.Count -gt 0) { $monthSprayLogs += $sl.items }
                    $pageNumber++
                } while ($sl.hasNextPage)
            } catch {
                Write-Warning "  erro spraylogs site ${siteId}: $_"
            }

            try {
                $pageNumber = 1
                do {
                    $sr = Invoke-RestMethod -Uri "$ApiBase/api/SiteReport/list?SiteId=$siteId&ReportDateAfter=$fromIso&ReportDateBefore=$toIso&PageSize=100&PageNumber=$pageNumber" -Headers $headers -Method Get
                    if ($sr.items -and $sr.items.Count -gt 0) { $monthSiteReports += $sr.items }
                    $pageNumber++
                } while ($sr.hasNextPage)
            } catch {
                Write-Warning "  erro siteReports site ${siteId}: $_"
            }
        }
    }

    Merge-Csv -NewRows $monthWeather -Path (Join-Path $dataDir 'weather.csv') -KeyProps @('deviceId', 'dateMeasured')
    Write-Host "  clima: $($monthWeather.Count) registros"
    if (-not $SkipExtras) {
        Merge-Csv -NewRows $monthSprayLogs -Path (Join-Path $dataDir 'spray_logs.csv') -KeyProps @('id', 'sprayLogId')
        Merge-Csv -NewRows $monthSiteReports -Path (Join-Path $dataDir 'site_reports.csv') -KeyProps @('id', 'siteReportId')
    }

    if ($monthEnd -lt $end) {
        $state.lastCompletedMonth = $monthStart.ToString('yyyy-MM-dd')
        $state | ConvertTo-Json | Set-Content $statePath -Encoding UTF8
    }
}

Write-Host ""
Write-Host "Concluido. Dados em $dataDir" -ForegroundColor Green
