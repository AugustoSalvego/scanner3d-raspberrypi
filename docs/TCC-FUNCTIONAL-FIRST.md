# TCC — scanner físico funcional (chatgpt-version)

Esta branch deriva exclusivamente de `hardware-integration` (commit `6470d49`). A versão do Claude não foi mesclada. **Prioridade: funcionamento real, não recursos extras.**

## Critérios de conclusão

1. Câmera Fifine K420 faz capturas reais; motor 28BYJ-48/ULN2003 gira a plataforma com passos conhecidos e salva fotos vinculadas aos ângulos.
2. Um tabuleiro de **9×6 cantos internos**, tamanho real de quadrado medido após impressão (nominal 10 mm), fornece calibração intrínseca da câmera e geometria do eixo de rotação.
3. Com o laser ligado, capturas do tabuleiro em orientações conhecidas permitem estimar o **plano do laser**.
4. Em cada foto do scan, detectamos a linha, projetamos raios da câmera, intersectamos o plano do laser, transformamos os pontos pelo ângulo da plataforma e geramos **PLY real**.
5. Uma malha 3D simples é gerada a partir dos pontos, sem fechar lacunas grandes automaticamente; avaliamos com um objeto fosco de medidas conhecidas.
6. Interface mostra feedback no clique, progresso verdadeiro, erro e conclusão; Stop Scan interrompe sem fingir sucesso.

## Estado da implementação — primeiro marco

- O driver GPIO em `scanner_core/motor.py` avança **um half-step real por unidade**, usando BCM (17,27,22,23).
- O pipeline em modo físico move, estabiliza, captura, registra `half_steps` e `angle_deg` em `outputs/scans/<sessão>/metadata.json`.
- A contagem de 4096 half-steps/volta é **hipótese**: medir na plataforma montada antes de reconstrução métrica. Sem encoder, posições são comandadas e não confirmadas por sensor.
- O site tem animação e feedback durante a aquisição.
- **No Raspberry Pi real:** câmera já reconheceu 54 cantos em 1280×720 e a base já girou. O cálculo da calibração completa e o PLY real ainda aguardam validação física.
- O gerador de PLY herdado é **simulado** e está identificado como DEMO; o fluxo físico não o chama.
- Não movimentar a base com tabuleiro desequilibrado; garantir apoio mecânico, folga dos fios e ausência de colisões.

## MVP: validação física prioritária

**Só um resultado:** escanear um cilindro pequeno e fosco com o laser e produzir um arquivo PLY de pontos REAIS. Não é necessário STL ou malha perfeita neste primeiro marco.

1. Abrir o painel Flask simplificado `/`. Não apagar nem limpar dados existentes.
2. Tabuleiro rígido vertical, quadrados de 10 mm, aproximadamente 2 cm fora do eixo, sem inclinação da plataforma. Laser OFF, luz branca ON. Verificar 54 cantos. Primeiro tentar **Calcular câmera/eixo com fotos salvas** (menu Informações técnicas), pois capturas anteriores podem bastar.
3. Se as fotos existentes não bastarem: tabuleiro vertical e inicialmente voltado para a câmera, botão **Calibrar câmera + eixo**. O motor procura a **direita** (testa a presença dos 54 cantos a cada 5°), interrompe ao ter **três falhas seguidas**, volta ao **centro** e então fotografa em incrementos de **3° à esquerda**, até o padrão desaparecer três vezes seguidas ou alcançar 36 fotos úteis. Velocidade do motor na calibração: **5 ms por half-step**. Espera de estabilização: **0,40 s**, mais o tempo de duas leituras de webcam. O cálculo começa automaticamente ao terminar. Se o primeiro giro for para esquerda em vez de direita, use **Inverter giro direita/esquerda** na interface.
4. Laser ON com luz suficiente para reconhecer os cantos, tabuleiro ainda vertical, botão **Calibrar plano do laser**. O mesmo trajeto direita → centro → fotografias à esquerda é repetido, e o plano é calculado automaticamente.
5. Remover tabuleiro; colocar cilindro fosco e claro no centro da plataforma; laser vermelho visível; iniciar escaneamento físico 360°. O sistema guarda fotos e posições comandadas de motor. Confirmar que a plataforma realmente completa uma volta.
6. **Gerar PLY real** no painel. O processamento detecta a linha vermelha, intersecta os raios da câmera com o plano calibrado, desfaz a rotação do motor, filtra pontos fora da região central e exporta coordenadas mm.
7. Abrir o PLY no MeshLab. Fotografar a tela, contar os pontos e comparar a forma com o objeto real. Anotar medidas de referência, número de fotos aceitas, cobertura e erros: material direto para resultados do TCC.

### Critério de sucesso mínimo

Um `real_scan_*.ply` ou `real_*.ply` criado de fotografias físicas deve conter pontos 3D organizados em uma forma reconhecível do objeto. A calibração vertical **tem validação limitada** e pode não ter informação suficiente para todos os parâmetros; reprojeção baixa, por si só, não garante boa reconstrução. Se falhar, usar os diagnósticos dos dados já salvos antes de acrescentar qualquer funcionalidade.

### Limitações importantes

- O giro da calibração **não é uma volta completa**: cada lado é limitado a 110° por segurança, com parada antecipada após três perdas consecutivas do tabuleiro. A captura usa **no máximo 36 fotos válidas** no lado esquerdo; isso não garante que todas sejam úteis geometricamente. Sem encoder, os ângulos são comandados e supõem inicialmente 4096 half-steps por volta. Medir uma volta real da plataforma é necessário para validar a escala angular.
- A base deve parar completamente antes da foto. A pausa inicial de 0,40 s é um ajuste experimental; se ocorrer borrão, aumente `SETTLE_SECONDS`. A detecção de cantos ocorre em cada parada para permitir identificar quando o tabuleiro desaparece.
- O raio útil de reconstrução inicialmente é de 90 mm e a nuvem não tem preenchimento automático de superfícies invisíveis.
- Todo PLY real é gravado em `outputs/point_clouds/real_<sessão>.ply`. O gerador simulado antigo não participa do novo painel.
- Testes automatizados de geometria e de integração com fotos sintéticas **não são** validação física. O resultado real depende de testes no Raspberry Pi.
- Não usar `git pull` com o motor em movimento. Preserve `outputs/`, incluindo calibrações anteriores e todas as fotos. Ao repetir uma etapa, a pasta anterior é movida para `outputs/calibration/chatgpt_version/history/` com seus metadados, em vez de ser apagada.

## Plano em ordem obrigatória

- [ ] Testar servidor Flask e pré-visualização da câmera.
- [ ] Testar motor fisicamente e medir passos por volta sem carga instável.
- [ ] Testar scan de 12–36 fotos e conferir `capture_positions` no metadata.
- [x] Implementar calibração vertical única com busca do limite à direita, retorno ao centro, fotos à esquerda e cálculo experimental. **Pendente validação física.**
- [ ] Obter valores reais de câmera, eixo e plano do laser no Raspberry e validar contra objeto conhecido.
- [x] Implementar reconstrução experimental de PLY a partir de fotos reais. **Pendente validação no scanner físico.**
- [ ] Gerar malha simples PLY/STL; medir erro contra objeto conhecido.
- [ ] Documentar fotos, algoritmo, limitações e resultados do TCC.

## Teste no Raspberry

```bash
ssh scanner@scanner.local
cd ~/scanner3d-raspberrypi
git fetch origin
git switch --track origin/chatgpt-version  # somente na primeira vez
# se já existe a branch local: git switch chatgpt-version
sudo apt install -y python3-opencv python3-numpy python3-flask python3-gpiozero
source venv/bin/activate
python -c 'import cv2, flask, numpy, gpiozero; print("Dependencies OK")'
python -m web_interface.app
```

Abrir http://scanner.local:5000. Primeiro verificar os cantos e confirmar o **sentido físico do giro**. O novo painel não inclui PLY simulado; `Gerar PLY real` só processa capturas físicas após câmera, eixo e laser calibrados. Interrompa o servidor antes de atualizar com Git; **não execute `git pull` com motor em movimento**.

## Originalidade e referências

Implementação e avaliação próprias, reutilizando nossa base `hardware-integration`. Biblioteca OpenCV e fórmulas geométricas conhecidas devem ser citadas no TCC. O trabalho de terceiros pode servir de referência conceitual, mas nenhum código externo é copiado sem licença/autorização e atribuição adequada.
