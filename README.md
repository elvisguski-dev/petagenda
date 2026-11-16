# 🐾 PetAgenda — banho e tosa autônomo

Sistema de agenda para pet shop que **agenda, confirma, lembra e responde clientes sozinho pelo WhatsApp**.
Só usa a biblioteca padrão do Python 3.9+ (não precisa instalar nada) e guarda tudo em um arquivo SQLite.

## Como rodar

```bash
cd petagenda
PAINEL_SENHA="troque-esta-senha" python3 server.py
```

- Painel: `http://localhost:8080/` (usuário: qualquer; senha: a que você definiu)
- Cliente agenda sozinho: `http://localhost:8080/agendar`
- Webhook de respostas: `http://SEU-DOMINIO:8080/webhook/whatsapp`

Variáveis opcionais: `PORT` (padrão 8080), `PETAGENDA_DB` (caminho do banco), `TZ` (padrão America/Sao_Paulo).

> **Antes de publicar na internet:** defina `PAINEL_SENHA`, coloque atrás de HTTPS (Caddy, Nginx ou Cloudflare Tunnel) e preencha o *Endereço público* em Configurações.

## O que acontece sozinho

| Momento | Mensagem automática |
|---|---|
| Cliente agenda (painel ou link online) | Confirmação com opções **1** confirmar / **2** cancelar |
| 24h antes | Lembrete pedindo confirmação |
| 2h antes | "Daqui a pouco, esperamos o pet" |
| Você marca **Concluir** | "Seu pet está pronto" + pós-atendimento no dia seguinte |
| X dias depois (padrão 30) | Lembrete de retorno com link de agendamento |
| Cliente responde 1 / 2 / 3 | Agenda atualiza sozinha e o cliente recebe a resposta |
| Cliente responde PARAR | Para mensagens de marketing (avisos de agendamento continuam) |
| Qualquer outra mensagem | Resposta com o link de agendamento (no máximo 1x a cada 12h) |

Mensagens de lembrete são descartadas automaticamente se o horário foi cancelado/remarcado, e o lembrete de retorno é descartado se o cliente já agendou de novo.

## Conectar o WhatsApp

Em **Configurações → Conexão WhatsApp** escolha o provedor e use **Enviar teste**.
O modo *Simulado* é o padrão: registra tudo mas não envia nada (ótimo para testar).

| Provedor | O que preencher | Webhook (respostas dos clientes) |
|---|---|---|
| Evolution API | URL, instância, API Key | evento `MESSAGES_UPSERT` apontando para `/webhook/whatsapp?token=SEU_TOKEN` |
| Z-API | ID da instância, token, client-token | "ao receber" apontando para `/webhook/whatsapp?token=SEU_TOKEN` |
| WhatsApp Cloud API (Meta) | Phone Number ID, token | URL de callback igual, *verify token* = seu token do webhook |

Cuidados importantes:
- **APIs não oficiais** (Evolution/Z-API) funcionam bem, mas o WhatsApp pode banir números que enviam muito. O sistema já usa janela de horário e intervalo aleatório entre envios; mantenha mensagens somente para clientes que já agendaram com você.
- **API oficial da Meta** só permite texto livre dentro de 24h após o cliente falar com você. Para lembretes fora dessa janela é preciso cadastrar *templates* aprovados na Meta (o sistema envia texto livre; adaptar para templates é a evolução natural).

## Estrutura

```
petagenda/
├── server.py      # servidor, API, agendador automático, WhatsApp, webhook
├── index.html     # painel do dono (agenda, clientes, serviços, mensagens, config)
├── agendar.html   # página pública de autoagendamento
└── petagenda.db   # criado no primeiro uso
```

## Para criar a linha de programas (como o OrçaZap)

Este projeto foi organizado para virar a base de uma família de produtos. Tudo que é específico de banho e tosa está em poucos pontos; o resto é reaproveitável:

- **Reaproveitável em qualquer nicho:** fila de mensagens, envio por provedor, webhook, respostas 1/2/3, opt-out, janela de envio, agendador, login do painel, página pública.
- **Específico do nicho (trocar por produto):** tabelas `pets`/`servicos` (preço por porte), textos em `TEMPLATES`, rótulos do painel.

Sugestão de próximos produtos com o mesmo núcleo: clínica veterinária (vacinas e retornos), barbearia/salão, oficina (revisões), clínica/consultório (consultas e faltas), academia.
