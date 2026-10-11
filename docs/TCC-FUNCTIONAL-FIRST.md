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

## MVP: validação física prioritária

**Só um resultado:** escanear um cilindro pequeno e fosco com o laser e produzir um arquivo PLY de pontos REAIS. Não é necessário STL ou malha perfeita neste primeiro marco.

1. Abrir o painel Flask simplificado `/`. Não apagar nem limpar dados existentes.
2. Tabuleiro rígido vertical, quadrados de 10 mm, aproximadamente 2 cm fora do eixo, sem inclinação da plataforma. Laser OFF, luz branca ON. Verificar 54 cantos. Primeiro tentar **Calcular câmera/eixo com fotos salvas** (menu Informações técnicas), pois capturas anteriores podem bastar.
3. Se as capturas existentes falharem por poucos cantos ou pouca variedade angular: tabuleiro de frente para a câmera, botão **Calibrar câmera + eixo**. A placa executa **36 fotos ao longo de 120° úteis**. A cada movimento espera 0,18 s, fotografa; depois retorna à orientação aproximada inicial, detecta os cantos e calcula automaticamente os parâmetros.
4. Laser ON com luz suficiente para reconhecer os cantos, tabuleiro ainda vertical, botão **Calibrar plano do laser**, também com 36 fotos na faixa útil e cálculo automático.
5. Remover tabuleiro; colocar cilindro fosco e claro no centro da plataforma; laser vermelho visível; iniciar escaneamento físico 360°. O sistema guarda fotos e posições comandadas de motor. Confirmar que a plataforma realmente completa uma volta.
6. **Gerar PLY real** no painel. O processamento detecta a linha vermelha, intersecta os raios da câmera com o plano calibrado, desfaz a rotação do motor, filtra pontos fora da região central e exporta coordenadas mm.
7. Abrir o PLY no MeshLab. Fotografar a tela, contar os pontos e comparar a forma com o objeto real. Anotar medidas de referência, número de fotos aceitas, cobertura e erros: material direto para resultados do TCC.

### Critério de sucesso mínimo

Um `real_scan_*.ply` ou `real_*.ply` criado de fotografias físicas deve conter pontos 3D organizados em uma forma reconhecível do objeto. A calibração vertical **tem validação limitada** e pode não ter informação suficiente para todos os parâmetros; reprojeção baixa, por si só, não garante boa reconstrução. Se falhar, usar os diagnósticos dos dados já salvos antes de acrescentar qualquer funcionalidade.

### Limitações importantes

- A amostragem angular do tabuleiro usa 36 posições em 120°; sem sensor de posição, os ângulos são comandados e assumem inicialmente 4096 half-steps por volta. Medir uma volta completa da plataforma é indispensável para validar a escala angular.
- A base deve parar completamente antes da foto. A pausa inicial de 0,18 s é hipótese e deve ser aumentada se houver borrões; o processamento dos cantos ocorre depois da movimentação.
- O raio útil de reconstrução inicialmente é de 90 mm e a nuvem não tem preenchimento automático de superfícies invisíveis.
- Todo PLY real é gravado em `outputs/point_clouds/real_<sessão>.ply`. O gerador simulado antigo não participa do novo painel.
- Testes automatizados de geometria e de integração com fotos sintéticas **não são** validação física. O resultado real depende de testes no Raspberry Pi.
- Não usar `git pull` com o motor em movimento. Preserve `outputs/`, incluindo calibrações anteriores e todas as fotos.

## Plano em ordem obrigatória

- [ ] Testar servidor Flask e pré-visualização da câmera.
- [ ] Testar motor fisicamente e medir passos por volta sem carga instável.
- [ ] Testar scan de 12–36 fotos e conferir `capture_positions` no metadata.
- [x] Implementar botão Calibrate com 24 fotos, orientação vertical única, sobreposição de cantos e cálculo experimental. Pendente validação física da reconstrução.
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

Abrir http://scanner.local:5000. Inicialmente, checar vídeo e fiação, desativar Simulation Mode e reduzir para 12 captures para primeiro teste físico. **Atenção:** isso somente valida aquisição, ainda não gera uma nuvem verdadeira.

## Originalidade e referências

Implementação e avaliação próprias, reutilizando nossa base `hardware-integration`. Biblioteca OpenCV e fórmulas geométricas conhecidas devem ser citadas no TCC. O trabalho de terceiros pode servir de referência conceitual, mas nenhum código externo é copiado sem licença/autorização e atribuição adequada.
