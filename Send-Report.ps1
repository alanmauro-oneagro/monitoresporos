<#
Envia por e-mail o resumo diario dos sites selecionados.
O corpo do e-mail traz uma tabela simples (compativel com clientes de email);
o relatorio completo com graficos vai em anexo (HTML), pois graficos em
JavaScript nao funcionam dentro do corpo de e-mails.
#>
param(
    [string]$ToAddress = 'alan.mauro@outlook.com',
    [string[]]$Sites = @(
        'OneAgro - 3 Irmaos',
        'OneAgro - Agricola Ritter',
        'OneAgro - Agua Azul',
        'OneAgro - Dallas',
        'OneAgro - Fazenda Alvorada',
        'OneAgro - Grupo PIVA',
        'OneAgro - K&S Agricola',
        'OneAgro - RIR Juina'
    )
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$dataDir = Join-Path $root 'data'
$countsPath = Join-Path $dataDir 'spore_counts.csv'
$credPath = Join-Path $root 'email_cred.xml'

if (-not (Test-Path $credPath)) {
    throw "Credenciais de e-mail nao encontradas em $credPath. Rode Setup-EmailCredentials.ps1 primeiro."
}
if (-not (Test-Path $countsPath)) {
    throw "Nao ha dados em $countsPath. Rode Fetch-BioScoutData.ps1 primeiro."
}

$cred = Import-Clixml -Path $credPath

# ---- Gera o relatorio completo (com graficos) filtrado, para anexar ----
$attachmentPath = Join-Path $root 'email_report.html'
& (Join-Path $root 'Build-Report.ps1') -Sites $Sites -OutputPath $attachmentPath

# ---- Monta tabela simples para o corpo do e-mail ----
$counts = Import-Csv $countsPath | Where-Object { $Sites -contains $_.siteName } | ForEach-Object {
    $_.concentration = [double]$_.concentration
    $_.warningConcentrationThreshold = [double]$_.warningConcentrationThreshold
    $_.dangerConcentrationThreshold = [double]$_.dangerConcentrationThreshold
    $_
}

$rowsHtml = ($Sites | ForEach-Object {
    $site = $_
    $siteRows = $counts | Where-Object { $_.siteName -eq $site }
    if (-not $siteRows) { return }
    $particulates = $siteRows | Select-Object -ExpandProperty displayName -Unique
    foreach ($p in $particulates) {
        $prows = $siteRows | Where-Object { $_.displayName -eq $p } | Sort-Object samplingStartTime
        $last = $prows[-1]
        $status = 'Normal'
        $color = '#2f9e44'
        if ($last.concentration -ge $last.dangerConcentrationThreshold -and $last.dangerConcentrationThreshold -gt 0) {
            $status = 'Perigo'; $color = '#c92a2a'
        } elseif ($last.concentration -ge $last.warningConcentrationThreshold -and $last.warningConcentrationThreshold -gt 0) {
            $status = 'Atencao'; $color = '#e8590c'
        }
        "<tr><td>$site</td><td>$p</td><td>$([math]::Round($last.concentration,1))</td><td>$($last.samplingStartTime)</td><td style='color:$color;font-weight:bold'>$status</td></tr>"
    }
}) -join "`n"

$body = @"
<html><body style="font-family:Arial,Helvetica,sans-serif">
<h2>BioScout - Resumo Diario ($(Get-Date -Format 'dd/MM/yyyy'))</h2>
<p>Ultima leitura de cada doenca, para os sites selecionados. Relatorio completo com graficos em anexo.</p>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
<tr style="background:#f1f3f5"><th>Site</th><th>Doenca</th><th>Concentracao (esporos/m3)</th><th>Atualizado em</th><th>Status</th></tr>
$rowsHtml
</table>
</body></html>
"@

Send-MailMessage -From $cred.UserName -To $ToAddress `
    -Subject "BioScout - Relatorio Diario $(Get-Date -Format 'dd/MM/yyyy')" `
    -Body $body -BodyAsHtml `
    -Attachments $attachmentPath `
    -SmtpServer 'smtp-mail.outlook.com' -Port 587 -UseSsl `
    -Credential $cred

Write-Host "E-mail enviado para $ToAddress" -ForegroundColor Green
