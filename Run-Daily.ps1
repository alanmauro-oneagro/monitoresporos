<#
Ponto de entrada unico: busca dados novos e regera relatorio/planilha/e-mail.
Este e o script que a tarefa agendada diaria chama.

Cada etapa roda isolada (uma falha nao impede as demais) e tudo fica
registrado em .\logs\run-daily-AAAA-MM-DD.log para diagnostico.
#>
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$logDir = Join-Path $root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logPath = Join-Path $logDir "run-daily-$(Get-Date -Format 'yyyy-MM-dd').log"

Start-Transcript -Path $logPath -Append | Out-Null
Write-Host "===== Run-Daily iniciado em $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss') ====="

function Invoke-Step {
    param([string]$Name, [string]$ScriptFile)
    Write-Host ""
    Write-Host "--- $Name ---" -ForegroundColor Cyan
    try {
        & (Join-Path $root $ScriptFile)
        Write-Host "--- ${Name}: OK ---" -ForegroundColor Green
        return $true
    } catch {
        Write-Warning "--- ${Name}: FALHOU: $_ ---"
        return $false
    }
}

$fetchOk = Invoke-Step -Name "Busca de dados" -ScriptFile 'Fetch-BioScoutData.ps1'
if (-not $fetchOk) {
    Write-Warning "Busca de dados falhou -- o relatorio/planilha de hoje vao usar os dados mais recentes ja salvos (podem estar desatualizados)."
}

Invoke-Step -Name "Relatorio HTML" -ScriptFile 'Build-Report.ps1' | Out-Null
Invoke-Step -Name "Envio de e-mail" -ScriptFile 'Send-Report.ps1' | Out-Null
Invoke-Step -Name "Planilha (Dashboard)" -ScriptFile 'Refresh-Dashboard.ps1' | Out-Null

Write-Host ""
Write-Host "===== Run-Daily concluido em $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss') ====="
Stop-Transcript | Out-Null
