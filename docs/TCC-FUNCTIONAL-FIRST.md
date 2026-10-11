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
- **Ainda não validado no Raspberry Pi real**. Calibração geométrica e reconstrução real ainda não foram implementadas nesta branch.
- O gerador de PLY herdado é **simulado** e está identificado como DEMO; o fluxo físico não o chama.
- Não movimentar a base com tabuleiro desequilibrado; garantir apoio mecânico, folga dos fios e ausência de colisões.

## Calibração rápida — primeiro resultado experimental

**Mudança de escopo:** o primeiro experimento usa **uma única orientação vertical do tabuleiro** (9 × 6 cantos internos, quadrados medidos de 10 mm), sem as inclinações de 15–25° das primeiras versões.

- Uma volta de **24 paradas/fotografias** (15° nominais entre paradas); motor usa os passos já definidos, não um deslocamento simulado.
- Após cada parada: **0,18 s** iniciais para a plataforma estabilizar, descartar no máximo um frame antigo e salvar a foto. Se houver borrão, aumentar a pausa no código.
- O OpenCV detecta os cantos **depois de a base completar a volta**, para não deixar o motor parado esperando processamento pesado.
- O tabuleiro deve ser fixado verticalmente, aproximadamente 2 cm fora do eixo, mas sem desequilibrar nem inclinar a plataforma. Uma posição exatamente centralizada reduz o movimento circular utilizado na estimativa.
- **Etapa câmera + eixo:** iluminação branca difusa, laser desligado. `Compute Camera + Axis` aproveita somente `camera1`, inclusive sessões anteriores interrompidas que tenham pelo menos oito fotos válidas e boa variação angular.
- **Etapa laser:** mesmo tabuleiro vertical, linha vermelha ligada sobre os quadrados e iluminação suficiente para detectar os cantos. `Compute Laser Plane` utiliza somente `laser1`.
- A execução é marcada como **validação limitada**: dados de uma única orientação podem deixar parâmetros ópticos e geométricos mal determinados. O programa rejeita menos de oito observações, amplitude angular insuficiente ou resíduos excessivos, mas somente o scan de um objeto de dimensões conhecidas poderá avaliar a reconstrução.
- Fotografias e relatórios ficam em `outputs/calibration/chatgpt_version/`. Calibrações válidas em JSON permanecem em `outputs/calibration/`. Nunca apague `outputs` sem fazer cópia.
- Enquanto estiver rodando, não faça `git pull` nem reinicie o servidor. Use **Stop Calibration**, aguarde a conclusão da interrupção e só depois atualize.

### Próximo marco

Ver `camera1: COMPLETE` e `Camera: READY / Turntable axis: READY`. Somente depois ligar o laser e completar `laser1`, antes de iniciar qualquer tentativa de reconstrução PLY verdadeira.

## Plano em ordem obrigatória

- [ ] Testar servidor Flask e pré-visualização da câmera.
- [ ] Testar motor fisicamente e medir passos por volta sem carga instável.
- [ ] Testar scan de 12–36 fotos e conferir `capture_positions` no metadata.
- [x] Implementar botão Calibrate com 24 fotos, orientação vertical única, sobreposição de cantos e cálculo experimental. Pendente validação física da reconstrução.
- [ ] Calibrar eixo de rotação e laser, validar a geometria e salvar arquivos JSON.
- [ ] Fazer scan real de cilindro fosco, gerar e conferir nuvem PLY.
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

Abrir http://scanner.local:5000. Inicialmente, checar vídeo e fiação, desativar Simulation Mode e reduzir para 12 captures para primeiro teste físico. **Atenção:** isso somente valida aquisição, ainda não gera uma nuvem verdadeira.

## Originalidade e referências

Implementação e avaliação próprias, reutilizando nossa base `hardware-integration`. Biblioteca OpenCV e fórmulas geométricas conhecidas devem ser citadas no TCC. O trabalho de terceiros pode servir de referência conceitual, mas nenhum código externo é copiado sem licença/autorização e atribuição adequada.
