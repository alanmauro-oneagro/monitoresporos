<#
Salva as credenciais do BioScout de forma criptografada (DPAPI),
vinculadas ao usuario e computador atuais. Rode uma unica vez.
Ninguem mais consegue ler esse arquivo, nem copiando para outra maquina.
#>

$credPath = Join-Path $PSScriptRoot 'bioscout_cred.xml'

$cred = Get-Credential -Message 'Digite seu email e senha do BioScout (new.bioscout.com.au)'

$cred | Export-Clixml -Path $credPath

Write-Host ""
Write-Host "Credenciais salvas com sucesso em:" -ForegroundColor Green
Write-Host "  $credPath"
Write-Host "Elas so podem ser lidas pelo usuario '$env:USERNAME' neste computador."
