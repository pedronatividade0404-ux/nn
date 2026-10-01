# Validação — Vercel + Daytona

- 24 testes locais: autenticação, CSRF/origem, isolamento por usuário, limites e criptografia, renovação/uso único de chave, permissões admin, state/client_ticket QR, start/stop/expiração do supervisor, execução de provisionamento na requisição, trava por usuário, envio em partes com repetição idempotente e montagem simulada, callback de expiração autenticado, renovação consultada no banco, manutenção diária e proteção contra repetir uma criação de sala com resultado incerto.
- Sintaxe JavaScript e compilação Python verificadas.
- Entrypoint `index.py` exporta `app`; configuração Vercel sem rewrite para Render e sem build `dist`.
- Não há `worker/main.py` nem necessidade de Background Worker Render/PC ligado. `worker/agent.py` é enviado para o Daytona e executado lá.

Testes de provedor são simulados: falta validar deploy real no Vercel, PostgreSQL externo, snapshot/saldo Daytona e conta TikTok autorizada. O callback reduz dependência de cron, mas sandbox pode continuar consumindo recursos se a suspensão remota falhar. Vercel Hobby não é plano para SaaS comercial. Não inclui pagamentos automáticos.
