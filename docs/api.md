# API do scanner

Inicie `python -m web_interface.app`; endereço local http://127.0.0.1:5000. Respostas JSON usam `{"success": true, "message": "...", "data": {...}}`. Falhas usam `success: false` e HTTP 400 (entrada), 404 (arquivo), 409 (tarefa ativa) ou 503 (recurso indisponível). O frontend lê `/status` em `data.status`.

| Rota | Método | Comportamento |
|---|---|---|
| /health | GET | Disponibilidade do servidor |
| /status | GET | Estado, sessão ativa, contagem e último resultado |
| /logs | GET | Eventos recentes |
| /settings | GET/POST | Snapshot/atualização atômica; números e booleanos JSON reais |
| /simulation-mode | GET/POST | POST com `{"enabled":true}`; corpo vazio alterna |
| /start-scan | POST | Reserva a tarefa e inicia aquisição no modo configurado |
| /stop-scan | POST | Solicita cancelamento; aguarde `running:false` |
| /generate-ply | POST | Reprocessa `{"session_id":"scan_..."}`; sem corpo seleciona última sessão compatível |
| /capture | POST | Captura manual independente; imagem sintética no modo simulação |
| /captures/filename | GET | Imagem manual |
| /clear-captures | POST | Apaga somente capturas manuais e sidecars |
| /reset-camera | POST | Libera câmera; abre novamente no próximo uso |
| /camera/status | GET | Propriedades e limitações observadas; não abre câmera |
| /video | GET | MJPEG sintético ou físico conforme modo |
| /scan-sessions | GET | Sessões persistidas, links para metadata e preview |
| /sessions/ID/files/path | GET | Arquivo permitido da sessão; `?download=1` força download |
| /point-clouds | GET | Todos os PLYs, com `download_url` e `delete_url` |
| /download-ply | GET | PLY mais recente persistido |
| /download-ply/path | GET | PLY avulso ou por sessão |
| /delete-ply/path | POST | Exclui o arquivo solicitado quando o scanner está ocioso |
| /calibration/status | GET | Valida conteúdo da calibração configurada |
| /viewer/status | GET | Disponibilidade de prévias |
| /api-info | GET | Rotas existentes e versão |

A aquisição e o processamento são assíncronos: uma resposta ao início não é uma conclusão. Estados terminais: `completed`, `cancelled`, `error`. Durante `cancelling`, outra tarefa continua proibida. O reprocessamento conserva o estado original da aquisição e grava `last_reconstruction_status` e histórico separado.

Exemplo de atualização:

```json
{"mode":"simulation","scan_steps":36,"step_delay":0.1,"capture_delay":0.05}
```

`simulation_mode` permanece como alias compatível: false seleciona físico. `mode: "offline"` não permite aquisição; use reprocessamento de sessão ou CLI com manifesto explícito. Uma calibração física precisa estar dentro da pasta autorizada de saídas para uso pela API.

Os parâmetros completos e exemplos estão no README. Propriedades desconhecidas, booleanos como strings, números fora dos limites, NaN e infinito invalidam a atualização inteira. Cada scan usa um snapshot estável. Configurações em memória voltam ao padrão ao reiniciar; os snapshots persistidos continuam associados às sessões.

Não há acesso arbitrário a arquivos do sistema. Caminhos resolvidos precisam permanecer dentro da pasta autorizada, inclusive após resolver links. Operações de exclusão, captura manual, câmera e configurações são bloqueadas durante tarefas ativas.
