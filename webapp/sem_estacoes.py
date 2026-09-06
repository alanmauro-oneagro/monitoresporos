"""Provedor de estacoes "vazio" -- usado por todo pais que ja tem
fronteira cadastrada (`countries.py`) mas AINDA nao tem uma integracao
propria com uma rede de estacoes oficial (isso exige achar/integrar a
API de cada agencia meteorologica nacional, um trabalho por pais que
nao da' pra automatizar, ver `inmet_stations.py`/`dmc_stations.py` pros
dois exemplos ja feitos). Mesma interface dos dois, sempre devolvendo
vazio -- o pais aparece certinho no mapa (fronteira, previsao de nuvens
via Open-Meteo, que ja e' global) so' sem pino de estacao oficial ate'
alguem integrar a API daquele pais especificamente."""


def credenciais_configuradas():
    return False


def get_estacoes():
    return []


def estacoes_mais_proximas(lat, lon, n=2):
    return []


def estacao_mais_proxima(lat, lon):
    return None
