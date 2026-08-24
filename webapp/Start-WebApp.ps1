<#
Inicia o BioScout Web (painel movel) e abre no navegador.
Feche a janela do terminal que abrir para parar o servidor.
#>
$root = $PSScriptRoot
$python = "C:\Users\AlanMauro\AppData\Local\Programs\Python\Python312\python.exe"
$node = Join-Path $root "node-runtime\node-v22.14.0-win-x64\node.exe"
$bridgeDir = Join-Path $root "whatsapp-bridge"

if (-not (Test-Path (Join-Path $root "bioscout_web.db"))) {
    Write-Host "Nenhum usuario cadastrado ainda. Vamos criar o administrador primeiro."
    & $python (Join-Path $root "setup_admin.py")
}

# Servico do WhatsApp (whatsapp-bridge) sobe numa janela separada -- se
# ainda nao foi pareado, o QR code aparece em Configuracoes > WhatsApp
# dentro do proprio app assim que ele conectar.
Start-Process -FilePath $node -ArgumentList "index.js" -WorkingDirectory $bridgeDir -WindowStyle Minimized

Start-Process "http://localhost:5000"
& $python (Join-Path $root "app.py")
