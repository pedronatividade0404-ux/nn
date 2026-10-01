# Validação da versão Vercel + Railway

- 33 testes locais passaram (`python -m pytest -q`). Houve um aviso de depreciação de Starlette/httpx.
- Foram verificados autenticação do worker, separação dos arquivos por usuário, bloqueio de caminhos indevidos, envio de arquivos em partes, aplicação de configuração e parada por usuário.
- Foram verificados limite global de uma live, liberação de capacidade, ocultação de chaves nos erros, restauração após reinício e encerramento por expiração do plano.
- A compilação dos arquivos Python e a sintaxe de `app/static/app.js` passaram.
- A configuração Railway, o ponto de entrada Vercel e a remoção da dependência Daytona foram conferidos.
- Um teste com FFmpeg real transcodificou um vídeo local com a nova configuração de escala, 30 fps e uma thread, com saída 0.

## Limites desta validação

Não foi executado build Docker neste ambiente. Não foram realizados deploys reais no Vercel/Railway nem transmissão real ao TikTok. Os testes de integração locais usam transporte local ou substitutos; não comprovam credenciais, saldo, elegibilidade LIVE ou disponibilidade de rede dos provedores.

O teste TCP enviado pelo usuário mostrou conexão à porta 1935 no Railway. Isso permite continuar a implantação, mas ainda é necessário confirmar a transmissão com uma URL e chave RTMP válidas.

Leia `README.md` antes de migrar. A primeira recuperação no Railway limpa a seleção dos arquivos antigos: os vídeos precisam ser enviados novamente. Preserve `APP_SECRET` e o banco existente.
