# NexaTok — Vercel + Railway

Esta versão substitui Daytona por um worker HTTP autenticado no Railway. O site continua no Vercel, usando seu PostgreSQL existente. Não requer Blueprint, seu PC ou cartão para usar créditos de teste já liberados. O crédito e o acesso são controlados pelo Railway; não é hospedagem gratuita permanente.

## O que muda

- Um worker compartilhado executa FFmpeg. Cada usuário recebe uma pasta privada logicamente separada no volume; não há uma VPS ou contêiner por cliente. O serviço não executa código de usuários.
- Limite global inicial: **1 live simultânea**, incluindo todos os usuários. Outros pedidos ficam em “Aguardando capacidade”. Os planos ainda limitam contas, mas não garantem capacidade computacional dedicada.
- Vídeos são enviados em partes de 3 MiB pelo site. Limite recomendado: 100 MB por vídeo, 200 MB por usuário e 300 MB de dados no volume inteiro.
- FFmpeg usa um thread de codificação e saída limitada a 960×540, preservando proporção, até 30 fps. A qualidade e a quantidade de lives exigem teste real.
- Plano expirado encerra o FFmpeg no worker, sem depender do navegador ou do Vercel.
- Loop, reinício programado, recuperação após falha e diagnóstico do FFmpeg com credenciais ocultas continuam disponíveis.
- Login, admin, chaves, renovação, contas e banco são preservados.
- Geração RTMP depende da sessão e autorização do TikTok. Não há garantia de funcionamento dos endpoints internos nem de live ilimitada pelo TikTok.

## 1. Atualize o repositório

Extraia este ZIP e copie o conteúdo para a raiz do repositório conectado ao Vercel. Não crie uma pasta extra dentro da raiz. Os arquivos `index.py`, `pyproject.toml`, `app/` e `worker/` devem estar na raiz.

Preserve seu APP_SECRET, DATABASE_URL, PUBLIC_URL e demais credenciais atuais. Trocar APP_SECRET pode tornar cookies/chaves salvos ilegíveis. Não envie arquivos .env ao GitHub.

Esta versão mantém nomes internos antigos (`daytona_service.py`, coluna sandbox e caminhos virtuais `/home/daytona/...`) para compatibilidade com o banco. Eles agora são traduzidos pelo adaptador para a pasta do usuário no Railway; não há chamada ao SDK Daytona.

## 2. Crie o worker Railway

1. Railway → New Project → Deploy from GitHub Repo → selecione o mesmo repositório atualizado.
2. Root Directory: raiz do repositório. Config: `railway.json`. Ele indica `worker/Dockerfile.railway`. Como alternativa, defina `RAILWAY_DOCKERFILE_PATH=worker/Dockerfile.railway` em Variables.
3. Não use o serviço Docker de diagnóstico anterior nem seu Start Command `python -c ...`. Crie um serviço novo. Deixe o Start Command vazio para usar o CMD do Dockerfile.
4. Adicione um **Volume** ao serviço, montado exatamente em **`/data`**. Trial/Free têm limite documentado de 0,5 GB por volume; não use 500 MB por vídeo. A montagem evita perder dados nos redeploys. O volume também consome crédito.
5. Variables do Railway:

| Nome | Valor |
| --- | --- |
| WORKER_TOKEN | segredo aleatório de no mínimo 32 caracteres, igual no Vercel |
| WORKER_DATA_DIR | /data |
| MAX_CONCURRENT_STREAMS | 1 |
| MAX_WORKER_USERS | 10 |
| MAX_DATA_MB | 300 |
| FFMPEG_THREADS | 1 |
| RAILWAY_DEPLOYMENT_DRAINING_SECONDS | 30 |

Gere WORKER_TOKEN num gerador de senhas confiável (48 caracteres ou mais). Não coloque o token no frontend, em prefixos públicos, nos logs ou no chat. O worker não precisa de DATABASE_URL nem APP_SECRET.

6. Settings → Networking → Generate Domain. Use porta **8080** se a interface perguntar. O worker usa PORT do Railway, ou 8080 quando não definido.
7. Healthcheck: `/health`. Replicas: **1**. Desative Serverless/sleep. Reinício: On Failure, até 3 tentativas, conforme railway.json. O plano Trial não oferece política Always.
8. Abra `https://SEU-WORKER.up.railway.app/health`: deve mostrar `{"ok":true}`. A saúde HTTP não confirma uma transmissão real.

## 3. Configure o Vercel

Em Settings → Environment Variables, Production:

| Nome | Valor |
| --- | --- |
| APP_ENV | production |
| WORKER_URL | https://SEU-WORKER.up.railway.app (sem /health) |
| WORKER_TOKEN | exatamente o segredo configurado no Railway |
| MAX_VIDEO_MB | 100 |
| MAX_STORAGE_MB | 200 |
| TIKTOK_STUDIO_VERSION | a versão confirmada no seu Studio (você informou 0.73.4) |

Mantenha DATABASE_URL externa com SSL, APP_SECRET, PUBLIC_URL, FRONTEND_URL, ADMIN_EMAIL e CRON_SECRET. ADMIN_BOOTSTRAP_TOKEN só deve permanecer durante a criação do primeiro admin. O padrão do adaptador usa TIKTOK_STUDIO_VERSION; parâmetros da versão não garantem aceitação pelo TikTok.

As variáveis DAYTONA_API_KEY e DAYTONA_SNAPSHOT não são usadas nesta versão e podem ser removidas do Vercel. Faça um novo deploy depois de salvar as variáveis.

## 4. Migre seu usuário e os vídeos

1. Pare as transmissões no site antigo enquanto ele ainda usa Daytona. Faça backup dos vídeos no Daytona antes de removê-los.
2. Depois do deploy novo, entre no site → **Plano & ambiente → Recuperar ambiente**. A operação cria/reaproveita a pasta do usuário no Railway e atualiza o vínculo no banco, sem consumir outra chave de plano.
3. Na primeira migração de um vínculo Daytona, o backend para a configuração das contas e limpa a seleção de vídeo/capa para evitar iniciar com arquivos inexistentes. Os vídeos físicos do Daytona **não são copiados automaticamente**. Na Biblioteca, exclua os registros antigos depois de garantir seu backup e envie os vídeos novamente. Desvincule vídeo/capa nas configurações das contas se algum registro estiver em uso. A exclusão é idempotente no novo volume. Selecione novamente vídeo/capa na configuração.
4. Clique em Parar na conta, salve configurações e configure uma URL RTMP válida. Não envie stream keys em prints.
5. Desative “Recuperar após falha” durante o primeiro teste, para não gastar crédito repetindo uma recusa.
6. Inicie **uma** live. Confira o estado no site e se o TikTok realmente recebe vídeo/áudio. “Transmitindo” indica processo vivo, não entrega confirmada ao TikTok.
7. Teste parar, loop, encerramento por validade e uma recuperação após reinício do serviço.
8. Só depois do teste e do backup, pare o sandbox antigo no painel Daytona. Não o exclua até confirmar que os arquivos foram transferidos. Esta versão não gerencia mais o sandbox antigo.

Em recuperações posteriores no Railway, uma configuração anterior marcada como running pode reiniciar. Pare a conta antes da migração. O worker restaura configs persistidas após reiniciar; haverá interrupção da live durante restart/deploy e pode ser necessário gerar nova chave TikTok.

## 5. Limites, custo e manutenção

Seu teste TCP Railway→TikTok:1935 passou, mas isso não confirma autenticação RTMP nem capacidade de codificar vídeos. Não prometemos 24 horas com apenas o crédito de teste. CPU, RAM, disco e saída de rede consomem o saldo. Mesmo um serviço ocioso ligado usa recursos.

O limite global de uma live protege o pequeno ambiente; aumentar MAX_CONCURRENT_STREAMS exige teste e recursos. Não basta ativar um plano Ultimate para obter 5 lives. Este worker tem isolamento por diretórios e autenticação administrativa compartilhada, não isolamento de VM por cliente; comece com uso próprio/piloto.

Se o worker ficar indisponível, o site continua acessível, mas upload/controle/transmissão ficam indisponíveis. Banco e volume são separados. Faça backup do PostgreSQL e dos vídeos. Não exponha config.json/config.pending nem Authorization nos logs.

O serviço limita dados a 300 MB e deixa espaço para montar arquivos (upload usa partes e arquivo final temporariamente). Se ocorrer HTTP 413, remova vídeos que não usa. Se os créditos acabarem, é necessário pagar ou migrar; não há renovação automática grátis garantida.

Os termos do Vercel Hobby restringem uso comercial; confira o plano adequado antes de vender o SaaS.

## Validação local

Instale requirements.txt e pytest, depois execute `python -m pytest -q` na raiz. Testes simulam provedores externos e incluem autenticação, separação de arquivos, upload em partes, limitação global de FFmpeg, stop e expiração. Não equivalem a um teste de live real.

Documentação oficial: https://docs.railway.com/volumes/reference ; https://docs.railway.com/builds/dockerfiles ; https://docs.railway.com/deployments/restart-policy ; https://docs.railway.com/pricing/free-trial
