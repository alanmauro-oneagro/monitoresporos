<#
Atualiza BioScoutDashboard.xlsx com os dados mais recentes dos CSVs.

Na pratica isso reconstroi o arquivo do zero chamando Build-Dashboard.ps1 --
tentar "atualizar" as conexoes de um arquivo ja aberto se mostrou instavel
nesta automacao (Power Query + Slicers via COM), enquanto reconstruir do
zero, em fases, com novas tentativas automaticas, e confiavel.
Isso e o que roda todo dia dentro de Run-Daily.ps1.
#>

& (Join-Path $PSScriptRoot 'Build-Dashboard.ps1')
