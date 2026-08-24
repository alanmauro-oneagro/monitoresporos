<#
Salva as credenciais de e-mail (remetente) de forma criptografada (DPAPI),
vinculadas ao usuario e computador atuais. Rode uma unica vez.

IMPORTANTE: use uma "senha de aplicativo" (App Password), nao a senha normal
da sua conta Outlook/Microsoft. Gere uma em:
https://account.live.com/proofs/AppPassword
#>

$credPath = Join-Path $PSScriptRoot 'email_cred.xml'

$cred = Get-Credential -Message 'Digite o e-mail remetente e a SENHA DE APLICATIVO (App Password) do Outlook'

$cred | Export-Clixml -Path $credPath

Write-Host ""
Write-Host "Credenciais de e-mail salvas com sucesso em:" -ForegroundColor Green
Write-Host "  $credPath"
Write-Host "So podem ser lidas pelo usuario '$env:USERNAME' neste computador."
