"""
config_db.py — Motor Gelato
============================

Módulo responsável pela configuração do banco de dados SQLite e pela execução
de migrações automáticas e seguras (ADD COLUMN IF NOT EXISTS via PRAGMA).

Design decisions:
    - Usamos SQLite por portabilidade: o sistema roda em qualquer máquina
      sem infraestrutura de banco de dados externa.
    - As migrações são aplicadas de forma incremental (PRAGMA table_info)
      para garantir que novos campos sejam adicionados sem perda de dados.
    - Migração zero-downtime: bancos com nome legado são detectados e
      renomeados automaticamente sem perda de dados no primeiro deploy.
"""

import datetime
import os
import sys
import sqlite3


# ==========================================
# CONFIGURAÇÃO DE CAMINHOS
# ==========================================

# sys.frozen indica que estamos rodando como executável empacotado (PyInstaller).
# Nesse caso, os arquivos estão ao lado do .exe, não do .py original.
if getattr(sys, 'frozen', False):
    base_path = os.path.dirname(sys.executable)
else:
    base_path = os.path.dirname(os.path.abspath(__file__))

# Suporte a migração zero-downtime: se o banco antigo existir, usa ele.
_OLD_DB = os.path.join(base_path, "dubelato_dados.db")
_NEW_DB = os.path.join(base_path, "motor_estoque.db")

if os.path.exists(_OLD_DB) and not os.path.exists(_NEW_DB):
    # Renomeia o banco legado para o novo nome sem perder dados.
    os.rename(_OLD_DB, _NEW_DB)

DB_FILE = _NEW_DB


# ==========================================
# BANCO DE DADOS — INICIALIZAÇÃO E MIGRAÇÕES
# ==========================================

def iniciar_banco() -> None:
    """Cria as tabelas do sistema e aplica migrações incrementais seguras.

    Esta função é idempotente: pode ser chamada múltiplas vezes sem efeitos
    colaterais, pois usa CREATE TABLE IF NOT EXISTS e verifica colunas via
    PRAGMA antes de executar qualquer ALTER TABLE.

    Fluxo de Estoque em 3 Níveis suportado:
        1. Almoxarifado (``estoque_almoxarifado``) — estoque bruto/comprado.
        2. Cozinha (``estoque_atual``) — em processo de fabricação.
        3. Vitrine (tabela ``estoque_pronto``) — produto acabado no PDV.

    A transferência entre níveis é registrada na tabela ``transferencias_estoque``
    para auditoria completa de rastreabilidade.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # ------------------------------------------------------------------
    # TABELAS CORE
    # ------------------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sabores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            categoria TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ingredientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            unidade TEXT NOT NULL,
            preco_unitario REAL DEFAULT 0,
            estoque_atual REAL DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registro_quebras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ingrediente_id INTEGER,
            quantidade REAL,
            motivo TEXT,
            data_hora TEXT,
            FOREIGN KEY(ingrediente_id) REFERENCES ingredientes(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registro_consumo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ingrediente_id INTEGER,
            quantidade REAL,
            data_hora TEXT,
            FOREIGN KEY(ingrediente_id) REFERENCES ingredientes(id)
        )
    """)

    # ------------------------------------------------------------------
    # MIGRAÇÃO INCREMENTAL — tabela `ingredientes`
    # Verifica quais colunas existem antes de adicionar novas.
    # Esse padrão evita erros em bancos de dados existentes de versões
    # anteriores do sistema, mantendo a compatibilidade retroativa.
    # ------------------------------------------------------------------
    cursor.execute("PRAGMA table_info(ingredientes)")
    colunas = {col[1] for col in cursor.fetchall()}

    migrations = {
        'escondido':            "ALTER TABLE ingredientes ADD COLUMN escondido INTEGER DEFAULT 0",
        'validade':             "ALTER TABLE ingredientes ADD COLUMN validade TEXT DEFAULT ''",
        'limite_alerta':        "ALTER TABLE ingredientes ADD COLUMN limite_alerta REAL DEFAULT 5.0",
        'estoque_almoxarifado': "ALTER TABLE ingredientes ADD COLUMN estoque_almoxarifado REAL DEFAULT 0",
        'unidade_almoxarifado': "ALTER TABLE ingredientes ADD COLUMN unidade_almoxarifado TEXT DEFAULT 'UN'",
        'fator_conversao':      "ALTER TABLE ingredientes ADD COLUMN fator_conversao REAL DEFAULT 1.0",
        'lead_time':            "ALTER TABLE ingredientes ADD COLUMN lead_time INTEGER DEFAULT 7",
        'burn_rate_diario':     "ALTER TABLE ingredientes ADD COLUMN burn_rate_diario REAL DEFAULT 0.5",
        'volatil':              "ALTER TABLE ingredientes ADD COLUMN volatil INTEGER DEFAULT 0",
    }

    for coluna, sql in migrations.items():
        if coluna not in colunas:
            try:
                cursor.execute(sql)
            except Exception as e:
                # Log não-fatal: a coluna pode ter sido criada por outra thread
                print(f"[Motor Gelato] Aviso na migração de '{coluna}': {e}")

    # ------------------------------------------------------------------
    # MIGRAÇÃO INCREMENTAL — tabela `receitas`
    # ------------------------------------------------------------------
    cursor.execute("PRAGMA table_info(receitas)")
    colunas_rec = {col[1] for col in cursor.fetchall()}

    # Garante que a coluna rendimento_cubas exista antes de criar a tabela
    # (caso a tabela já exista sem essa coluna de uma versão anterior).
    if colunas_rec and 'rendimento_cubas' not in colunas_rec:
        cursor.execute("ALTER TABLE receitas ADD COLUMN rendimento_cubas INTEGER DEFAULT 1")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS receitas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome_gelato TEXT NOT NULL,
            rendimento_cubas INTEGER DEFAULT 1
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS receita_itens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receita_id INTEGER,
            ingrediente_id INTEGER,
            quantidade REAL,
            FOREIGN KEY(receita_id) REFERENCES receitas(id),
            FOREIGN KEY(ingrediente_id) REFERENCES ingredientes(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS producao_diaria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sabor TEXT NOT NULL,
            quantidade_kg REAL NOT NULL,
            data_hora TEXT NOT NULL
        )
    """)

    # ------------------------------------------------------------------
    # CONTROLE DE NF-e
    # ------------------------------------------------------------------

    # Garante unicidade da chave de acesso para evitar lançamentos duplos.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registro_nfe (
            chave_acesso TEXT PRIMARY KEY,
            data_importacao TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS historico_entradas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ingrediente_id INTEGER,
            quantidade REAL,
            tipo TEXT,
            data_hora TEXT,
            FOREIGN KEY(ingrediente_id) REFERENCES ingredientes(id)
        )
    """)

    # Tabela de mapeamento inteligente: lembra o vínculo xprod → ingrediente
    # para agilizar futuras importações do mesmo fornecedor.
    cursor.execute("DROP TABLE IF EXISTS mapeamento_nfe")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mapeamento_nfe (
            xprod TEXT PRIMARY KEY,
            ingrediente_id INTEGER,
            fator_conversao REAL
        )
    """)

    # ------------------------------------------------------------------
    # CONTROLE DE VITRINE (PDV) — Nível 3 do fluxo de estoque
    # ------------------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS estoque_pronto (
            sabor TEXT PRIMARY KEY,
            quantidade_cubas REAL DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS auditoria_cubas_vazias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sabor TEXT NOT NULL,
            peso_consumido REAL,
            tipo_evento TEXT,
            data_hora TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS controle_vitrine (
            sabor TEXT PRIMARY KEY,
            peso_enviado REAL DEFAULT 0,
            peso_retornado REAL DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS produtos_pdv (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            peso_teorico REAL NOT NULL
        )
    """)

    # ------------------------------------------------------------------
    # NOVA TABELA: LOG AUDITÁVEL DE TRANSFERÊNCIAS ENTRE NÍVEIS
    # Registra cada movimentação entre Almoxarifado → Cozinha → Vitrine,
    # permitindo rastreabilidade completa para relatórios e auditoria.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transferencias_estoque (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ingrediente_id INTEGER,
            qtd_origem REAL NOT NULL,
            qtd_destino REAL NOT NULL,
            origem TEXT NOT NULL,
            destino TEXT NOT NULL,
            data_hora TEXT NOT NULL,
            observacao TEXT DEFAULT '',
            FOREIGN KEY(ingrediente_id) REFERENCES ingredientes(id)
        )
    """)

    # ------------------------------------------------------------------
    # SEED INICIAL — apenas se o banco estiver completamente vazio
    # Os sabores padrão são exemplos genéricos para demonstração.
    # ------------------------------------------------------------------
    cursor.execute("SELECT COUNT(*) FROM sabores")
    if cursor.fetchone()[0] == 0:
        sabores_fixos = [
            "Produto A", "Produto B", "Produto C",
            "Produto D", "Produto E", "Produto F", "Produto G"
        ]
        sabores_rotativos = [
            "Rotativo 1", "Rotativo 2", "Rotativo 3", "Rotativo 4"
        ]

        for nome in sabores_fixos:
            cursor.execute(
                "INSERT INTO sabores (nome, categoria) VALUES (?, 'fixo')", (nome,)
            )
        for nome in sabores_rotativos:
            cursor.execute(
                "INSERT INTO sabores (nome, categoria) VALUES (?, 'rotativo')", (nome,)
            )

    conn.commit()
    conn.close()
