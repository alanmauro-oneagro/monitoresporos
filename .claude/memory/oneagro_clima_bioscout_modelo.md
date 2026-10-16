# Clima do BioScout e' MODELO, nao sensor (verificado 2026-10-16)

O equipamento BioScout so' mede esporos -- nao tem sensor de chuva/clima.
O `weather.csv` que o BioScout mandava (out/2025 a ago/2026) tem
`assignedWeatherHub` vazio em todas as leituras e fazendas diferentes
(K&S Agricola x Agua Azul) com temperatura/umidade/chuva/vento IDENTICOS em
7558 de 7559 horas -> e' clima de modelo por coordenada, igual a Open-Meteo.
O endpoint de clima do BioScout hoje responde 404; desde entao todo clima do
site vem da Open-Meteo (MET Norway de backup). Chuva MEDIDA so' com equipamento
na fazenda (pluviometro/estacao com API). CEMADEN foi testado e descartado
(pluviometros longe demais das fazendas, PR #28); INMET so' via ACT.
Nao chamar o clima do BioScout de "medido"/"sensor" em telas ou textos.

## Regra (decisao do Alan, 2026-10-16): clima SO' de fonte externa

Do BioScout o site usa SO' esporos (spore_counts.csv). Clima (temp, UR,
chuva, vento) vem sempre de fonte externa: estacao oficial da fazenda
(`_estacao_oficial_do_site`) > Open-Meteo na estacao de referencia
(`_weather_coords_all`) -- tudo via `app._completar_clima_horario({}, ...)`.
`bioscout_fetch` nao busca mais clima; `weather.csv` antigo nao e' lido por
nenhuma tela (Graficos, Recomendacoes/rosa dos ventos, PDF, NDVI, Excel
Relatorio Diario, Painel). Nao reintroduzir leitura de clima do BioScout.
