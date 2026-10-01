# NexaTok — Vercel + Daytona

Esta versão substitui o worker contínuo do Render por operações limitadas à duração de uma requisição no Vercel. Não exige computador ligado nem Background Worker Render. O site e a API ficam no Vercel; FFmpeg, vídeos e supervisor ficam em um sandbox Daytona por usuário. Ainda exige PostgreSQL externo e créditos/recursos Daytona.

## Publicar no Vercel

1. Extraia o ZIP. Envie o conteúdo da pasta `nexatok` para um repositório GitHub privado. Na raiz devem aparecer `index.py`, `vercel.json`, `pyproject.toml`, `requirements.txt`, `app/` e `worker/`.
2. Tenha um PostgreSQL acessível pela internet. Pode reutilizar seu banco Render com **External Database URL**, ou usar outro PostgreSQL gerenciado. Não use Internal Database URL fora do Render. O Postgres gratuito Render expira em 30 dias; não é armazenamento gratuito permanente. Nunca use SQLite em produção Vercel.
3. No Vercel, escolha Add New → Project e importe o repositório. Configure Root Directory como a pasta que contém `index.py`. Use a detecção de FastAPI (Framework Preset FastAPI, quando disponível). Se havia configuração da versão antiga, limpe os overrides de Build Command, Install Command e Output Directory. Não configure `dist` nem o build de frontend antigo.
4. Em Environment Variables, adicione os valores abaixo para Production. Use valores próprios, não os exemplos.

| Variável | Valor |
|---|---|
| `APP_ENV` | `production` |
| `DATABASE_URL` | URL externa PostgreSQL completa, incluindo TLS recomendado pelo provedor |
| `APP_SECRET` | Segredo aleatório com 32+ caracteres |
| `ADMIN_EMAIL` | Seu e-mail em minúsculas |
| `ADMIN_BOOTSTRAP_TOKEN` | Código aleatório para o cadastro inicial do administrador |
| `DAYTONA_API_KEY` | Sua API key Daytona, com permissão de criar/modificar sandboxes |
| `DAYTONA_API_URL` | `https://app.daytona.io/api` |
| `DAYTONA_SNAPSHOT` | `nexatok-ffmpeg`, ou nome exato do seu snapshot |
| `PUBLIC_URL` | Origem final do site, por exemplo `https://seu-projeto.vercel.app` |
| `FRONTEND_URL` | A mesma origem de PUBLIC_URL |
| `CRON_SECRET` | Outro segredo aleatório com 32+ caracteres; protege a manutenção diária |

Gere os segredos no PowerShell: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Use um segredo diferente para cada finalidade. Nunca coloque os segredos no frontend, no GitHub ou em mensagens de suporte.

5. Faça deploy. Se a URL final ainda não era conhecida, atualize PUBLIC_URL/FRONTEND_URL e faça redeploy. As origens precisam ser exatas, sem `/` final. Preview deployments têm origem diferente; não são autorizados automaticamente.
6. Confira `https://SEU-DOMINIO/api/health`. Cadastre seu administrador usando ADMIN_EMAIL e o código de instalação. Depois de cadastrar, remova ADMIN_BOOTSTRAP_TOKEN do Vercel e faça redeploy.
7. Na Administração, gere uma chave e resgate-a em Plano & ambiente. A requisição espera a criação e preparação do ambiente; pode levar um ou dois minutos. Acompanhe o resultado na atividade recente.
8. Envie um vídeo, importe cookies TikTok e configure uma conta em Live studio. Inicie uma live de teste e confirme no TikTok se o vídeo está chegando.

O projeto exporta `app` em `index.py` e aponta explicitamente para `index:app`, evitando o erro de entrypoint ausente. Usa Python 3.12 e configuração de função com maxDuration 300 segundos. Ative/mantenha Fluid Compute compatível no projeto. Arquivos estáticos são servidos pelo suporte FastAPI/Vercel. Não crie um segundo serviço no Render e não execute `python -m worker.main`: esse módulo foi removido nesta versão.

## Preparar o snapshot Daytona (Windows)

O Dockerfile do snapshot continua em `worker/Dockerfile.daytona`; o supervisor não é um serviço Render.

Com Docker Desktop aberto e CLI Daytona instalada, entre na pasta principal `nexatok` e execute:

```powershell
daytona login
daytona snapshot create nexatok-ffmpeg --dockerfile ".\worker\Dockerfile.daytona" --cpu 2 --memory 4 --disk 10
daytona snapshot list
```

Se o PowerShell já estiver na pasta `worker`, use `--dockerfile ".\Dockerfile.daytona"`.

O snapshot precisa de Python 3, FFmpeg, bash, usuário `daytona` e `/home/daytona` gravável. Os 2 CPUs/4 GB/10 GB são apenas ponto de partida para testar uma live. Dimensione CPU, memória e disco pelos vídeos e quantidade de transmissões. Snapshots opcionais por plano: DAYTONA_SNAPSHOT_BASIC/PRO/ULTIMATE. Ambientes existentes são reaproveitados e não são redimensionados automaticamente na mudança de plano.

## Como as operações funcionam agora

- Ao ativar a chave, a API registra uma operação persistente e tenta executá-la dentro da mesma requisição. O ambiente é identificado por usuário e pode ser recuperado após interrupção.
- Start, stop, gerar RTMP, validar cookies e encerrar sala são chamados pela API; não existe um loop de worker fora do Daytona.
- Há uma trava por usuário com prazo de 330 segundos para evitar duas operações simultâneas sobrescrevendo o mesmo ambiente.
- O dashboard lê o heartbeat/status do supervisor. Não é responsável por manter a live no ar.
- Operações interrompidas ficam registradas. O dashboard tenta retomar operações pendentes enquanto estiver aberto, uma por requisição. A função `/api/operations/resume` também permite retomada autenticada.
- Provisionamento/start/stop/validação podem ser retomados depois de expirar a trava. Geração/encerramento de sala com resultado incerto não são repetidos automaticamente: confira no TikTok antes de solicitar novamente.
- Reinício programado e recuperação após falha continuam dentro do sandbox. FFmpeg ativo é diferente de confirmação de live publicada no TikTok.

Uma operação que exceder o tempo da função pode continuar/ter ocorrido no provedor mesmo se o navegador receber timeout. Aguarde cerca de seis minutos, abra o dashboard e use recuperar ambiente; não resgate outra chave para tentar corrigir provisionamento.

## Expiração sem navegador ou PC ligado

O supervisor recebe o vencimento do plano e interrompe FFmpeg localmente ao expirar, mesmo se a API não responder. Depois faz uma chamada autenticada à API para pedir suspensão do sandbox; o token é específico daquele usuário. A chave geral Daytona não é enviada ao sandbox do cliente.

A API confere o vencimento atualizado no banco. Se houve renovação, sincroniza a nova configuração. Se realmente expirou, solicita stop ao Daytona e marca o ambiente suspenso. O supervisor tenta novamente em caso de indisponibilidade, a cada 60 segundos.

Uma manutenção diária Vercel, protegida por CRON_SECRET, tenta suspender até cinco ambientes expirados por execução como recuperação adicional. No Hobby, o cron diário não tem horário exato e não substitui o prazo local do supervisor.

**Limite real:** se o Vercel, o banco ou a API Daytona estiver indisponível, as lives param pelo prazo local, mas o sandbox pode permanecer ligado e continuar consumindo recursos até a suspensão conseguir completar. Monitore saldo e ambientes no Daytona. A manutenção diária não promete suspensão imediata nem capacidade ilimitada para muitos usuários.

O endereço Production precisa estar acessível ao callback de máquina. Uma proteção Vercel que exija login adicional para toda a produção bloqueia o callback; confira essa configuração. A API de callback continua exigindo seu token específico.

## Vídeos grandes no Vercel

Vercel Functions têm limite de corpo por requisição. A biblioteca envia os arquivos em partes de **3 MiB**, cada uma por uma requisição separada; nenhuma função recebe o vídeo inteiro. As partes são armazenadas diretamente no sandbox e montadas lá ao final.

- Limite inicial por vídeo: 500 MB; por capa: 5 MB; armazenamento total por usuário: 2 GB.
- MAX_VIDEO_MB e MAX_STORAGE_MB permitem alterar os limites do aplicativo, respeitando o disco do snapshot e as quotas dos provedores.
- Cada envio tem reserva de espaço e uma posição confirmada. Partes repetidas com o mesmo hash não duplicam os dados.
- O frontend tenta reenviar uma parte até três vezes. Se falhar, mostra erro. Não há interface de retomada após fechar/recarregar a página; a API disponibiliza a posição do envio em `/api/uploads/{id}`.
- Reservas expiram em 24 horas. O supervisor remove partes abandonadas com mais de 48 horas enquanto estiver rodando. Um novo envio pode exigir aguardar expiração da reserva ou cancelar o envio pela API.
- Envios consomem requisições e transferência: dividir em partes resolve o limite de corpo, não elimina custos/quotas.

## Funções mantidas

Cadastro/login/logout e troca de senha; administrador protegido por código de instalação; Basic 3 dias/1 conta, Pro 7 dias/3 contas, Ultimate 12 dias/5 contas; ativação por chave de uso único, renovação preservando dias; contas por usuário e cookies criptografados; importação texto/JSON/Netscape e extensão Chrome; título/tópico/game tag/região/replay/maiores/capa; RTMP/stream key e link de live; vídeos em loop; controle de múltiplas contas; reinício programado, espera e recuperação após falha.

Login Kit QR é opcional: adicione TIKTOK_CLIENT_KEY e TIKTOK_CLIENT_SECRET de um aplicativo aprovado. Ele fornece autorização de perfil, não os cookies exigidos pelo gerador RTMP. Geração RTMP utiliza endpoints internos portados do app original; sua estabilidade e autorização dependem do TikTok. A conta precisa de acesso ao LIVE Studio; o site não libera essa autorização.

Para a extensão Chrome, carregue a pasta `extension` em `chrome://extensions` → Modo do desenvolvedor → Carregar sem compactação. Abra seu site, faça login no NexaTok, informe a origem exata na extensão e importe sua sessão TikTok. Para várias sessões, use perfis Chrome separados.

Não inclui checkout PIX/cartão. O administrador confirma pagamento fora do sistema e entrega a chave. Escolher um plano sem uma chave válida não libera ambiente.

## Custos e plano gratuito

Não precisa de Background Worker Render nem PC ligado. Isso não significa hospedagem e transmissões gratuitas sem limites: Daytona consome créditos/recursos; banco e Vercel têm limites de plano. Vercel Hobby é para uso pessoal/não comercial. Para vender planos, escolha um plano/provider que permita seu uso comercial; esta adaptação não contorna essas regras.

## Migração da versão anterior

Use a mesma APP_SECRET e DATABASE_URL se quiser preservar usuários e cookies. O código cria apenas duas tabelas novas (`environment_control` e `multipart_uploads`), sem adicionar colunas às tabelas existentes. Faça backup antes de qualquer mudança. O banco precisa estar acessível pelo Vercel. Não mantenha o worker antigo funcionando em paralelo com esta versão: ele pode sobrescrever configurações novas.

Se já há sandboxes com o supervisor antigo, pare as lives e pare esses sandboxes pelo painel Daytona antes de usar recuperar ambiente nesta versão. Isso encerra o supervisor antigo; ao recuperar, o novo supervisor será enviado e iniciado. Não delete os sandboxes para preservar os vídeos. Depois escolha manualmente iniciar as lives; configurações antigas de `desired=running` podem retomar ao recuperar o ambiente. Se ainda não criou usuários/ambientes, essa etapa não é necessária.

## Testes e limites da validação

```bash
pip install -r requirements.txt
pip install pytest
python -m pytest -q
node --check app/static/app.js
python -m compileall -q app worker
```

Os testes usam SQLite temporário e Daytona/TikTok simulados. O supervisor também é iniciado como processo real com FFmpeg simulado para testar start/stop/expiração. Não foi publicado em sua conta Vercel nem testado com suas credenciais Daytona/TikTok. Não houve inspeção visual completa do layout; confira no navegador desktop/celular antes de abrir para clientes.

Documentação usada:
- https://vercel.com/docs/frameworks/backend/fastapi
- https://vercel.com/docs/functions/limitations
- https://vercel.com/docs/cron-jobs/usage-and-pricing
- https://vercel.com/docs/plans/hobby
- https://www.daytona.io/docs/en/python-sdk/sync/daytona/
- https://www.daytona.io/docs/en/tools/cli/
