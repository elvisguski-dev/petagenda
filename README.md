# 🐾 PetAgenda — banho e tosa autônomo

Sistema de agenda para pet shop que **agenda, confirma, lembra e responde clientes sozinho pelo WhatsApp**.
Só usa a biblioteca padrão do Python 3.9+ (não precisa instalar nada) e guarda tudo em um arquivo SQLite.

> 🖥️ **Visual:** painel e página do cliente vêm no **tema hacker** (preto + verde neon). O botão no topo alterna para o tema claro original.

## Instalar no Windows (recomendado)

1. Baixe o projeto (botão verde **Code → Download ZIP**) e **extraia o ZIP inteiro**.
2. Abra a pasta `instalador` e dê dois cliques em **`INSTALAR.bat`**.
3. Responda as perguntas (porta, senha do painel e se deve iniciar com o Windows) e pronto.

O instalador procura o Python 3.9+ (tenta instalar pelo `winget` se não achar), copia o programa para
`%LOCALAPPDATA%\PetAgenda`, cria atalhos na Área de Trabalho e no Menu Iniciar e registra o programa em
*Configurações → Aplicativos* para desinstalar depois. **Não precisa de administrador.**
Rodar o `INSTALAR.bat` de novo **atualiza** o programa e mantém a senha, a configuração e todos os dados.

Detalhes úteis:
- O sistema só envia lembretes e respostas **enquanto o computador estiver ligado e o PetAgenda rodando**. Por isso o instalador oferece iniciar junto com o Windows.
- Configuração em `%LOCALAPPDATA%\PetAgenda\petagenda.env` (`PORT`, `HOST`, `PAINEL_SENHA`). Por padrão só este computador acessa (`HOST=127.0.0.1`); use `0.0.0.0` para acessar de outros aparelhos da rede.
- Ao desinstalar, ele pergunta se deve guardar uma cópia dos dados em *Documentos\PetAgenda-backup*.
- Logs em `%LOCALAPPDATA%\PetAgenda\logs`.

## Como rodar (manual, qualquer sistema)

```bash
cd petagenda
PAINEL_SENHA="troque-esta-senha" python3 server.py
```

- Painel: `http://localhost:8080/` (usuário: qualquer; senha: a que você definiu)
- Cliente agenda sozinho: `http://localhost:8080/agendar`
- Webhook de respostas: `http://SEU-DOMINIO:8080/webhook/whatsapp`

Variáveis opcionais: `PORT` (padrão 8080), `HOST` (padrão 0.0.0.0), `PETAGENDA_DB` (caminho do banco), `TZ`. Também podem ficar num arquivo `petagenda.env` ao lado do `server.py` (uma linha `CHAVE=valor`).

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
├── instalador/    # instalador para Windows (INSTALAR.bat + scripts + ícone)
└── petagenda.db   # criado no primeiro uso
```

## Para criar a linha de programas (como o OrçaZap)

Este projeto foi organizado para virar a base de uma família de produtos. Tudo que é específico de banho e tosa está em poucos pontos; o resto é reaproveitável:

- **Reaproveitável em qualquer nicho:** fila de mensagens, envio por provedor, webhook, respostas 1/2/3, opt-out, janela de envio, agendador, login do painel, página pública.
- **Específico do nicho (trocar por produto):** tabelas `pets`/`servicos` (preço por porte), textos em `TEMPLATES`, rótulos do painel.

Sugestão de próximos produtos com o mesmo núcleo: clínica veterinária (vacinas e retornos), barbearia/salão, oficina (revisões), clínica/consultório (consultas e faltas), academia.
