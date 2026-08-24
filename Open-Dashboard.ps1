<#
Busca dados novos do BioScout, reconstroi a planilha do zero e abre para
visualizacao. Nao ha mais atualizacao automatica agendada -- rode isto
(ou o atalho "BioScout Dashboard" na area de trabalho) sempre que quiser
ver os dados mais recentes.

Tudo fica registrado em .\logs\open-dashboard-AAAA-MM-DD-HHmmss.log
para diagnostico.
#>
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$logDir = Join-Path $root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logPath = Join-Path $logDir "open-dashboard-$(Get-Date -Format 'yyyy-MM-dd-HHmmss').log"

Start-Transcript -Path $logPath -Append | Out-Null
Write-Host "===== Atualizando BioScout Dashboard em $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss') ====="

try {
    Write-Host "--- Busca de dados ---" -ForegroundColor Cyan
    & (Join-Path $root 'Fetch-BioScoutData.ps1')
    Write-Host "--- Busca de dados: OK ---" -ForegroundColor Green
} catch {
    Write-Warning "--- Busca de dados: FALHOU: $_ ---"
    Write-Warning "A planilha vai abrir com os dados mais recentes ja salvos (podem estar desatualizados)."
}

try {
    Write-Host "--- Reconstruindo planilha ---" -ForegroundColor Cyan
    & (Join-Path $root 'Build-Dashboard.ps1')
    Write-Host "--- Planilha: OK ---" -ForegroundColor Green
} catch {
    Write-Warning "--- Planilha: FALHOU: $_ ---"
}

Write-Host ""
Write-Host "===== Concluido em $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss') ====="
Stop-Transcript | Out-Null

Start-Process (Join-Path $root 'BioScoutDashboard.xlsx')
