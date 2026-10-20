# 3D Scanner

Digitalização 3D com Raspberry Pi, webcam USB, laser de linha, mesa giratória e interface Flask.

## Branches

- `chatgpt-version`: base funcional preservada.
- `final-polish`: interface refinada e melhorias em reconstrução, sessões e calibração.

## Interface

Quatro áreas: **Escanear**, **Histórico**, **Calibração** e **Configurações**.

- A captura física de uma volta inicia automaticamente a reconstrução PLY.
- Abaixo da webcam, o visualizador WebGL permite girar e ampliar nuvens PLY, exibir RGB ou cor monocromática e escolher arquivos do histórico.
- O histórico oferece download, renomeação e exclusão com confirmação.
- A configuração aceita intervalos angulares numéricos de 0,5° a 90° e persiste no equipamento.
- A calibração utiliza o tabuleiro 9 × 6. Uma nova calibração da câmera e do eixo fica preparada até o cálculo do laser ser concluído.

**Limitação atual:** a nuvem é exibida quando o PLY fica pronto; a prévia progressiva durante a captura ainda exige validação de desempenho no Raspberry Pi 3 B+.

## Executar no Raspberry Pi

```bash
cd ~/scanner3d-raspberrypi
git fetch origin
git switch final-polish
git pull --ff-only origin final-polish
source .venv/bin/activate
python -m web_interface.app
```

Se ainda não houver ambiente virtual, crie um e instale `requirements.txt`.

Acesse a porta 5000 pelo navegador da mesma rede.

## Arquivos

```text
scanner_core/reconstruction.py         Detecção e reconstrução 3D
scanner_core/calibration_workflow.py    Calibração física
scanner_core/pipeline.py                Captura e movimentação
scanner_core/session.py                 Sessões e metadados
scanner_core/runtime_settings.py        Configurações salvas
web_interface/app.py                    API Flask
web_interface/templates/index.html     Estrutura HTML
web_interface/static/dashboard.css     Design
web_interface/static/dashboard.js      Interface e visualizador WebGL
outputs/scans/                          Sessões e fotografias
outputs/point_clouds/                   PLYs gerados
outputs/calibration/                    Calibrações
```

Os diretórios em `outputs/` contêm os dados produzidos no equipamento. Não os exclua durante atualizações.

Cada sessão possui um identificador único; o horário depende da sincronização correta do relógio do Raspberry Pi. Os ângulos registrados representam posições comandadas, não medidas por encoder. As cores RGB são estimadas a partir das imagens; o PLY não é uma malha de superfícies.

## Testes

A GitHub Action em `.github/workflows/calibration-tests.yml` verifica a sintaxe de Python e JavaScript e executa testes de API, calibração, sessões e reconstrução. Esses testes não substituem a validação real da webcam, do motor e da qualidade dimensional no Raspberry Pi.
