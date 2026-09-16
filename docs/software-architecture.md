# Arquitetura implementada

`web_interface.app.create_app` expõe a API/painel e recebe um `ScanController`, permitindo testes isolados. Importar módulos não inicializa dispositivos. `tools.run_scan` usa o mesmo controlador pelo terminal.

- `pipeline.py`: reserva sincronizada, thread por tarefa, cancelamento por Event, aquisição e reconstrução, limpeza garantida.
- `camera.py`: câmera lazy, acesso coordenado, resolução real, controles/readback, descarte de frames e diagnóstico.
- `motor.py`: sequência contínua de oito estados; cada transição conta um meio passo comandado; GPIO injetável.
- `session.py`: IDs UUID + microssegundos, snapshots, JSON atômico, acesso restrito e lock de processo.
- `runtime_settings.py`: validação estrita, atualização completa ou rejeição completa.
- `laser.py`: máscara, centro subpixel por linha, rejeições e diagnósticos.
- `calibration.py`: intrínsecos/distorção, poses de tabuleiro, plano do laser e eixo de rotação.
- `point_cloud.py`: manifesto, triangulação, transformação para objeto, filtros, PLY, prévia e relatório.
- `simulation.py`: imagens sintéticas explícitas de geometria conhecida para testar o núcleo comum.

O controlador passa os snapshots à reconstrução e nunca consulta configurações mutáveis durante o scan. A thread ativa conserva a exclusão até liberar o motor e gravar o estado final. O cancelamento não troca antecipadamente `running` para false. A reserva de arquivo usa lock do SO, liberado também em término do processo.

Dados originais são preservados. Reprocessamento gera uma nova pasta de resultado. Sessões incompletas encontradas após reinício são apresentadas como interrompidas, sem serem convertidas em sucesso. Não é possível retomar fisicamente uma posição só com o contador salvo, pois não existe sensor de posição.

Referências de implementação: [OpenCV — calibração e geometria](https://docs.opencv.org/4.12.0/d9/d0c/group__calib3d.html) e [GPIO Zero — dispositivos de saída](https://gpiozero.readthedocs.io/en/stable/api_output.html).
