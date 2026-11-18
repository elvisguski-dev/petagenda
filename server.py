#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PetAgenda - agenda de banho e tosa com mensagens automaticas no WhatsApp.

Somente biblioteca padrao do Python 3.9+ (sem pip install).
Rodar:   python3 server.py          (abre em http://localhost:8080)
Senha:   PAINEL_SENHA="minhasenha" python3 server.py
"""
import base64
import hmac
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Console do Windows (cp1252) nao aceita emojis: nunca deixar um print derrubar o envio.
for _s in (sys.stdout, sys.stderr):
    if _s is not None and hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")


def carregar_env():
    """Le petagenda.env (CHAVE=VALOR) ao lado do server.py, sem sobrescrever variaveis ja definidas."""
    p = Path(os.environ.get("PETAGENDA_ENV") or (Path(__file__).resolve().parent / "petagenda.env"))
    try:
        linhas = p.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for ln in linhas:
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


carregar_env()

os.environ.setdefault("TZ", "America/Sao_Paulo")
if hasattr(time, "tzset"):
    time.tzset()

BASE = Path(__file__).resolve().parent
DB_PATH = os.environ.get("PETAGENDA_DB", str(BASE / "petagenda.db"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
SENHA = os.environ.get("PAINEL_SENHA", "")

FMT = "%Y-%m-%d %H:%M"
TS = "%Y-%m-%d %H:%M:%S"
DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
STATUS_VALIDOS = ("agendado", "confirmado", "em_atendimento", "concluido", "cancelado", "faltou")
ATIVOS = ("agendado", "confirmado", "em_atendimento")
MARKETING = ("pos", "retorno")
SECRETOS = ("evolution_apikey", "zapi_token", "zapi_client_token", "cloud_token", "webhook_token")
MASCARA = "********"

WAKE = threading.Event()
FILA_LOCK = threading.Lock()

# --------------------------------------------------------------------------
# Configuracao padrao e modelos de mensagem
# --------------------------------------------------------------------------
TEMPLATES = {
    "confirmacao": "Olá, {cliente}! 🐾 Seu horário no {negocio} está reservado:\n\n🐶 {pet}\n✂️ {servico}\n📅 {data} às {hora}\n💰 {valor}\n\nResponda *1* para CONFIRMAR ou *2* para CANCELAR.",
    "lembrete_24h": "Oi, {cliente}! Passando para lembrar do horário do(a) {pet}: {servico}, {data} às {hora}, no {negocio}. 🐾\n\nResponda *1* para confirmar ou *2* se precisar cancelar.",
    "lembrete_2h": "Daqui a pouco! 🛁 Esperamos o(a) {pet} hoje às {hora} para {servico}. Até já!",
    "pronto": "{cliente}, o(a) {pet} está pronto(a) e cheirosinho(a)! ✨🐕 Pode vir buscar. — {negocio}",
    "pos": "Oi, {cliente}! Como foi o atendimento do(a) {pet}? Conta pra gente, sua opinião ajuda muito 💙\n\nResponda PARAR se não quiser mais receber mensagens de acompanhamento.",
    "retorno": "Oi, {cliente}! Já faz {dias} dias do último banho do(a) {pet}. Que tal agendar o próximo? Reserve em: {link}\n\nResponda PARAR para não receber mais este lembrete.",
    "cancelado": "Cancelamos o horário do(a) {pet} ({data} às {hora}). Para reagendar acesse: {link}",
    "remarcado": "Horário atualizado! 🐾 {pet}: {servico}, {data} às {hora}.\n\nResponda *1* para confirmar ou *2* para cancelar.",
    "resp_confirmado": "Perfeito, {cliente}! ✅ Horário confirmado: {data} às {hora}. Te esperamos!",
    "resp_cancelado": "Tudo bem, {cliente}. Cancelamos o horário de {data} às {hora}. Para reagendar quando quiser: {link}",
    "resp_remarcar": "Para escolher um novo horário acesse: {link} 🐾",
    "resp_parar": "Pronto, {cliente}! Você não receberá mais lembretes de retorno. Os avisos dos seus agendamentos continuam normalmente.",
    "fallback": "Oi! Sou o assistente virtual do {negocio} 🐾 Para agendar um horário acesse: {link}\nSe precisar falar com uma pessoa, é só escrever aqui que respondemos assim que possível.",
}

DEFAULT_CONFIG = {
    "negocio": "Meu Pet Shop",
    "telefone_negocio": "",
    "endereco": "",
    "url_publica": "http://localhost:8080",
    "abre": "08:00",
    "fecha": "18:00",
    "pausa_inicio": "",
    "pausa_fim": "",
    "dias_funcionamento": [0, 1, 2, 3, 4, 5],  # 0 = segunda ... 6 = domingo
    "capacidade": 2,
    "passo_min": 30,
    "antecedencia_min_horas": 2,
    "janela_envio_inicio": "08:00",
    "janela_envio_fim": "20:00",
    "intervalo_envio_seg": 6,
    "lembrete_24h": True,
    "lembrete_2h": True,
    "pos_atendimento": True,
    "retorno_dias": 30,
    "resposta_automatica": True,
    "provedor": "simulado",  # simulado | evolution | zapi | cloud
    "evolution_url": "",
    "evolution_instancia": "",
    "evolution_apikey": "",
    "zapi_instancia": "",
    "zapi_token": "",
    "zapi_client_token": "",
    "cloud_phone_id": "",
    "cloud_token": "",
    "webhook_token": "",
    "templates": TEMPLATES,
}

SCHEMA = """
create table if not exists config(key text primary key, value text not null);
create table if not exists clientes(
  id integer primary key autoincrement, nome text not null, telefone text not null,
  obs text default '', optout integer default 0, criado text);
create table if not exists pets(
  id integer primary key autoincrement,
  cliente_id integer not null references clientes(id) on delete cascade,
  nome text not null, especie text default 'cão', raca text default '',
  porte text default 'M', obs text default '');
create table if not exists servicos(
  id integer primary key autoincrement, nome text not null,
  duracao_min integer not null default 60,
  preco_p real default 0, preco_m real default 0, preco_g real default 0,
  ativo integer default 1);
create table if not exists agendamentos(
  id integer primary key autoincrement,
  pet_id integer not null references pets(id) on delete cascade,
  servico_id integer not null references servicos(id),
  inicio text not null, fim text not null, status text not null default 'agendado',
  valor real default 0, obs text default '', origem text default 'painel', criado text,
  lembrete24_ok integer default 0, lembrete2_ok integer default 0);
create table if not exists mensagens(
  id integer primary key autoincrement, direcao text not null default 'saida',
  cliente_id integer, pet_id integer, agendamento_id integer,
  tipo text not null, telefone text not null, texto text not null,
  status text not null default 'pendente', prioridade integer default 0,
  enviar_em text, enviada_em text, tentativas integer default 0,
  erro text default '', criado text);
create index if not exists ix_ag_inicio on agendamentos(inicio);
create index if not exists ix_msg_status on mensagens(status, enviar_em);
"""

AG_SQL = """
select a.*, p.nome as pet_nome, p.porte, p.especie, p.cliente_id,
       c.nome as cliente_nome, c.telefone, c.optout as cliente_optout,
       s.nome as servico_nome
from agendamentos a
join pets p on p.id = a.pet_id
join clientes c on c.id = p.cliente_id
join servicos s on s.id = a.servico_id
"""


class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status
        self.msg = msg


# --------------------------------------------------------------------------
# Banco de dados
# --------------------------------------------------------------------------
def db():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("pragma foreign_keys=on")
    return c


WAKE_PEND = [False]


@contextmanager
def conn():
    c = db()
    try:
        yield c
        c.commit()
        if WAKE_PEND[0]:  # so acorda o agendador depois do commit, senao ele nao enxerga a mensagem
            WAKE_PEND[0] = False
            WAKE.set()
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.execute("pragma journal_mode=wal")
        c.executescript(SCHEMA)
        if not c.execute("select count(*) from servicos").fetchone()[0]:
            padrao = [
                ("Banho", 45, 50, 70, 90),
                ("Tosa", 60, 60, 80, 110),
                ("Banho + Tosa", 90, 90, 120, 160),
                ("Tosa higiênica", 30, 30, 40, 50),
                ("Hidratação", 30, 35, 45, 60),
            ]
            c.executemany(
                "insert into servicos(nome,duracao_min,preco_p,preco_m,preco_g) values(?,?,?,?,?)", padrao
            )


def get_config(c=None):
    def carregar(cx):
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        for r in cx.execute("select key, value from config"):
            try:
                cfg[r["key"]] = json.loads(r["value"])
            except ValueError:
                pass
        t = dict(TEMPLATES)
        t.update(cfg.get("templates") or {})
        cfg["templates"] = t
        return cfg

    if c is not None:
        return carregar(c)
    with conn() as cx:
        return carregar(cx)


def salvar_config(c, dados):
    atual = get_config(c)
    for k, v in dados.items():
        if k not in DEFAULT_CONFIG:
            continue
        if k in SECRETOS and v == MASCARA:
            continue
        if k == "templates" and isinstance(v, dict):
            t = dict(atual["templates"])
            t.update({kk: str(vv) for kk, vv in v.items() if kk in TEMPLATES})
            v = t
        if k == "dias_funcionamento":
            v = sorted({int(x) for x in v if 0 <= int(x) <= 6})
        if k in ("capacidade", "passo_min", "intervalo_envio_seg", "retorno_dias", "antecedencia_min_horas"):
            v = max(0, int(v))
            if k in ("capacidade", "passo_min"):
                v = max(1, v)
        if k in ("lembrete_24h", "lembrete_2h", "pos_atendimento", "resposta_automatica"):
            v = bool(v)
        if k == "provedor" and v not in ("simulado", "evolution", "zapi", "cloud"):
            raise ApiError(400, "Provedor inválido")
        c.execute("insert or replace into config(key,value) values(?,?)", (k, json.dumps(v, ensure_ascii=False)))


# --------------------------------------------------------------------------
# Utilitarios
# --------------------------------------------------------------------------
def agora():
    return datetime.now()


def norm_fone(s):
    d = re.sub(r"\D", "", s or "")
    d = d.lstrip("0")
    if len(d) in (10, 11):
        d = "55" + d
    return d


def primeiro_nome(n):
    return (n or "").strip().split(" ")[0].capitalize() if n else ""


def brl(v):
    s = f"{float(v or 0):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_data(dt):
    return f"{DIAS[dt.weekday()]}, {dt:%d/%m}"


def hm(s, ref):
    h, m = [int(x) for x in s.split(":")]
    return ref.replace(hour=h, minute=m, second=0, microsecond=0)


def link(cfg):
    return cfg["url_publica"].rstrip("/") + "/agendar"


def sem_acento(s):
    s = unicodedata.normalize("NFD", s or "")
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn").lower().strip()


def render(tpl, ctx):
    class D(dict):
        def __missing__(self, k):
            return "{" + k + "}"

    try:
        return tpl.format_map(D(ctx))
    except (ValueError, IndexError, KeyError):
        return tpl


def ctx_base(cfg):
    return {
        "cliente": "", "pet": "", "servico": "", "data": "", "hora": "", "valor": "",
        "negocio": cfg["negocio"], "link": link(cfg), "dias": cfg["retorno_dias"],
        "endereco": cfg["endereco"],
    }


def ctx_ag(cfg, a):
    ini = datetime.strptime(a["inicio"], FMT)
    x = ctx_base(cfg)
    x.update(
        cliente=primeiro_nome(a["cliente_nome"]), pet=a["pet_nome"], servico=a["servico_nome"],
        data=fmt_data(ini), hora=ini.strftime("%H:%M"), valor=brl(a["valor"]),
    )
    return x


def get_ag(c, ag_id):
    return c.execute(AG_SQL + " where a.id=?", (ag_id,)).fetchone()


def achar_cliente(c, fone):
    d = norm_fone(fone)
    if len(d) < 8:
        return None
    return c.execute("select * from clientes where telefone like ? order by id limit 1", ("%" + d[-8:],)).fetchone()


# --------------------------------------------------------------------------
# Horarios e agendamentos
# --------------------------------------------------------------------------
def ocupacao_max(c, ini, fim, ignorar=0):
    rows = c.execute(
        "select inicio, fim from agendamentos where status in ('agendado','confirmado','em_atendimento') "
        "and inicio < ? and fim > ? and id != ?",
        (fim.strftime(FMT), ini.strftime(FMT), ignorar),
    ).fetchall()
    minutos = int((fim - ini).total_seconds() // 60)
    mx = 0
    for m in range(0, max(minutos, 1), 15):
        s = (ini + timedelta(minutes=m)).strftime(FMT)
        mx = max(mx, sum(1 for r in rows if r["inicio"] <= s < r["fim"]))
    return mx


def slot_livre(c, cfg, ini, fim, antecedencia=False, ignorar=0):
    if ini.weekday() not in cfg["dias_funcionamento"]:
        return False, "Não atendemos neste dia"
    if ini < hm(cfg["abre"], ini) or fim > hm(cfg["fecha"], ini):
        return False, "Fora do horário de funcionamento"
    if cfg["pausa_inicio"] and cfg["pausa_fim"]:
        if ini < hm(cfg["pausa_fim"], ini) and fim > hm(cfg["pausa_inicio"], ini):
            return False, "Horário de pausa"
    if antecedencia and ini < agora() + timedelta(hours=cfg["antecedencia_min_horas"]):
        return False, "Horário muito próximo, escolha outro"
    if ocupacao_max(c, ini, fim, ignorar) >= cfg["capacidade"]:
        return False, "Horário lotado"
    return True, ""


def listar_horarios(c, cfg, data_str, servico_id, publico=False):
    try:
        dia = datetime.strptime(data_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise ApiError(400, "Data inválida")
    srv = c.execute("select * from servicos where id=? and ativo=1", (servico_id,)).fetchone()
    if not srv:
        raise ApiError(404, "Serviço não encontrado")
    dur = timedelta(minutes=srv["duracao_min"])
    t = hm(cfg["abre"], dia)
    fim_dia = hm(cfg["fecha"], dia)
    passo = timedelta(minutes=cfg["passo_min"])
    out = []
    while t + dur <= fim_dia:
        ok, _ = slot_livre(c, cfg, t, t + dur, antecedencia=publico)
        if ok:
            out.append(t.strftime("%H:%M"))
        t += passo
    return out


def criar_ag(c, cfg, pet_id, servico_id, inicio, obs="", origem="painel", forcar=False):
    pet = c.execute("select * from pets where id=?", (pet_id,)).fetchone()
    srv = c.execute("select * from servicos where id=? and ativo=1", (servico_id,)).fetchone()
    if not pet:
        raise ApiError(404, "Pet não encontrado")
    if not srv:
        raise ApiError(404, "Serviço não encontrado")
    try:
        ini = datetime.strptime(str(inicio)[:16].replace("T", " "), FMT)
    except ValueError:
        raise ApiError(400, "Data/hora inválida")
    fim = ini + timedelta(minutes=srv["duracao_min"])
    if not forcar:
        ok, motivo = slot_livre(c, cfg, ini, fim, antecedencia=(origem == "publico"))
        if not ok:
            raise ApiError(409, motivo)
    dup = c.execute(
        "select 1 from agendamentos where pet_id=? and status in ('agendado','confirmado','em_atendimento') "
        "and inicio < ? and fim > ?",
        (pet_id, fim.strftime(FMT), ini.strftime(FMT)),
    ).fetchone()
    if dup:
        raise ApiError(409, "Este pet já tem um horário neste período")
    valor = srv["preco_" + (pet["porte"] or "M").lower()]
    delta = ini - agora()
    cur = c.execute(
        "insert into agendamentos(pet_id,servico_id,inicio,fim,status,valor,obs,origem,criado,lembrete24_ok,lembrete2_ok) "
        "values(?,?,?,?, 'agendado', ?,?,?,?,?,?)",
        (pet_id, servico_id, ini.strftime(FMT), fim.strftime(FMT), valor, obs or "", origem,
         agora().strftime(TS), int(delta <= timedelta(hours=24)), int(delta <= timedelta(hours=2))),
    )
    ag_id = cur.lastrowid
    a = get_ag(c, ag_id)
    enfileirar_ag(c, cfg, a, "confirmacao", prioridade=1)
    return get_ag(c, ag_id)


def enfileirar(c, tipo, telefone, texto, cliente_id=None, pet_id=None, ag_id=None, enviar_em=None, prioridade=0):
    c.execute(
        "insert into mensagens(direcao,cliente_id,pet_id,agendamento_id,tipo,telefone,texto,status,prioridade,enviar_em,criado) "
        "values('saida',?,?,?,?,?,?, 'pendente', ?,?,?)",
        (cliente_id, pet_id, ag_id, tipo, telefone, texto, prioridade,
         (enviar_em or agora()).strftime(TS), agora().strftime(TS)),
    )
    WAKE_PEND[0] = True


def enfileirar_ag(c, cfg, a, chave, enviar_em=None, prioridade=0):
    if chave in MARKETING and a["cliente_optout"]:
        return
    texto = render(cfg["templates"][chave], ctx_ag(cfg, a))
    enfileirar(c, chave, a["telefone"], texto, a["cliente_id"], a["pet_id"], a["id"], enviar_em, prioridade)


def mudar_status(c, cfg, ag_id, novo, notificar=True):
    a = get_ag(c, ag_id)
    if not a:
        raise ApiError(404, "Agendamento não encontrado")
    if novo not in STATUS_VALIDOS:
        raise ApiError(400, "Status inválido")
    if a["status"] == novo:
        return a
    c.execute("update agendamentos set status=? where id=?", (novo, ag_id))
    if novo in ("cancelado", "faltou"):
        c.execute(
            "update mensagens set status='ignorada' where agendamento_id=? and status='pendente' "
            "and tipo in ('confirmacao','lembrete_24h','lembrete_2h','remarcado')", (ag_id,))
        if novo == "cancelado" and notificar and a["status"] in ATIVOS:
            enfileirar_ag(c, cfg, a, "cancelado", prioridade=1)
    if novo == "concluido":
        hoje = agora().strftime("%Y-%m-%d")
        if a["inicio"][:10] == hoje:
            enfileirar_ag(c, cfg, a, "pronto", prioridade=1)
        base = agora().replace(hour=10, minute=0, second=0, microsecond=0)
        if cfg["pos_atendimento"]:
            enfileirar_ag(c, cfg, a, "pos", enviar_em=base + timedelta(days=1))
        if cfg["retorno_dias"] > 0:
            c.execute("update mensagens set status='ignorada' where tipo='retorno' and status='pendente' and pet_id=?",
                      (a["pet_id"],))
            enfileirar_ag(c, cfg, a, "retorno", enviar_em=base + timedelta(days=cfg["retorno_dias"]))
    return get_ag(c, ag_id)


# --------------------------------------------------------------------------
# Envio de WhatsApp
# --------------------------------------------------------------------------
def post_json(url, body, headers, timeout=20):
    req = urllib.request.Request(
        url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Falha de conexão: {e.reason}")


def enviar_whatsapp(cfg, fone, texto):
    p = cfg["provedor"]
    if p == "simulado":
        print(f"[SIMULADO] -> {fone}: {texto[:80]!r}")
        return
    if p == "evolution":
        if not (cfg["evolution_url"] and cfg["evolution_instancia"] and cfg["evolution_apikey"]):
            raise RuntimeError("Evolution API não configurada")
        url = f"{cfg['evolution_url'].rstrip('/')}/message/sendText/{cfg['evolution_instancia']}"
        post_json(url, {"number": fone, "text": texto}, {"apikey": cfg["evolution_apikey"]})
    elif p == "zapi":
        if not (cfg["zapi_instancia"] and cfg["zapi_token"]):
            raise RuntimeError("Z-API não configurada")
        url = f"https://api.z-api.io/instances/{cfg['zapi_instancia']}/token/{cfg['zapi_token']}/send-text"
        h = {"Client-Token": cfg["zapi_client_token"]} if cfg["zapi_client_token"] else {}
        post_json(url, {"phone": fone, "message": texto}, h)
    elif p == "cloud":
        if not (cfg["cloud_phone_id"] and cfg["cloud_token"]):
            raise RuntimeError("WhatsApp Cloud API não configurada")
        url = f"https://graph.facebook.com/v20.0/{cfg['cloud_phone_id']}/messages"
        post_json(url, {"messaging_product": "whatsapp", "to": fone, "type": "text", "text": {"body": texto}},
                  {"Authorization": "Bearer " + cfg["cloud_token"]})
    else:
        raise RuntimeError("Provedor desconhecido")


def dentro_da_janela(cfg, dt):
    return hm(cfg["janela_envio_inicio"], dt) <= dt <= hm(cfg["janela_envio_fim"], dt)


def deve_ignorar(c, m, agora_dt):
    """Retorna motivo (str) se a mensagem nao faz mais sentido, senao None."""
    if m["agendamento_id"] and m["tipo"] in ("confirmacao", "lembrete_24h", "lembrete_2h", "remarcado"):
        a = get_ag(c, m["agendamento_id"])
        if not a or a["status"] not in ("agendado", "confirmado"):
            return "Agendamento não está mais ativo"
        if m["tipo"] != "confirmacao" and a["inicio"] <= agora_dt.strftime(FMT):
            return "Horário já começou"
    if m["tipo"] == "retorno" and m["cliente_id"]:
        futuro = c.execute(
            "select 1 from agendamentos a join pets p on p.id=a.pet_id where p.cliente_id=? "
            "and a.status in ('agendado','confirmado') and a.inicio > ?",
            (m["cliente_id"], agora_dt.strftime(FMT))).fetchone()
        if futuro:
            return "Cliente já tem novo agendamento"
    if m["tipo"] in MARKETING and m["cliente_id"]:
        cli = c.execute("select optout from clientes where id=?", (m["cliente_id"],)).fetchone()
        if cli and cli["optout"]:
            return "Cliente pediu para não receber"
    return None


def processar_fila(limite=5):
    enviados = 0
    with FILA_LOCK:
        cfg = get_config()
        for _ in range(100):
            if enviados >= limite:
                break
            with conn() as c:
                ag = agora()
                extra = "" if dentro_da_janela(cfg, ag) else " and prioridade>=1"
                m = c.execute(
                    "select * from mensagens where direcao='saida' and status='pendente' and enviar_em <= ?"
                    + extra + " order by prioridade desc, enviar_em, id limit 1", (ag.strftime(TS),)).fetchone()
                if not m:
                    break
                motivo = deve_ignorar(c, m, ag)
                if motivo:
                    c.execute("update mensagens set status='ignorada', erro=? where id=?", (motivo, m["id"]))
                    continue
                if enviados > 0 and cfg["provedor"] != "simulado":
                    c.commit()
                    time.sleep(random.uniform(0.7, 1.3) * cfg["intervalo_envio_seg"])
                try:
                    enviar_whatsapp(cfg, m["telefone"], m["texto"])
                    c.execute("update mensagens set status='enviada', enviada_em=?, tentativas=tentativas+1, erro='' where id=?",
                              (agora().strftime(TS), m["id"]))
                    enviados += 1
                except Exception as e:  # noqa: BLE001
                    t = m["tentativas"] + 1
                    if t >= 3:
                        c.execute("update mensagens set status='erro', tentativas=?, erro=? where id=?",
                                  (t, str(e)[:300], m["id"]))
                    else:
                        c.execute("update mensagens set tentativas=?, erro=?, enviar_em=? where id=?",
                                  (t, str(e)[:300], (agora() + timedelta(minutes=5)).strftime(TS), m["id"]))
    return enviados


def gerar_lembretes():
    cfg = get_config()
    ag = agora()
    with conn() as c:
        rows = c.execute(AG_SQL + " where a.status in ('agendado','confirmado') and a.inicio > ?",
                         (ag.strftime(FMT),)).fetchall()
        for a in rows:
            delta = datetime.strptime(a["inicio"], FMT) - ag
            if not a["lembrete24_ok"] and delta <= timedelta(hours=24):
                if cfg["lembrete_24h"]:
                    enfileirar_ag(c, cfg, a, "lembrete_24h")
                c.execute("update agendamentos set lembrete24_ok=1 where id=?", (a["id"],))
            if not a["lembrete2_ok"] and delta <= timedelta(hours=2):
                if cfg["lembrete_2h"]:
                    enfileirar_ag(c, cfg, a, "lembrete_2h")
                c.execute("update agendamentos set lembrete2_ok=1 where id=?", (a["id"],))


def tick():
    gerar_lembretes()
    processar_fila()


def agendador():
    while True:
        WAKE.clear()  # limpa ANTES do tick: um aviso que chegue durante o tick nao se perde
        try:
            tick()
        except Exception as e:  # noqa: BLE001
            print("erro no agendador:", e)
        WAKE.wait(20)


# --------------------------------------------------------------------------
# Mensagens recebidas (webhook)
# --------------------------------------------------------------------------
def extrair_mensagem(p):
    """Aceita Meta Cloud API, Evolution API, Z-API ou JSON generico. Retorna (fone, texto) ou None."""
    if not isinstance(p, dict):
        return None
    try:
        m = p["entry"][0]["changes"][0]["value"]["messages"][0]
        texto = (m.get("text") or {}).get("body") or (m.get("button") or {}).get("text") or ""
        return m["from"], texto
    except (KeyError, IndexError, TypeError):
        pass
    d = p.get("data")
    if isinstance(d, list) and d:
        d = d[0]
    if isinstance(d, dict) and isinstance(d.get("key"), dict):
        key = d["key"]
        if key.get("fromMe"):
            return None
        jid = key.get("remoteJid", "")
        if "@g.us" in jid:
            return None
        if "@lid" in jid:
            jid = key.get("remoteJidAlt") or d.get("senderPn") or jid
        msg = d.get("message") or {}
        texto = msg.get("conversation") or (msg.get("extendedTextMessage") or {}).get("text") or ""
        return jid.split("@")[0], texto
    if p.get("phone") and isinstance(p.get("text"), dict):
        if p.get("fromMe") or p.get("isGroup"):
            return None
        return p["phone"], p["text"].get("message", "")
    if p.get("telefone") and p.get("texto") is not None:
        return p["telefone"], str(p["texto"])
    return None


def tratar_entrada(fone, texto):
    cfg = get_config()
    fone = norm_fone(fone)
    t = re.sub(r"[^a-z0-9 ]", "", sem_acento(texto))
    primeira = t.split()[0] if t else ""
    with conn() as c:
        cli = achar_cliente(c, fone)
        c.execute(
            "insert into mensagens(direcao,cliente_id,tipo,telefone,texto,status,enviar_em,enviada_em,criado) "
            "values('entrada',?, 'resposta', ?,?, 'recebida', ?,?,?)",
            (cli["id"] if cli else None, fone, texto, agora().strftime(TS), agora().strftime(TS), agora().strftime(TS)))
        if not cfg["resposta_automatica"]:
            return
        ag = None
        limite = (agora() - timedelta(hours=1)).strftime(FMT)
        if cli:
            # a resposta vale para o agendamento da ultima mensagem enviada ao cliente (se ainda ativo)
            ult = c.execute(
                "select agendamento_id from mensagens where direcao='saida' and cliente_id=? and agendamento_id is not null "
                "and tipo in ('confirmacao','lembrete_24h','lembrete_2h','remarcado') order by id desc limit 10",
                (cli["id"],)).fetchall()
            for u in ult:
                cand = get_ag(c, u["agendamento_id"])
                if cand and cand["status"] in ("agendado", "confirmado") and cand["inicio"] > limite:
                    ag = cand
                    break
            if not ag:
                ag = c.execute(
                    AG_SQL + " where p.cliente_id=? and a.status in ('agendado','confirmado') and a.inicio > ? "
                    "order by a.inicio limit 1", (cli["id"], limite)).fetchone()
        chave = None
        if cli and primeira in ("parar", "sair", "stop"):
            c.execute("update clientes set optout=1 where id=?", (cli["id"],))
            c.execute("update mensagens set status='ignorada' where cliente_id=? and status='pendente' "
                      "and tipo in ('pos','retorno')", (cli["id"],))
            ctx = ctx_base(cfg)
            ctx["cliente"] = primeiro_nome(cli["nome"])
            enfileirar(c, "resp_parar", fone, render(cfg["templates"]["resp_parar"], ctx), cli["id"], prioridade=1)
            return
        extra = ""
        if ag and primeira in ("1", "sim", "s", "confirmo", "confirmar", "confirmado", "ok"):
            # confirma todos os pets do cliente no mesmo dia
            mesmos = c.execute(
                AG_SQL + " where p.cliente_id=? and a.status='agendado' and substr(a.inicio,1,10)=?",
                (cli["id"], ag["inicio"][:10])).fetchall()
            ids = {ag["id"]} | {x["id"] for x in mesmos}
            for i in ids:
                mudar_status(c, cfg, i, "confirmado")
            if len(ids) > 1:
                nomes = [get_ag(c, i)["pet_nome"] for i in sorted(ids)]
                extra = "\nPets confirmados: " + ", ".join(nomes)
            chave = "resp_confirmado"
        elif ag and primeira in ("2", "nao", "n", "cancelar", "cancela", "cancelo"):
            mudar_status(c, cfg, ag["id"], "cancelado", notificar=False)
            chave = "resp_cancelado"
        elif ag and primeira in ("3", "remarcar", "reagendar", "remarca"):
            chave = "resp_remarcar"
        if chave:
            ctx = ctx_ag(cfg, ag)
            enfileirar(c, chave, fone, render(cfg["templates"][chave], ctx) + extra, cli["id"], ag["pet_id"], ag["id"], prioridade=1)
            return
        # sem intencao reconhecida: responde com o link, no maximo 1 vez a cada 12 horas
        recente = c.execute(
            "select 1 from mensagens where telefone=? and tipo='fallback' and criado > ?",
            (fone, (agora() - timedelta(hours=12)).strftime(TS))).fetchone()
        if not recente:
            ctx = ctx_base(cfg)
            if cli:
                ctx["cliente"] = primeiro_nome(cli["nome"])
            enfileirar(c, "fallback", fone, render(cfg["templates"]["fallback"], ctx),
                       cli["id"] if cli else None, prioridade=1)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
ROUTES = []
RATE = {}


class Req:
    def __init__(self, method, path, q, body, args, ip, headers):
        self.method, self.path, self.q, self.body, self.args, self.ip, self.headers = (
            method, path, q, body, args, ip, headers)


class Raw:
    def __init__(self, text, status=200):
        self.text, self.status = text, status


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


def rate_ok(ip, limite=10, janela=3600):
    agora_t = time.time()
    lst = [t for t in RATE.get(ip, []) if agora_t - t < janela]
    if len(lst) >= limite:
        RATE[ip] = lst
        return False
    lst.append(agora_t)
    RATE[ip] = lst
    return True


def exigir(body, *campos):
    for k in campos:
        if body.get(k) in (None, ""):
            raise ApiError(400, f"Campo obrigatório: {k}")


def porte_ok(p):
    p = str(p or "M").upper()
    if p not in ("P", "M", "G"):
        raise ApiError(400, "Porte deve ser P, M ou G")
    return p


def fone_ok(s):
    f = norm_fone(s)
    if len(f) < 12:
        raise ApiError(400, "Telefone inválido. Use DDD + número, ex.: (51) 99999-9999")
    return f


def cliente_completo(c, row):
    d = dict(row)
    d["pets"] = [dict(p) for p in c.execute("select * from pets where cliente_id=? order by nome", (row["id"],))]
    return d


@route("GET", "/api/dashboard")
def r_dashboard(r):
    ag = agora()
    hoje = ag.strftime("%Y-%m-%d")
    with conn() as c:
        cfg = get_config(c)
        q1 = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
        semana = []
        for i in range(7):
            d = (ag + timedelta(days=i)).strftime("%Y-%m-%d")
            semana.append({"data": d, "total": q1(
                "select count(*) from agendamentos where substr(inicio,1,10)=? and status in ('agendado','confirmado','em_atendimento','concluido')", d)})
        hoje_lista = c.execute(
            AG_SQL + " where substr(a.inicio,1,10)=? and a.status!='cancelado' order by a.inicio", (hoje,)).fetchall()
        return {
            "provedor": cfg["provedor"],
            "hoje": q1("select count(*) from agendamentos where substr(inicio,1,10)=? and status not in ('cancelado')", hoje),
            "a_confirmar": q1("select count(*) from agendamentos where status='agendado' and inicio > ?", ag.strftime(FMT)),
            "fila": q1("select count(*) from mensagens where direcao='saida' and status='pendente'"),
            "enviadas_hoje": q1("select count(*) from mensagens where direcao='saida' and status='enviada' and substr(enviada_em,1,10)=?", hoje),
            "recebidas_hoje": q1("select count(*) from mensagens where direcao='entrada' and substr(criado,1,10)=?", hoje),
            "erros": q1("select count(*) from mensagens where status='erro'"),
            "receita_hoje": q1("select coalesce(sum(valor),0) from agendamentos where status='concluido' and substr(inicio,1,10)=?", hoje),
            "hoje_lista": [dict(x) for x in hoje_lista],
            "semana": semana,
        }


# ---- clientes / pets ----
@route("GET", "/api/clientes")
def r_clientes(r):
    q = sem_acento(r.q.get("q", ""))
    with conn() as c:
        out = [cliente_completo(c, x) for x in c.execute("select * from clientes order by nome")]
    if q:
        out = [x for x in out if q in sem_acento(x["nome"]) or q in x["telefone"]
               or any(q in sem_acento(p["nome"]) for p in x["pets"])]
    return out


def gravar_pets(c, cliente_id, pets, sincronizar):
    manter = []
    for p in pets or []:
        if not str(p.get("nome", "")).strip():
            continue
        vals = (p["nome"].strip(), p.get("especie") or "cão", p.get("raca") or "", porte_ok(p.get("porte")), p.get("obs") or "")
        if p.get("id"):
            c.execute("update pets set nome=?, especie=?, raca=?, porte=?, obs=? where id=? and cliente_id=?",
                      vals + (p["id"], cliente_id))
            manter.append(int(p["id"]))
        else:
            cur = c.execute("insert into pets(cliente_id,nome,especie,raca,porte,obs) values(?,?,?,?,?,?)",
                            (cliente_id,) + vals)
            manter.append(cur.lastrowid)
    if sincronizar:
        for x in c.execute("select id from pets where cliente_id=?", (cliente_id,)).fetchall():
            if x["id"] not in manter:
                c.execute("delete from pets where id=?", (x["id"],))


@route("POST", "/api/clientes")
def r_cliente_novo(r):
    exigir(r.body, "nome", "telefone")
    fone = fone_ok(r.body["telefone"])
    with conn() as c:
        if achar_cliente(c, fone):
            raise ApiError(409, "Já existe um cliente com este telefone")
        cur = c.execute("insert into clientes(nome,telefone,obs,criado) values(?,?,?,?)",
                        (r.body["nome"].strip(), fone, r.body.get("obs", ""), agora().strftime(TS)))
        gravar_pets(c, cur.lastrowid, r.body.get("pets"), False)
        return cliente_completo(c, c.execute("select * from clientes where id=?", (cur.lastrowid,)).fetchone())


@route("PUT", r"/api/clientes/(\d+)")
def r_cliente_edit(r):
    exigir(r.body, "nome", "telefone")
    cid = int(r.args[0])
    fone = fone_ok(r.body["telefone"])
    with conn() as c:
        if not c.execute("select 1 from clientes where id=?", (cid,)).fetchone():
            raise ApiError(404, "Cliente não encontrado")
        c.execute("update clientes set nome=?, telefone=?, obs=?, optout=? where id=?",
                  (r.body["nome"].strip(), fone, r.body.get("obs", ""), int(bool(r.body.get("optout"))), cid))
        if "pets" in r.body:
            gravar_pets(c, cid, r.body["pets"], True)
        return cliente_completo(c, c.execute("select * from clientes where id=?", (cid,)).fetchone())


@route("DELETE", r"/api/clientes/(\d+)")
def r_cliente_del(r):
    with conn() as c:
        c.execute("delete from clientes where id=?", (int(r.args[0]),))
    return {"ok": True}


# ---- servicos ----
@route("GET", "/api/servicos")
def r_servicos(r):
    with conn() as c:
        return [dict(x) for x in c.execute("select * from servicos where ativo=1 order by nome")]


def vals_servico(b):
    exigir(b, "nome")
    dur = int(b.get("duracao_min") or 0)
    if dur < 15:
        raise ApiError(400, "Duração mínima: 15 minutos")
    return (b["nome"].strip(), dur, float(b.get("preco_p") or 0), float(b.get("preco_m") or 0), float(b.get("preco_g") or 0))


@route("POST", "/api/servicos")
def r_servico_novo(r):
    with conn() as c:
        cur = c.execute("insert into servicos(nome,duracao_min,preco_p,preco_m,preco_g) values(?,?,?,?,?)",
                        vals_servico(r.body))
        return dict(c.execute("select * from servicos where id=?", (cur.lastrowid,)).fetchone())


@route("PUT", r"/api/servicos/(\d+)")
def r_servico_edit(r):
    with conn() as c:
        c.execute("update servicos set nome=?, duracao_min=?, preco_p=?, preco_m=?, preco_g=? where id=?",
                  vals_servico(r.body) + (int(r.args[0]),))
        return dict(c.execute("select * from servicos where id=?", (int(r.args[0]),)).fetchone())


@route("DELETE", r"/api/servicos/(\d+)")
def r_servico_del(r):
    with conn() as c:
        c.execute("update servicos set ativo=0 where id=?", (int(r.args[0]),))
    return {"ok": True}


# ---- agendamentos ----
@route("GET", "/api/agendamentos")
def r_ags(r):
    hoje = agora().strftime("%Y-%m-%d")
    de = r.q.get("de") or hoje
    ate = r.q.get("ate") or de
    sql = AG_SQL + " where substr(a.inicio,1,10) between ? and ?"
    params = [de, ate]
    if r.q.get("status"):
        sql += " and a.status=?"
        params.append(r.q["status"])
    with conn() as c:
        return [dict(x) for x in c.execute(sql + " order by a.inicio", params)]


@route("GET", "/api/horarios")
def r_horarios(r):
    with conn() as c:
        return listar_horarios(c, get_config(c), r.q.get("data"), int(r.q.get("servico_id") or 0))


@route("POST", "/api/agendamentos")
def r_ag_novo(r):
    exigir(r.body, "pet_id", "servico_id", "inicio")
    with conn() as c:
        a = criar_ag(c, get_config(c), int(r.body["pet_id"]), int(r.body["servico_id"]), r.body["inicio"],
                     r.body.get("obs", ""), "painel", bool(r.body.get("forcar")))
        return dict(a)


@route("PUT", r"/api/agendamentos/(\d+)")
def r_ag_edit(r):
    ag_id = int(r.args[0])
    with conn() as c:
        cfg = get_config(c)
        a = get_ag(c, ag_id)
        if not a:
            raise ApiError(404, "Agendamento não encontrado")
        srv_id = int(r.body.get("servico_id") or a["servico_id"])
        srv = c.execute("select * from servicos where id=?", (srv_id,)).fetchone()
        try:
            ini = datetime.strptime(str(r.body.get("inicio") or a["inicio"])[:16].replace("T", " "), FMT)
        except ValueError:
            raise ApiError(400, "Data/hora inválida")
        fim = ini + timedelta(minutes=srv["duracao_min"])
        if not r.body.get("forcar"):
            ok, motivo = slot_livre(c, cfg, ini, fim, ignorar=ag_id)
            if not ok:
                raise ApiError(409, motivo)
        mudou = ini.strftime(FMT) != a["inicio"]
        delta = ini - agora()
        c.execute(
            "update agendamentos set servico_id=?, inicio=?, fim=?, obs=?, valor=?, "
            "lembrete24_ok=?, lembrete2_ok=? where id=?",
            (srv_id, ini.strftime(FMT), fim.strftime(FMT), r.body.get("obs", a["obs"]),
             srv["preco_" + (a["porte"] or "M").lower()],
             int(delta <= timedelta(hours=24)), int(delta <= timedelta(hours=2)), ag_id))
        if mudou and a["status"] in ("agendado", "confirmado"):
            c.execute("update agendamentos set status='agendado' where id=?", (ag_id,))
            c.execute("update mensagens set status='ignorada' where agendamento_id=? and status='pendente' "
                      "and tipo in ('confirmacao','lembrete_24h','lembrete_2h','remarcado')", (ag_id,))
            enfileirar_ag(c, cfg, get_ag(c, ag_id), "remarcado", prioridade=1)
        return dict(get_ag(c, ag_id))


@route("POST", r"/api/agendamentos/(\d+)/status")
def r_ag_status(r):
    exigir(r.body, "status")
    with conn() as c:
        return dict(mudar_status(c, get_config(c), int(r.args[0]), r.body["status"]))


@route("DELETE", r"/api/agendamentos/(\d+)")
def r_ag_del(r):
    with conn() as c:
        c.execute("delete from agendamentos where id=?", (int(r.args[0]),))
    return {"ok": True}


# ---- mensagens ----
@route("GET", "/api/mensagens")
def r_msgs(r):
    sql = ("select m.*, c.nome as cliente_nome from mensagens m left join clientes c on c.id=m.cliente_id")
    params = []
    if r.q.get("status"):
        sql += " where m.status=?"
        params.append(r.q["status"])
    sql += " order by m.id desc limit ?"
    params.append(min(int(r.q.get("limite") or 200), 500))
    with conn() as c:
        return [dict(x) for x in c.execute(sql, params)]


@route("POST", "/api/mensagens/enviar")
def r_msg_manual(r):
    exigir(r.body, "cliente_id", "texto")
    with conn() as c:
        cli = c.execute("select * from clientes where id=?", (int(r.body["cliente_id"]),)).fetchone()
        if not cli:
            raise ApiError(404, "Cliente não encontrado")
        enfileirar(c, "manual", cli["telefone"], str(r.body["texto"]), cli["id"], prioridade=1)
    return {"ok": True}


@route("POST", r"/api/mensagens/(\d+)/reenviar")
def r_msg_reenviar(r):
    with conn() as c:
        c.execute("update mensagens set status='pendente', tentativas=0, erro='', prioridade=1, enviar_em=? "
                  "where id=? and direcao='saida'", (agora().strftime(TS), int(r.args[0])))
    WAKE.set()
    return {"ok": True}


@route("DELETE", r"/api/mensagens/(\d+)")
def r_msg_cancelar(r):
    with conn() as c:
        c.execute("update mensagens set status='cancelada' where id=? and status='pendente'", (int(r.args[0]),))
    return {"ok": True}


@route("POST", "/api/fila/processar")
def r_fila(r):
    gerar_lembretes()
    return {"enviadas": processar_fila(limite=20)}


# ---- configuracao ----
@route("GET", "/api/config")
def r_config_get(r):
    cfg = get_config()
    for k in SECRETOS:
        if cfg.get(k):
            cfg[k] = MASCARA
    cfg["templates_padrao"] = TEMPLATES
    return cfg


@route("PUT", "/api/config")
def r_config_put(r):
    with conn() as c:
        salvar_config(c, r.body)
    return {"ok": True}


@route("POST", "/api/config/testar")
def r_config_testar(r):
    exigir(r.body, "telefone")
    fone = fone_ok(r.body["telefone"])
    cfg = get_config()
    with conn() as c:
        enfileirar(c, "teste", fone,
                   f"✅ Teste do PetAgenda — {cfg['negocio']}. Se você recebeu esta mensagem, o WhatsApp está conectado!",
                   prioridade=1)
        mid = c.execute("select max(id) from mensagens").fetchone()[0]
    processar_fila(limite=1)
    with conn() as c:
        m = c.execute("select status, erro from mensagens where id=?", (mid,)).fetchone()
    return {"status": m["status"], "erro": m["erro"], "provedor": cfg["provedor"]}


# ---- webhook ----
def webhook_autorizado(r, cfg):
    tok = cfg.get("webhook_token") or ""
    if not tok:
        return True
    enviado = r.q.get("token") or r.headers.get("X-Webhook-Token") or ""
    return hmac.compare_digest(enviado, tok)


@route("GET", "/webhook/whatsapp")
def r_webhook_verifica(r):
    cfg = get_config()  # verificacao da Meta Cloud API
    if r.q.get("hub.mode") == "subscribe" and hmac.compare_digest(r.q.get("hub.verify_token", ""), cfg.get("webhook_token") or "x-"):
        return Raw(r.q.get("hub.challenge", ""))
    return Raw("PetAgenda webhook ativo")


@route("POST", "/webhook/whatsapp")
def r_webhook(r):
    cfg = get_config()
    if not webhook_autorizado(r, cfg):
        raise ApiError(403, "Token inválido")
    msg = extrair_mensagem(r.body)
    if msg and msg[0]:
        tratar_entrada(msg[0], msg[1])
    return {"ok": True}


# ---- publico (pagina de autoagendamento) ----
@route("GET", "/api/publico/info")
def r_pub_info(r):
    with conn() as c:
        cfg = get_config(c)
        srv = [dict(x) for x in c.execute("select id,nome,duracao_min,preco_p,preco_m,preco_g from servicos where ativo=1 order by nome")]
    return {k: cfg[k] for k in ("negocio", "telefone_negocio", "endereco", "abre", "fecha", "dias_funcionamento")} | {"servicos": srv}


@route("GET", "/api/publico/horarios")
def r_pub_horarios(r):
    with conn() as c:
        return listar_horarios(c, get_config(c), r.q.get("data"), int(r.q.get("servico_id") or 0), publico=True)


@route("POST", "/api/publico/agendar")
def r_pub_agendar(r):
    b = r.body
    exigir(b, "nome", "telefone", "pet_nome", "servico_id", "inicio")
    if not rate_ok(r.ip):
        raise ApiError(429, "Muitas tentativas. Tente novamente mais tarde.")
    fone = fone_ok(b["telefone"])
    porte = porte_ok(b.get("porte"))
    with conn() as c:
        cfg = get_config(c)
        cli = achar_cliente(c, fone)
        if cli:
            futuros = c.execute(
                "select count(*) from agendamentos a join pets p on p.id=a.pet_id where p.cliente_id=? "
                "and a.status in ('agendado','confirmado') and a.inicio > ?", (cli["id"], agora().strftime(FMT))).fetchone()[0]
            if futuros >= 3:
                raise ApiError(409, "Você já tem 3 horários marcados. Para mais, fale com a gente pelo WhatsApp.")
            cid = cli["id"]
        else:
            cid = c.execute("insert into clientes(nome,telefone,criado) values(?,?,?)",
                            (str(b["nome"]).strip()[:80], fone, agora().strftime(TS))).lastrowid
        pet = c.execute("select * from pets where cliente_id=? and lower(nome)=lower(?)", (cid, str(b["pet_nome"]).strip())).fetchone()
        if pet:
            pid = pet["id"]
        else:
            pid = c.execute("insert into pets(cliente_id,nome,especie,raca,porte) values(?,?,?,?,?)",
                            (cid, str(b["pet_nome"]).strip()[:60], b.get("especie") or "cão", str(b.get("raca") or "")[:60], porte)).lastrowid
        a = criar_ag(c, cfg, pid, int(b["servico_id"]), b["inicio"], "", "publico")
        ini = datetime.strptime(a["inicio"], FMT)
        return {"ok": True, "pet": a["pet_nome"], "servico": a["servico_nome"], "data": fmt_data(ini),
                "hora": ini.strftime("%H:%M"), "valor": a["valor"]}


# --------------------------------------------------------------------------
# Servidor HTTP
# --------------------------------------------------------------------------
class H(BaseHTTPRequestHandler):
    server_version = "PetAgenda/1.0"

    def log_message(self, fmt, *args):
        pass

    def _auth_ok(self, path):
        if not SENHA:
            return True
        if path == "/agendar" or path.startswith("/api/publico/") or path.startswith("/webhook/"):
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                _, _, pw = base64.b64decode(h[6:]).decode().partition(":")
            except Exception:  # noqa: BLE001
                return False
            return hmac.compare_digest(pw, SENHA)
        return False

    def _send(self, status, data, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _file(self, nome):
        p = BASE / nome
        if not p.exists():
            return self._json(404, {"erro": "Arquivo não encontrado"})
        self._send(200, p.read_bytes(), "text/html; charset=utf-8")

    def _run(self, method):
        u = urllib.parse.urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if not self._auth_ok(path):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="PetAgenda"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "GET" and path in ("/", "/index.html"):
            return self._file("index.html")
        if method == "GET" and path == "/agendar":
            return self._file("agendar.html")
        body = {}
        if method in ("POST", "PUT"):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                return self._json(400, {"erro": "JSON inválido"})
        ip = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
        achou = False
        for m, rx, fn in ROUTES:
            mt = rx.match(path)
            if not mt:
                continue
            achou = True
            if m != method:
                continue
            try:
                res = fn(Req(method, path, q, body, mt.groups(), ip, self.headers))
            except ApiError as e:
                return self._json(e.status, {"erro": e.msg})
            except Exception as e:  # noqa: BLE001
                print("erro:", method, path, repr(e))
                return self._json(500, {"erro": "Erro interno: " + str(e)})
            if isinstance(res, Raw):
                return self._send(res.status, res.text.encode(), "text/plain; charset=utf-8")
            return self._json(200, res)
        self._json(405 if achou else 404, {"erro": "Rota não encontrada"})

    def do_GET(self):
        self._run("GET")

    def do_POST(self):
        self._run("POST")

    def do_PUT(self):
        self._run("PUT")

    def do_DELETE(self):
        self._run("DELETE")


def main():
    init_db()
    threading.Thread(target=agendador, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print(f"PetAgenda rodando em http://localhost:{PORT}")
    print(f"  Painel:            http://localhost:{PORT}/")
    print(f"  Agendamento online: http://localhost:{PORT}/agendar")
    print(f"  Webhook WhatsApp:  http://SEU-DOMINIO:{PORT}/webhook/whatsapp")
    print("  Senha do painel:  " + ("ATIVADA" if SENHA else "desativada (defina PAINEL_SENHA antes de publicar na internet)"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado.")


if __name__ == "__main__":
    main()
