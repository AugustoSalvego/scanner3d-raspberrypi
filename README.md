# Scanner3D Raspberry Pi

Scanner experimental com Python, Flask, OpenCV e NumPy, preparado para Raspberry Pi 3, webcam USB Fifine K420, laser vermelho de linha e plataforma com 28BYJ-48 / ULN2003.

O software reconstrói pontos a partir da linha observada nas imagens, com intrínsecos da câmera, plano do laser e eixo da plataforma calibrados. A validade de um PLY não comprova a forma nem a precisão física: inspecione as máscaras, sobreposições, dimensões e prévias, e compare com um objeto de medidas conhecidas.

## Modos e estado de validação

- **Simulação**: imagens de um objeto matemático conhecido, identificadas como `synthetic`, sem webcam ou GPIO. Usa o mesmo detector e triangulador do processamento físico.
- **Offline**: imagens existentes, calibração e manifesto com ângulos explícitos. Nenhuma volta é inferida pela quantidade de arquivos.
- **Físico**: comanda meios passos, espera estabilização, descarta frames antigos, captura e reconstrói. Falhas não provocam fallback para simulação.

A implementação é testável sem hardware. A execução física e a precisão do conjunto só podem ser confirmadas com a montagem, suas calibrações e imagens reais. Veja o registro desta execução em [docs/validation.md](docs/validation.md).

## Instalação no Windows

Python 3.10 ou superior; esta execução utiliza Python 3.12.2. Na raiz do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m web_interface.app
```

Abra http://127.0.0.1:5000. O modo inicial é simulação e o preview também é sintético. O servidor não abre câmera nem GPIO ao importar módulos. Use somente uma instância por equipamento. A aplicação não possui autenticação e foi projetada para uso local na rede de desenvolvimento.

Uma execução reproduzível pelo terminal:

```powershell
.\.venv\Scripts\python.exe -m tools.run_scan --mode simulation --captures 36
```

O comando imprime o ID da sessão e o caminho da nuvem. Para repetir apenas o processamento, substitua `SESSION_ID` pelo ID retornado:

```powershell
.\.venv\Scripts\python.exe -m tools.reconstruct outputs/scans/SESSION_ID --calibration outputs/scans/SESSION_ID/calibration.json
```

## Instalação no Raspberry Pi

Em Raspberry Pi OS com Python compatível, use os pacotes do sistema para OpenCV/NumPy/GPIO quando não houver wheels para a arquitetura ARM:

```bash
sudo apt update
sudo apt install python3-venv python3-opencv python3-numpy python3-flask python3-pytest python3-gpiozero
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pytest -q
```

A alternativa em sistemas com wheels disponíveis é instalar `requirements-dev.txt` no venv e `gpiozero` no Raspberry Pi. Não instale GPIO para executar simulação no computador. O projeto usa OpenCV sem janelas gráficas; as imagens de diagnóstico são arquivos PNG.

## Calibração física

Siga [o procedimento completo](docs/calibration-plan.md). Mantenha câmera, lente/foco, resolução, laser e montagem fixos após calibrar. Se algum deles mudar, refaça as etapas afetadas. Todas as medidas são em milímetros.

1. Fotografe um tabuleiro com dimensões medidas, em pelo menos oito poses distintas, variando posição, escala e inclinação. `columns` e `rows` são cantos internos.
2. Registre pares do tabuleiro sem/com laser em várias poses, sem mover o alvo entre cada par. A pose é obtida do tabuleiro e a linha é intersectada com seu plano.
3. Determine o eixo com um canto fixo do alvo girando na plataforma e ângulos documentados, ou informe medidas independentes no referencial da câmera.
4. Valide o bundle completo antes de adquirir um objeto.

```bash
python -m tools.calibrate camera --images "data/camera/*.png" --columns 9 --rows 6 --square-mm 20
python -m tools.calibrate laser --manifest data/laser/manifest.json
python -m tools.calibrate axis-fit --manifest data/axis/manifest.json
python -m tools.calibrate validate --calibration outputs/calibration/calibration.json
```

O valor `20` é um exemplo de dimensão do quadrado: substitua pela dimensão realmente medida. Os exemplos de manifesto e os critérios de diversidade, degenerescência e resíduos estão em [docs/calibration-plan.md](docs/calibration-plan.md). Nenhum arquivo de calibração física é inventado pelo aplicativo.

## Primeiro teste no equipamento

Confira a alimentação do motor/ULN2003, terra comum e ordem das bobinas. Os pinos BCM 17, 27, 22, 23 vêm dos scripts antigos e precisam ser conferidos na montagem. Veja [o roteiro de hardware](docs/hardware-integration-plan.md) para os comandos de captura e movimento pequeno e para verificar passos por volta.

A unidade do motor é **uma transição de meio passo**. Um ciclo da sequência contém oito transições. O valor nominal `4096` transições por volta é configurável e precisa ser medido na sua plataforma, incluindo qualquer transmissão. A posição é comandada em malha aberta; não há sensor, homing ou garantia de ausência de perda de passos. A primeira energização estabelece a referência relativa antes da captura.

Crie `physical-settings.json` na raiz, ajustando os valores à montagem:

```json
{
  "scan_steps": 180,
  "steps_per_revolution": 4096,
  "direction": 1,
  "motor_pins": [17, 27, 22, 23],
  "motor_step_delay": 0.002,
  "step_delay": 0.3,
  "capture_delay": 0.05,
  "camera": {
    "device": 0, "width": 640, "height": 480,
    "fps": 30, "flush_frames": 5, "controls": {}
  },
  "detector": {},
  "filtering": {"voxel_size_mm": 0.5},
  "reconstruction": {"min_depth_mm": 1, "max_depth_mm": 5000}
}
```

A resolução deve corresponder à calibração e ao frame efetivamente recebido. `step_delay` é o tempo de estabilização após o movimento; `motor_step_delay` é o intervalo entre meios passos; `capture_delay` é a pausa posterior à captura. A distribuição usa posições inteiras `floor(i * steps_per_revolution / scan_steps)`, sem erro acumulado e sem repetir 360°.

Depois de conferir câmera e movimento pequeno:

```bash
.venv/bin/python -m tools.run_scan --mode physical --settings physical-settings.json --calibration outputs/calibration/calibration.json
```

Ctrl+C cancela e aguarda a limpeza. As bobinas são liberadas em conclusão, cancelamento e erro. A aquisição termina na última posição de captura, sem assumir retorno físico à origem.

Para ajustar exposição e foco, use `camera.controls` com propriedades suportadas pelo backend, por exemplo `exposure`, `focus`, `auto_exposure` e `autofocus`. Os valores dependem do driver e não são portáveis. Consulte `GET /camera/status` e os metadados de captura para verificar aceitação/readback. Controles não comprovados continuam desabilitados no painel; o arquivo de configurações/API permite solicitar ajustes no equipamento real.

## Imagens antigas e processamento offline

Prepare uma pasta com `metadata.json`, imagens e calibração real. Exemplo de formato, com ângulos que devem vir do registro da aquisição:

```json
{
  "schema_version": 1,
  "source": "physical",
  "angle_source": "Registro manual de posições; descrever como os ângulos foram obtidos",
  "captures": [
    {"id": "frame_000", "index": 0, "path": "images/frame_000.png", "angle_deg": 0, "status": "captured"},
    {"id": "frame_001", "index": 1, "path": "images/frame_001.png", "angle_deg": 12.5, "status": "captured"}
  ]
}
```

Os números do exemplo descrevem o formato, não a sua aquisição. Pode haver ângulos irregulares ou capturas ausentes: os descartes são relatados. `laser_off_path` opcional deve apontar para uma imagem pareada da mesma pose/exposição. O software não pressupõe controle eletrônico do laser.

```bash
python -m tools.reconstruct data/minha_sessao --calibration outputs/calibration/calibration.json --settings physical-settings.json
python -m tools.detect_red --help
```

Não coloque calibração sintética em uma sessão física. Caminhos de imagens são relativos ao manifesto e não podem escapar da pasta autorizada. A ausência de linha, imagens inválidas, resolução incompatível ou reconstrução vazia não são tratadas como sucesso.

## Resultados e inspeção

`outputs/scans/SESSION_ID/` guarda:

- `metadata.json`: origem dos dados, estados, capturas, posições comandadas, ângulos, timestamps, resoluções, falhas e histórico de reconstruções.
- `settings.json` e `calibration.json`: snapshots utilizados na aquisição.
- `captures/*.png`: imagens originais.
- `point_clouds/`: execuções de reconstrução com nuvem bruta, filtrada, prévia PNG e relatório.
- Máscaras, sobreposições da linha e estatísticas por captura, em caminhos indicados no relatório.

Cada reconstrução usa uma nova pasta para preservar os resultados anteriores. A filtragem voxel mantém uma amostra por célula; o filtro de vizinhança é opcional e desativado por padrão. Compare sempre com a nuvem bruta.

O painel mostra prévias e permite baixar os PLYs mesmo após reiniciar o servidor. A listagem também inclui arquivos fora de sessões em `outputs/point_clouds`. Capturas manuais ficam em `outputs/captures` e nunca são anexadas a sessões encerradas.

Para aceitar uma reconstrução real, confira origem e calibração, alinhamento da linha nos overlays, cobertura angular, dimensões e forma da nuvem. Meça um objeto de referência e registre os erros antes de afirmar precisão dimensional.

## Coordenadas e limitações

Câmera: +X à direita, +Y para baixo, +Z para a frente. O plano do laser satisfaz `n · p + d = 0`. Os pixels são corrigidos pela distorção e seus raios intersectados com esse plano. Pontos atrás da câmera, raios quase paralelos e pontos fora dos limites são rejeitados.

A nuvem é expressa em milímetros, com origem no ponto calibrado do eixo e orientação da câmera na referência zero. Cada ponto é transformado por `R(axis, -(angle_deg + angle_offset_deg)) * (p_camera - axis_point)`. O sentido positivo segue a regra da mão direita em torno de `axis_direction`.

A segmentação usa duas faixas de vermelho em HSV, excesso de vermelho, largura, saturação, ambiguidade, confiança e continuidade. Sem par laser desligado/ligado, uma marca vermelha estreita pode ser indistinguível do laser: inspecione os diagnósticos e ajuste ROI/iluminação. Trechos ausentes não são preenchidos artificialmente.

## API e desenvolvimento

Veja [docs/api.md](docs/api.md) e [docs/software-architecture.md](docs/software-architecture.md). Os estados são `idle`, `acquiring`, `processing`, `cancelling`, `completed`, `cancelled` e `error`. Sessões encontradas incompletas após reinício são apresentadas como `interrupted`.

Atualizações durante uma tarefa ativa retornam 409. O cancelamento mantém a reserva até o worker encerrar. Um lock do sistema operacional impede dois processos usando a mesma pasta de saídas; processos configurados com raízes diferentes não compartilham esse lock, portanto não execute duas instâncias para a mesma montagem.

`SCANNER_OUTPUT_ROOT` permite escolher outra raiz de saídas. O padrão é `outputs` na raiz do projeto, independentemente do diretório de onde o servidor foi iniciado.

Testes: detecção com ruído/ausência/reflexos, triangulação e rotações com valores esperados, calibrações inválidas e degeneradas, fase/contagem do motor, câmera falsa, cancelamento, concorrência, sessões/API, PLY e reconstrução repetida. Testes sintéticos validam software, não a precisão física.

[Author – Danilo Augusto Salvego dos Santos](https://github.com/AugustoSalvego)
