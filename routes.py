"""
routes.py — Motor Gelato
=========================

Roteador principal da API REST do Motor Gelato.

Organização dos endpoints:
    - Autenticação: PIN de acesso por perfil.
    - Cardápio/Sabores: Gerenciamento do mix de produtos da cozinha.
    - Ingredientes/Insumos: CRUD completo e controle de estoque (3 níveis).
    - Transferências: Movimentação auditável Almoxarifado → Cozinha → Vitrine.
    - Receitas (Fichas Técnicas): Vínculo ingrediente/produto com custo.
    - Produção: Registro de lotes produzidos na cozinha.
    - Vitrine (PDV): Controle de cubas/unidades no ponto de venda.
    - MRP: Material Requirements Planning — sugestão inteligente de compras.
    - NF-e: Importação de Notas Fiscais Eletrônicas via XML.
    - Relatórios: Exportações e históricos.

Design decision — SQLite sem ORM:
    Optamos por sqlite3 puro em vez de SQLAlchemy para reduzir dependências
    e manter o código legível para desenvolvedores de qualquer nível.
    O custo é a verbosidade das queries, compensado pelos comentários inline.
"""

import os
from dotenv import load_dotenv
load_dotenv()
import io
import csv
import math
import asyncio
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

import sqlite3
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, FileResponse

from config_db import DB_FILE
from models import (
    LoginRequest, EdicaoSabores, Alerta, Producao,
    NovoIngrediente, AtualizacaoEstoque, AtualizacaoPreco,
    AtualizacaoLimite, AtualizacaoFator, AtualizacaoParametros,
    ListaInventario, EntradaManual, Transferencia, TransferenciaEstoque,
    NovaReceita, ListaMetas, ListaQuebras, EntradaNFE,
    RegistroVitrine, RegistroCubaVazia, RegistroRefill,
    NovoProdutoPDV, RequestEmailCompras,
)
from ws_manager import manager, alertas_ativos


# ==========================================
# CONFIGURAÇÕES DE ACESSO (AUTENTICAÇÃO)
# ==========================================
# Em produção, mova esses valores para variáveis de ambiente:
#   export PIN_GERENTE=xxxx
#   export PIN_FUNCIONARIO=xxxx
PIN_GERENTE = os.getenv("PIN_GERENTE", "8520")
PIN_FUNCIONARIO = os.getenv("PIN_FUNCIONARIO", "0258")


# ==========================================
# CONFIGURAÇÕES DE E-MAIL
# ==========================================
# Configure as variáveis de ambiente antes de usar o envio de e-mail:
#   export EMAIL_REMETENTE=seu@email.com
#   export EMAIL_SENHA=sua_senha_de_app
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE", "")
EMAIL_SENHA = os.getenv("EMAIL_SENHA", "")


router = APIRouter()


# ==========================================
# AUTENTICAÇÃO
# ==========================================

@router.post("/validar_pin", summary="Validar PIN de acesso")
async def validar_pin(dados: LoginRequest):
    """Valida o PIN de acesso e retorna o destino conforme o perfil do usuário.

    O sistema usa dois perfis de acesso:
    - **Gerente**: acesso completo ao módulo de gestão e estoque.
    - **Funcionário**: acesso restrito ao módulo de balcão/PDV.

    Args:
        dados: Objeto contendo o PIN digitado pelo usuário.

    Returns:
        dict: ``{"sucesso": True, "destino": "/rota"}`` em caso de sucesso,
              ou ``{"sucesso": False, "mensagem": "..."}`` em caso de falha.
    """
    if dados.pin == PIN_GERENTE:
        return {"sucesso": True, "destino": "/estoque"}
    elif dados.pin == PIN_FUNCIONARIO:
        return {"sucesso": True, "destino": "/balcao"}
    else:
        return {"sucesso": False, "mensagem": "PIN Incorreto"}


# ==========================================
# CARDÁPIO / SABORES
# ==========================================

@router.get("/sabores", summary="Listar sabores do cardápio")
async def ler_sabores():
    """Retorna o cardápio atual separado em produtos fixos e rotativos.

    Returns:
        dict: ``{"fixos": [...], "rotativos": [...]}`` com os nomes dos produtos.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT nome, categoria FROM sabores")
    rows = cursor.fetchall()
    conn.close()
    return {
        "fixos": [r[0] for r in rows if r[1] == 'fixo'],
        "rotativos": [r[0] for r in rows if r[1] == 'rotativo']
    }


@router.post("/atualizar_sabores", summary="Atualizar cardápio completo")
async def atualizar_sabores(dados: EdicaoSabores):
    """Substitui completamente o cardápio de sabores e notifica a cozinha via WebSocket.

    A operação é transacional: apaga todos os sabores e insere a nova lista.
    O broadcast WebSocket garante que a tela de Cozinha atualize o grid
    de botões instantaneamente, sem necessidade de recarregar a página.

    Args:
        dados: Listas completas de produtos fixos e rotativos.

    Returns:
        dict: ``{"status": "sucesso"}`` após a atualização.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sabores")
    for f in dados.fixos:
        cursor.execute("INSERT INTO sabores (nome, categoria) VALUES (?, 'fixo')", (f,))
    for r in dados.rotativos:
        cursor.execute("INSERT INTO sabores (nome, categoria) VALUES (?, 'rotativo')", (r,))
    conn.commit()
    conn.close()
    await manager.broadcast({"acao": "atualizar_cardapio"})
    return {"status": "sucesso"}


@router.post("/alerta_cuba", summary="Disparar alerta de reposição no PDV")
async def alerta_cuba(alerta: Alerta):
    """Registra um alerta de reposição e notifica a cozinha em tempo real.

    O alerta é armazenado em memória (``alertas_ativos``) para que conexões
    WebSocket que reconectarem após uma queda ainda recebam o estado atual.

    Args:
        alerta: Objeto com o nome do sabor que precisa de reposição.

    Returns:
        dict: ``{"status": "alerta enviado"}``
    """
    sabor_upper = alerta.sabor.upper().strip()
    alertas_ativos.add(sabor_upper)
    await manager.broadcast({"acao": "piscar", "sabor": alerta.sabor})
    return {"status": "alerta enviado"}


@router.get("/alertas_ativos", summary="Listar alertas de reposição ativos")
async def listar_alertas_ativos():
    """Retorna a lista de sabores com alerta de reposição ativo.

    Usado ao reconectar um WebSocket para restaurar o estado visual
    (botões piscando) sem depender do histórico de mensagens.

    Returns:
        list[str]: Lista de nomes de sabores com alerta ativo.
    """
    return list(alertas_ativos)


# ==========================================
# PRODUÇÃO (COZINHA — NÍVEL 2)
# ==========================================

@router.post("/producao", summary="Registrar lote produzido na cozinha")
async def registrar_producao(dados: Producao):
    """Registra um novo lote de produção e remove o alerta de reposição correspondente.

    Após o registro, o sistema faz broadcast WebSocket com ação ``"produzido"``
    para que o botão do sabor na tela da Cozinha pare de piscar.

    Args:
        dados: Sabor produzido e quantidade em kg do lote.

    Returns:
        dict: ``{"status": "sucesso"}``

    Raises:
        HTTPException: 500 em caso de erro no banco de dados.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        data_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sabor_limpo = dados.sabor.upper().strip()

        cursor.execute(
            "INSERT INTO producao_diaria (sabor, quantidade_kg, data_hora) VALUES (?, ?, ?)",
            (sabor_limpo, dados.quantidade, data_atual)
        )
        conn.commit()
        conn.close()

        # Remove o alerta ativo após confirmar a produção.
        if sabor_limpo in alertas_ativos:
            alertas_ativos.remove(sabor_limpo)

        await manager.broadcast({"acao": "produzido", "sabor": dados.sabor})
        return {"status": "sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/limpar_historico_producao", summary="Limpar histórico de produção")
async def limpar_historico():
    """Remove todos os registros de produção (operação de manutenção diária).

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM producao_diaria")
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.post("/baixar_estoque_retroativo/{id}", summary="Baixar estoque por lote de produção")
async def baixar_retroativo(id: int):
    """Desconta os ingredientes da cozinha com base em um lote de produção registrado.

    Usa a ficha técnica (receita) do produto para calcular os ingredientes
    consumidos proporcionalmente à quantidade produzida.

    Mecanismo anti-duplicidade: adiciona o sufixo ' ✓' ao nome do sabor
    no registro de produção após a baixa, evitando duplo desconto sem
    precisar alterar o schema do banco de dados.

    Args:
        id: ID do registro em ``producao_diaria`` a ser processado.

    Returns:
        dict: ``{"status": "sucesso"}``

    Raises:
        HTTPException: 404 se o registro não existir.
        HTTPException: 400 se não houver ficha técnica cadastrada para o produto.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT sabor, quantidade_kg FROM producao_diaria WHERE id = ?", (id,))
    prod = cursor.fetchone()

    if not prod:
        conn.close()
        raise HTTPException(status_code=404, detail="Produção não encontrada")

    sabor, qtd = prod
    # Remove o checkmark de produções já processadas antes de buscar a receita.
    sabor_limpo = sabor.replace(' ✓', '')

    cursor.execute(
        "SELECT id, rendimento_cubas FROM receitas WHERE nome_gelato = ?", (sabor_limpo,)
    )
    receita = cursor.fetchone()

    if not receita:
        conn.close()
        raise HTTPException(status_code=400, detail="Ficha técnica não encontrada")

    rec_id, rendimento = receita
    # Multiplicador: quantas "receitas base" foram feitas neste lote.
    multiplicador = qtd / rendimento

    cursor.execute(
        "SELECT ingrediente_id, quantidade FROM receita_itens WHERE receita_id = ?", (rec_id,)
    )
    for ing_id, qtd_ing in cursor.fetchall():
        cursor.execute(
            "UPDATE ingredientes SET estoque_atual = estoque_atual - ? WHERE id = ?",
            (qtd_ing * multiplicador, ing_id)
        )

    # Marca o registro como processado para evitar baixa dupla.
    cursor.execute("UPDATE producao_diaria SET sabor = sabor || ' ✓' WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.delete("/cancelar_producao/{id}", summary="Estornar lote de produção")
async def cancelar_producao(id: int):
    """Reverte um lote de produção devolvendo os ingredientes ao estoque da cozinha.

    Args:
        id: ID do registro em ``producao_diaria`` a ser estornado.

    Returns:
        dict: ``{"status": "sucesso", "mensagem": "..."}``

    Raises:
        HTTPException: 404 se o registro não existir.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT sabor, quantidade_kg FROM producao_diaria WHERE id = ?", (id,))
    producao = cursor.fetchone()

    if not producao:
        conn.close()
        raise HTTPException(status_code=404, detail="Registro não encontrado")

    sabor, qtd_kg = producao
    # Remove o checkmark ' ✓' ANTES de buscar a receita.
    # O endpoint /baixar_estoque_retroativo/{id} marca produções processadas
    # com ' ✓'. Sem essa limpeza, o estorno não encontraria a receita e
    # deletaria o registro sem devolver os ingredientes ao estoque.
    sabor_limpo = sabor.replace(' ✓', '').upper().strip()
    cursor.execute(
        "SELECT id, rendimento_cubas FROM receitas WHERE nome_gelato = ?",
        (sabor_limpo,)
    )
    receita = cursor.fetchone()

    if receita:
        rec_id, rendimento_base = receita
        multiplicador = qtd_kg / rendimento_base
        cursor.execute(
            "SELECT ingrediente_id, quantidade FROM receita_itens WHERE receita_id = ?", (rec_id,)
        )
        for ing_id, qtd_receita in cursor.fetchall():
            qtd_para_devolver = qtd_receita * multiplicador
            cursor.execute(
                "UPDATE ingredientes SET estoque_atual = estoque_atual + ? WHERE id = ?",
                (qtd_para_devolver, ing_id)
            )

    cursor.execute("DELETE FROM producao_diaria WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return {"status": "sucesso", "mensagem": "Produção estornada e ingredientes devolvidos!"}


@router.get("/relatorio_producao", summary="Relatório de produção do período")
async def relatorio():
    """Retorna o histórico e resumo de produção por sabor.

    O campo ``tem_receita`` indica se existe ficha técnica cadastrada para
    permitir a baixa automática de ingredientes.

    Returns:
        dict: ``{"historico": [...], "resumo": [...]}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.id, p.sabor, p.quantidade_kg, p.data_hora,
               (SELECT COUNT(1) FROM receitas r
                WHERE r.nome_gelato = REPLACE(p.sabor, ' ✓', '')) as tem_receita
        FROM producao_diaria p
        ORDER BY p.id DESC LIMIT 100
    """)
    hist = [
        {"id": r[0], "sabor": r[1], "quantidade": r[2], "data_hora": r[3], "tem_receita": bool(r[4])}
        for r in cursor.fetchall()
    ]

    # Agrupa por sabor removendo o checkmark para evitar duplicatas no gráfico.
    cursor.execute("""
        SELECT REPLACE(sabor, ' ✓', ''), SUM(quantidade_kg)
        FROM producao_diaria
        GROUP BY REPLACE(sabor, ' ✓', '')
        ORDER BY SUM(quantidade_kg) DESC
    """)
    res = [{"sabor": r[0], "total": round(r[1], 2)} for r in cursor.fetchall()]
    conn.close()
    return {"historico": hist, "resumo": res}


# ==========================================
# INGREDIENTES / INSUMOS — CRUD
# ==========================================

@router.get("/ingredientes", summary="Listar todos os ingredientes")
async def listar_ingredientes():
    """Retorna todos os ingredientes com seus dados de estoque nos 3 níveis.

    Returns:
        list[dict]: Lista de ingredientes com campos de almoxarifado, cozinha,
                    parâmetros de MRP e flags de controle.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, nome, unidade, preco_unitario, estoque_atual,
               limite_alerta, estoque_almoxarifado, unidade_almoxarifado,
               fator_conversao, escondido, validade,
               lead_time, burn_rate_diario, volatil
        FROM ingredientes ORDER BY nome
    """)
    res = []
    for r in cursor.fetchall():
        res.append({
            "id": r[0], "nome": r[1], "unidade": r[2], "preco": r[3],
            "estoque": r[4], "limite": r[5] if r[5] is not None else 0.0,
            "estoque_almoxarifado": r[6] if r[6] is not None else 0.0,
            "unidade_almoxarifado": r[7], "fator_conversao": r[8],
            "escondido": bool(r[9]), "validade": r[10] if r[10] is not None else "",
            "lead_time": r[11] if r[11] is not None else 7,
            "burn_rate": r[12] if r[12] is not None else 0.5,
            "volatil": bool(r[13])
        })
    conn.close()
    return res


@router.post("/ingredientes", summary="Cadastrar novo ingrediente")
async def criar_ingrediente(ing: NovoIngrediente):
    """Cadastra um novo ingrediente no sistema.

    Args:
        ing: Dados completos do novo ingrediente.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO ingredientes
        (nome, unidade, preco_unitario, estoque_atual, limite_alerta,
        estoque_almoxarifado, unidade_almoxarifado, fator_conversao,
        lead_time, burn_rate_diario, volatil)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ing.nome.upper(), ing.unidade, ing.preco_unitario, ing.estoque_atual,
        ing.limite_alerta, ing.estoque_almoxarifado, ing.unidade_almoxarifado,
        ing.fator_conversao, ing.lead_time, ing.burn_rate_diario, int(ing.volatil)
    ))
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.delete("/ingredientes/{id}", summary="Remover ingrediente")
async def deletar_ingrediente(id: int):
    """Remove um ingrediente e seus vínculos em receitas.

    Args:
        id: ID do ingrediente a ser removido.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ingredientes WHERE id = ?", (id,))
    cursor.execute("DELETE FROM receita_itens WHERE ingrediente_id = ?", (id,))
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.put("/ingredientes/{item_id}/estoque", summary="Atualizar estoque na cozinha")
async def atualizar_estoque(item_id: int, dados: AtualizacaoEstoque):
    """Atualiza diretamente o estoque da Cozinha (Nível 2).

    Args:
        item_id: ID do ingrediente.
        dados: Novo valor absoluto de estoque.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE ingredientes SET estoque_atual = ? WHERE id = ?",
        (dados.novo_estoque, item_id)
    )
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.put("/ingredientes/{item_id}/almoxarifado", summary="Atualizar estoque no almoxarifado")
async def atualizar_almoxarifado(item_id: int, dados: AtualizacaoEstoque):
    """Atualiza diretamente o estoque do Almoxarifado (Nível 1).

    Args:
        item_id: ID do ingrediente.
        dados: Novo valor absoluto de estoque no almoxarifado.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE ingredientes SET estoque_almoxarifado = ? WHERE id = ?",
        (dados.novo_estoque, item_id)
    )
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.put("/ingredientes/{item_id}/preco", summary="Atualizar preço unitário")
async def atualizar_preco(item_id: int, dados: AtualizacaoPreco):
    """Atualiza o preço unitário de compra de um ingrediente.

    Args:
        item_id: ID do ingrediente.
        dados: Novo preço unitário em reais.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE ingredientes SET preco_unitario = ? WHERE id = ?",
        (dados.novo_preco, item_id)
    )
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.put("/ingredientes/{item_id}/limite", summary="Atualizar gatilho de alerta")
async def atualizar_limite(item_id: int, dados: AtualizacaoLimite):
    """Atualiza o limite mínimo de estoque que dispara alertas de compra.

    Args:
        item_id: ID do ingrediente.
        dados: Novo valor de limite de alerta.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE ingredientes SET limite_alerta = ? WHERE id = ?",
        (dados.novo_limite, item_id)
    )
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.put("/ingredientes/{item_id}/fator", summary="Atualizar fator de conversão")
async def atualizar_fator(item_id: int, dados: AtualizacaoFator):
    """Atualiza o fator de conversão entre unidade de compra e unidade de uso.

    Exemplo: 1 Caixa de leite (12 litros) → fator_conversao = 12.

    Args:
        item_id: ID do ingrediente.
        dados: Novo fator de conversão.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE ingredientes SET fator_conversao = ? WHERE id = ?",
        (dados.novo_fator, item_id)
    )
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.put("/ingredientes/{item_id}/parametros", summary="Atualizar parâmetros MRP")
async def atualizar_parametros_inteligentes(item_id: int, dados: AtualizacaoParametros):
    """Atualiza os parâmetros que alimentam o modelo de MRP do ingrediente.

    Args:
        item_id: ID do ingrediente.
        dados: Novos valores de limite, lead time, burn rate e flag de volatilidade.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE ingredientes
        SET limite_alerta = ?, lead_time = ?, burn_rate_diario = ?, volatil = ?
        WHERE id = ?
    """, (dados.novo_limite, dados.novo_lead_time, dados.novo_burn_rate,
          int(dados.novo_volatil), item_id))
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.put("/ingredientes/{item_id}/toggle_ocultar", summary="Alternar visibilidade do ingrediente")
async def toggle_ocultar(item_id: int):
    """Alterna o estado de visibilidade de um ingrediente na interface.

    Ingredientes ocultados não aparecem no inventário por padrão, útil para
    itens sazonais ou descontinuados que não devem ser deletados.

    Args:
        item_id: ID do ingrediente.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE ingredientes
        SET escondido = CASE WHEN escondido = 1 THEN 0 ELSE 1 END
        WHERE id = ?
    """, (item_id,))
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.post("/salvar_inventario", summary="Salvar resultado de inventário físico")
async def salvar_inventario(dados: ListaInventario):
    """Processa o resultado de uma contagem física de inventário.

    Para cada item contado:
    1. Compara o estoque contado com o saldo anterior no sistema.
    2. Se houve redução, registra o delta como consumo em ``registro_consumo``
       (alimenta o cálculo automático de burn rate).
    3. Atualiza o saldo e a validade do lote.

    Args:
        dados: Lista com os resultados da contagem física.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    data_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for item in dados.itens:
        cursor.execute(
            "SELECT estoque_almoxarifado FROM ingredientes WHERE id = ?", (item.id,)
        )
        row = cursor.fetchone()
        estoque_antigo = row[0] if row else 0.0

        # Se o estoque diminuiu na contagem física, registra como consumo real.
        # Esse consumo alimenta o cálculo do burn_rate_diario no endpoint /recalcular_burn_rates.
        if item.estoque_real < estoque_antigo:
            consumo = estoque_antigo - item.estoque_real
            cursor.execute(
                "INSERT INTO registro_consumo (ingrediente_id, quantidade, data_hora) VALUES (?, ?, ?)",
                (item.id, consumo, data_atual)
            )

        cursor.execute("""
            UPDATE ingredientes
            SET estoque_almoxarifado = ?, validade = ?
            WHERE id = ?
        """, (item.estoque_real, item.validade, item.id))

    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.post("/ingredientes/{item_id}/entrada_manual", summary="Lançar entrada avulsa no almoxarifado")
async def entrada_manual(item_id: int, dados: EntradaManual):
    """Registra uma entrada de estoque no almoxarifado sem NF-e associada.

    Útil para compras em dinheiro, doações ou ajustes pontuais que não
    geram documento fiscal eletrônico.

    Args:
        item_id: ID do ingrediente que recebeu a entrada.
        dados: Quantidade e motivo da entrada.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    data_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "UPDATE ingredientes SET estoque_almoxarifado = estoque_almoxarifado + ? WHERE id = ?",
        (dados.quantidade, item_id)
    )
    cursor.execute(
        "INSERT INTO historico_entradas (ingrediente_id, quantidade, tipo, data_hora) VALUES (?, ?, ?, ?)",
        (item_id, dados.quantidade, f"Avulsa: {dados.motivo}", data_atual)
    )
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


# ==========================================
# TRANSFERÊNCIAS ENTRE NÍVEIS DE ESTOQUE
# ==========================================

@router.post(
    "/transferir_estoque",
    deprecated=True,
    summary="[DEPRECADO] Usar /transferencia/almoxarifado-para-cozinha"
)
async def transferir_estoque(dados: Transferencia):
    """Endpoint legado sem validação de saldo. Mantido para compatibilidade.

    .. deprecated::
        Use ``/transferencia/almoxarifado-para-cozinha`` que inclui validação
        de saldo, conversão de unidades e log auditável de rastreabilidade.

    Args:
        dados: ID do ingrediente e quantidade a transferir.

    Returns:
        dict: Mesmo retorno do endpoint atual.
    """
    payload = TransferenciaEstoque(
        item_id=dados.item_id,
        qtd_transferida=dados.qtd_transferida,
        origem="almoxarifado",
        destino="cozinha",
        observacao="[migrado de rota legada]"
    )
    return await transferencia_almoxarifado_cozinha(payload)


@router.post("/transferencia/almoxarifado-para-cozinha",
             summary="Transferência auditável: Almoxarifado → Cozinha")
async def transferencia_almoxarifado_cozinha(dados: TransferenciaEstoque):
    """Transfere insumos do Almoxarifado para a Cozinha com validação e log de auditoria.

    Fluxo:
    1. Valida se há saldo suficiente no almoxarifado.
    2. Aplica o fator de conversão (unidade de compra → unidade de uso).
    3. Debita do almoxarifado e credita na cozinha atomicamente.
    4. Registra a operação em ``transferencias_estoque`` para rastreabilidade.

    Args:
        dados: Payload com ID do ingrediente, quantidade e metadados da transferência.

    Returns:
        dict: ``{"status": "sucesso", "qtd_cozinha": float, "qtd_almoxarifado": float}``

    Raises:
        HTTPException: 404 se o ingrediente não existir.
        HTTPException: 400 se o saldo no almoxarifado for insuficiente.
    """
    if dados.origem != "almoxarifado" or dados.destino != "cozinha":
        raise HTTPException(
            status_code=400,
            detail="Use origem='almoxarifado' e destino='cozinha' neste endpoint."
        )

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT estoque_almoxarifado, fator_conversao, estoque_atual FROM ingredientes WHERE id = ?",
        (dados.item_id,)
    )
    row = cursor.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Ingrediente não encontrado.")

    est_almox, fator, est_cozinha = row

    if dados.qtd_transferida > est_almox:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail=f"Saldo insuficiente no almoxarifado. Disponível: {est_almox:.2f}"
        )

    # Converte unidades: ex. 2 caixas × 12 kg/caixa = 24 kg na cozinha.
    qtd_convertida = dados.qtd_transferida * fator
    novo_almox = est_almox - dados.qtd_transferida
    novo_cozinha = est_cozinha + qtd_convertida

    cursor.execute(
        "UPDATE ingredientes SET estoque_almoxarifado = ?, estoque_atual = ? WHERE id = ?",
        (novo_almox, novo_cozinha, dados.item_id)
    )

    # Registro auditável — preserva rastreabilidade de toda a cadeia logística.
    data_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO transferencias_estoque
        (ingrediente_id, qtd_origem, qtd_destino, origem, destino, data_hora, observacao)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (dados.item_id, dados.qtd_transferida, qtd_convertida,
          "almoxarifado", "cozinha", data_atual, dados.observacao))

    conn.commit()
    conn.close()
    return {
        "status": "sucesso",
        "qtd_cozinha": round(qtd_convertida, 3),
        "qtd_almoxarifado": round(novo_almox, 3)
    }


@router.post("/transferencia/cozinha-para-vitrine",
             summary="Transferência auditável: Cozinha → Vitrine")
async def transferencia_cozinha_vitrine(dados: TransferenciaEstoque):
    """Move produto acabado da Cozinha (freezer) para a Vitrine/PDV (Nível 3).

    Fluxo:
    1. Valida se há saldo suficiente na cozinha (estoque_atual).
    2. Debita da cozinha e credita em ``estoque_pronto`` (vitrine).
    3. Registra a operação em ``transferencias_estoque``.

    Nota: Para este nível, a transferência é 1:1 (sem conversão de unidade),
    pois o produto já está no formato final de venda.

    Args:
        dados: Payload com ID do ingrediente, quantidade (em cubas/unidades) e metadados.

    Returns:
        dict: ``{"status": "sucesso", "qtd_vitrine": float, "qtd_cozinha": float}``

    Raises:
        HTTPException: 404 se o ingrediente não existir.
        HTTPException: 400 se o saldo na cozinha for insuficiente.
    """
    if dados.origem != "cozinha" or dados.destino != "vitrine":
        raise HTTPException(
            status_code=400,
            detail="Use origem='cozinha' e destino='vitrine' neste endpoint."
        )

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT nome, estoque_atual FROM ingredientes WHERE id = ?", (dados.item_id,)
    )
    row = cursor.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Ingrediente não encontrado.")

    nome, est_cozinha = row

    if dados.qtd_transferida > est_cozinha:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail=f"Saldo insuficiente na cozinha. Disponível: {est_cozinha:.2f}"
        )

    novo_cozinha = est_cozinha - dados.qtd_transferida

    cursor.execute(
        "UPDATE ingredientes SET estoque_atual = ? WHERE id = ?",
        (novo_cozinha, dados.item_id)
    )

    # Credita na tabela de vitrine. Se o sabor não existir, cria com UPSERT.
    sabor_upper = nome.upper().strip()
    cursor.execute("SELECT quantidade_cubas FROM estoque_pronto WHERE sabor = ?", (sabor_upper,))
    vitrine_row = cursor.fetchone()
    if vitrine_row:
        cursor.execute(
            "UPDATE estoque_pronto SET quantidade_cubas = quantidade_cubas + ? WHERE sabor = ?",
            (dados.qtd_transferida, sabor_upper)
        )
    else:
        cursor.execute(
            "INSERT INTO estoque_pronto (sabor, quantidade_cubas) VALUES (?, ?)",
            (sabor_upper, dados.qtd_transferida)
        )

    nova_qtd_vitrine = (vitrine_row[0] if vitrine_row else 0) + dados.qtd_transferida

    data_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO transferencias_estoque
        (ingrediente_id, qtd_origem, qtd_destino, origem, destino, data_hora, observacao)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (dados.item_id, dados.qtd_transferida, dados.qtd_transferida,
          "cozinha", "vitrine", data_atual, dados.observacao))

    conn.commit()
    conn.close()
    return {
        "status": "sucesso",
        "qtd_vitrine": round(nova_qtd_vitrine, 3),
        "qtd_cozinha": round(novo_cozinha, 3)
    }


@router.get("/transferencias", summary="Histórico de transferências entre níveis")
async def listar_transferencias(limit: int = 50):
    """Retorna o histórico auditável de transferências entre os 3 níveis de estoque.

    Args:
        limit: Número máximo de registros a retornar (padrão: 50).

    Returns:
        list[dict]: Histórico de transferências com nome do ingrediente, quantidades,
                    origem, destino, data e observação.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t.id, i.nome, t.qtd_origem, t.qtd_destino,
               t.origem, t.destino, t.data_hora, t.observacao
        FROM transferencias_estoque t
        LEFT JOIN ingredientes i ON t.ingrediente_id = i.id
        ORDER BY t.id DESC
        LIMIT ?
    """, (limit,))
    res = [{
        "id": r[0], "ingrediente": r[1],
        "qtd_origem": r[2], "qtd_destino": r[3],
        "origem": r[4], "destino": r[5],
        "data_hora": r[6], "observacao": r[7]
    } for r in cursor.fetchall()]
    conn.close()
    return res


# ==========================================
# RECEITAS (FICHAS TÉCNICAS DE PRODUÇÃO)
# ==========================================

@router.get("/receitas", summary="Listar fichas técnicas com custo")
async def listar_receitas():
    """Retorna todas as receitas com custo calculado por produto.

    O custo é calculado com ``preco_unitario / fator_conversao`` para normalizar
    o preço da unidade de compra para a unidade de uso na receita.

    Returns:
        list[dict]: Receitas com id, nome, rendimento, custo e flag de preço zerado.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.id, r.nome_gelato, r.rendimento_cubas,
        SUM(ri.quantidade * (i.preco_unitario / NULLIF(i.fator_conversao, 0))),
        SUM(CASE WHEN i.preco_unitario = 0 OR i.preco_unitario = 1.0
                 OR i.preco_unitario IS NULL THEN 1 ELSE 0 END)
        FROM receitas r
        LEFT JOIN receita_itens ri ON r.id = ri.receita_id
        LEFT JOIN ingredientes i ON ri.ingrediente_id = i.id
        GROUP BY r.id
        ORDER BY r.nome_gelato
    """)
    res = [
        {
            "id": r[0], "nome": r[1], "rendimento": r[2],
            "custo": round(r[3] or 0, 2),
            "tem_preco_zerado": (r[4] is None or r[4] > 0)
        }
        for r in cursor.fetchall()
    ]
    conn.close()
    return res


@router.get("/receitas/{receita_id}", summary="Obter detalhes de uma receita")
async def obter_receita(receita_id: int):
    """Retorna os ingredientes e quantidades de uma receita específica.

    Args:
        receita_id: ID da receita a consultar.

    Returns:
        dict: Dados da receita com lista de ingredientes e quantidades.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, nome_gelato, rendimento_cubas FROM receitas WHERE id = ?", (receita_id,)
    )
    rec = cursor.fetchone()
    cursor.execute("""
        SELECT ri.ingrediente_id, i.nome, i.unidade, ri.quantidade, i.preco_unitario
        FROM receita_itens ri
        JOIN ingredientes i ON ri.ingrediente_id = i.id
        WHERE ri.receita_id = ?
    """, (receita_id,))
    itens = [
        {"ingrediente_id": r[0], "nome": r[1], "unidade": r[2], "quantidade": r[3], "preco": r[4]}
        for r in cursor.fetchall()
    ]
    conn.close()
    return {"id": rec[0], "nome_gelato": rec[1], "rendimento_cubas": rec[2], "itens": itens}


@router.post("/receitas", summary="Criar nova ficha técnica")
async def criar_receita(receita: NovaReceita):
    """Cria uma nova ficha técnica de produção.

    Args:
        receita: Dados da receita com ingredientes e quantidades.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO receitas (nome_gelato, rendimento_cubas) VALUES (?, ?)",
        (receita.nome_gelato.upper().strip(), receita.rendimento_cubas)
    )
    rid = cursor.lastrowid
    for it in receita.itens:
        cursor.execute(
            "INSERT INTO receita_itens (receita_id, ingrediente_id, quantidade) VALUES (?, ?, ?)",
            (rid, it.ingrediente_id, it.quantidade)
        )
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.put("/receitas/{receita_id}", summary="Atualizar ficha técnica")
async def atualizar_receita(receita_id: int, receita: NovaReceita):
    """Atualiza uma ficha técnica existente (substitui todos os ingredientes).

    Args:
        receita_id: ID da receita a atualizar.
        receita: Novos dados da receita.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE receitas SET nome_gelato = ?, rendimento_cubas = ? WHERE id = ?",
        (receita.nome_gelato.upper().strip(), receita.rendimento_cubas, receita_id)
    )
    cursor.execute("DELETE FROM receita_itens WHERE receita_id = ?", (receita_id,))
    for it in receita.itens:
        cursor.execute(
            "INSERT INTO receita_itens (receita_id, ingrediente_id, quantidade) VALUES (?, ?, ?)",
            (receita_id, it.ingrediente_id, it.quantidade)
        )
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.delete("/receitas/{id}", summary="Excluir ficha técnica")
async def deletar_receita(id: int):
    """Remove uma ficha técnica e seus vínculos de ingredientes.

    Args:
        id: ID da receita a remover.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM receitas WHERE id = ?", (id,))
    cursor.execute("DELETE FROM receita_itens WHERE receita_id = ?", (id,))
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


# ==========================================
# VITRINE / PDV (NÍVEL 3)
# ==========================================

@router.post("/enviar_vitrine", summary="Registrar envio para a vitrine")
async def enviar_vitrine(dados: RegistroVitrine):
    """Registra o peso de um produto enviado da cozinha para a vitrine refrigerada.

    Inicia um novo ciclo de rastreamento para aquele sabor, zerando
    o peso retornado do ciclo anterior.

    Args:
        dados: Sabor e peso em kg enviado para a vitrine.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    sabor = dados.sabor.upper().strip()
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO controle_vitrine (sabor, peso_enviado, peso_retornado) VALUES (?, ?, 0)",
        (sabor, dados.peso)
    )
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.post("/retornar_vitrine", summary="Registrar retorno da vitrine ao fim do dia")
async def retornar_vitrine(dados: RegistroVitrine):
    """Registra o peso que retornou da vitrine e calcula o consumo real do dia.

    O consumo (peso enviado − peso retornado) é lançado na auditoria para
    rastreamento acurado do CMV (Custo das Mercadorias Vendidas).

    Args:
        dados: Sabor e peso em kg que retornou da vitrine.

    Returns:
        dict: ``{"status": "sucesso", "consumo_registrado": float}``
    """
    sabor = dados.sabor.upper().strip()
    data_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("SELECT peso_enviado FROM controle_vitrine WHERE sabor = ?", (sabor,))
    row = cursor.fetchone()
    peso_enviado = row[0] if row else 0.0

    consumo = max(peso_enviado - dados.peso, 0)

    if consumo > 0:
        cursor.execute("""
            INSERT INTO auditoria_cubas_vazias (sabor, peso_consumido, tipo_evento, data_hora)
            VALUES (?, ?, 'RETORNO_FIM_DIA', ?)
        """, (sabor, consumo, data_atual))

    cursor.execute(
        "INSERT OR REPLACE INTO controle_vitrine (sabor, peso_enviado, peso_retornado) VALUES (?, 0, ?)",
        (sabor, dados.peso)
    )
    conn.commit()
    conn.close()
    return {"status": "sucesso", "consumo_registrado": consumo}


@router.post("/cuba_esvaziada", summary="Registrar esvaziamento total de uma cuba no PDV")
async def registrar_cuba_esvaziada(dados: RegistroCubaVazia):
    """Registra o esvaziamento completo de uma cuba/unidade no ponto de venda.

    Desconta 1 unidade do ``estoque_pronto`` (freezer da cozinha) e registra
    o evento na auditoria com o peso real que havia sido enviado.

    Args:
        dados: Nome do sabor cuja cuba foi esvaziada.

    Returns:
        dict: ``{"status": "sucesso"}``

    Raises:
        HTTPException: 500 em caso de erro de banco de dados.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        data_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sabor_upper = dados.sabor.upper().strip()

        cursor.execute(
            "SELECT peso_enviado FROM controle_vitrine WHERE sabor = ?", (sabor_upper,)
        )
        row = cursor.fetchone()
        peso_real_saida = row[0] if row and row[0] > 0 else 8.0

        cursor.execute(
            "INSERT INTO auditoria_cubas_vazias (sabor, peso_consumido, tipo_evento, data_hora) "
            "VALUES (?, ?, 'ESVAZIOU_BALCAO', ?)",
            (sabor_upper, peso_real_saida, data_atual)
        )
        cursor.execute(
            "UPDATE controle_vitrine SET peso_enviado = 0, peso_retornado = 0 WHERE sabor = ?",
            (sabor_upper,)
        )

        cursor.execute("SELECT 1 FROM estoque_pronto WHERE sabor = ?", (sabor_upper,))
        if cursor.fetchone():
            cursor.execute(
                "UPDATE estoque_pronto SET quantidade_cubas = quantidade_cubas - 1 WHERE sabor = ?",
                (sabor_upper,)
            )
        else:
            cursor.execute(
                "INSERT INTO estoque_pronto (sabor, quantidade_cubas) VALUES (?, -1)",
                (sabor_upper,)
            )

        conn.commit()
        conn.close()
        return {"status": "sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cuba_refill", summary="Registrar troca de cuba na vitrine (refill)")
async def registrar_refill(dados: RegistroRefill):
    """Registra a troca de uma cuba parcialmente consumida por uma nova.

    Calcula o consumo da cuba anterior (peso enviado − peso restante),
    lança na auditoria e inicia novo ciclo com o peso da nova cuba.
    Desconta também 1 unidade do freezer (estoque_pronto).

    Args:
        dados: Sabor, peso restante na cuba antiga e peso da nova cuba.

    Returns:
        dict: ``{"status": "sucesso", "consumo_registrado": float}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    data_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sabor_upper = dados.sabor.upper().strip()

    cursor.execute("SELECT peso_enviado FROM controle_vitrine WHERE sabor = ?", (sabor_upper,))
    row = cursor.fetchone()
    peso_real_saida = row[0] if row and row[0] > 0 else 8.0

    peso_que_saiu = max(peso_real_saida - dados.peso_restante, 0)

    cursor.execute("""
        INSERT INTO auditoria_cubas_vazias (sabor, peso_consumido, tipo_evento, data_hora)
        VALUES (?, ?, 'TROCA/REFILL', ?)
    """, (sabor_upper, peso_que_saiu, data_atual))

    cursor.execute(
        "INSERT OR REPLACE INTO controle_vitrine (sabor, peso_enviado, peso_retornado) VALUES (?, ?, 0)",
        (sabor_upper, dados.peso_novo)
    )

    cursor.execute("SELECT 1 FROM estoque_pronto WHERE sabor = ?", (sabor_upper,))
    if cursor.fetchone():
        cursor.execute(
            "UPDATE estoque_pronto SET quantidade_cubas = quantidade_cubas - 1 WHERE sabor = ?",
            (sabor_upper,)
        )
    else:
        cursor.execute(
            "INSERT INTO estoque_pronto (sabor, quantidade_cubas) VALUES (?, -1)", (sabor_upper,)
        )

    conn.commit()
    conn.close()
    return {"status": "sucesso", "consumo_registrado": peso_que_saiu}


@router.get("/status_vitrine/{sabor}", summary="Consultar status atual de um sabor na vitrine")
async def status_vitrine(sabor: str):
    """Retorna o peso enviado e retornado para um sabor específico na vitrine.

    Args:
        sabor: Nome do produto a consultar.

    Returns:
        dict: ``{"peso_enviado": float, "peso_retornado": float}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT peso_enviado, peso_retornado FROM controle_vitrine WHERE sabor = ?",
        (sabor.upper().strip(),)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"peso_enviado": row[0], "peso_retornado": row[1]}
    return {"peso_enviado": 0.0, "peso_retornado": 0.0}


@router.get("/resumo_auditoria", summary="Resumo diário de saída e produção")
async def resumo_auditoria(data: str = None):
    """Retorna o total de peso consumido na vitrine e produzido na cozinha para uma data.

    Args:
        data: Data no formato YYYY-MM-DD. Se omitida, usa a data de hoje.

    Returns:
        dict: ``{"data": str, "peso_saida_real": float, "total_produzido": float}``
    """
    if not data:
        data = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT SUM(peso_consumido) FROM auditoria_cubas_vazias WHERE data_hora LIKE ?",
        (f"{data}%",)
    )
    peso_saida_real = cursor.fetchone()[0] or 0.0

    cursor.execute(
        "SELECT SUM(quantidade_kg) FROM producao_diaria WHERE data_hora LIKE ?",
        (f"{data}%",)
    )
    total_produzido = cursor.fetchone()[0] or 0.0
    conn.close()
    return {"data": data, "peso_saida_real": peso_saida_real, "total_produzido": total_produzido}


# ==========================================
# PRODUTOS DO PDV
# ==========================================

@router.get("/produtos_pdv", summary="Listar produtos do PDV")
async def listar_produtos_pdv():
    """Lista todos os produtos cadastrados no ponto de venda com seus pesos teóricos.

    Returns:
        list[dict]: Produtos com id, nome e peso de referência.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, nome, peso_teorico FROM produtos_pdv ORDER BY nome")
    res = [{"id": r[0], "nome": r[1], "peso": r[2]} for r in cursor.fetchall()]
    conn.close()
    return res


@router.post("/produtos_pdv", summary="Cadastrar produto no PDV")
async def criar_produto_pdv(prod: NovoProdutoPDV):
    """Cadastra um novo produto no ponto de venda.

    Args:
        prod: Nome e peso teórico do produto.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO produtos_pdv (nome, peso_teorico) VALUES (?, ?)",
        (prod.nome.upper(), prod.peso_teorico)
    )
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.delete("/produtos_pdv/{id}", summary="Remover produto do PDV")
async def deletar_produto_pdv(id: int):
    """Remove um produto do ponto de venda.

    Args:
        id: ID do produto a remover.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM produtos_pdv WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


# ==========================================
# MRP — MATERIAL REQUIREMENTS PLANNING
# ==========================================

@router.get("/api/mrp", summary="Gerar lista inteligente de compras (MRP)")
async def gerar_mrp_visual():
    """Calcula e retorna os ingredientes que atingiram o ponto de pedido.

    Modelo matemático implementado:
        - **Burn Rate Diário**: Taxa média de consumo real calculada em
          ``/api/recalcular_burn_rates`` com base no histórico de inventários.
        - **Estoque de Segurança**: ``burn_rate × lead_time`` — quantidade mínima
          para sobreviver ao período de espera do fornecedor sem ruptura.
        - **Ponto de Pedido**: ``estoque_segurança + limite_alerta`` — gatilho
          dinâmico que considera tanto o lead time quanto a margem manual configurada.
        - **Quantidade a Comprar**: ``(consumo_mensal + estoque_segurança) - estoque_atual``,
          arredondado para cima (``math.ceil``) para garantir o abastecimento completo.

    Returns:
        list[dict]: Itens abaixo do ponto de pedido com sugestão de quantidade de compra.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, nome, estoque_almoxarifado, unidade_almoxarifado, limite_alerta,
               lead_time, burn_rate_diario, volatil
        FROM ingredientes
        WHERE escondido = 0 OR escondido IS NULL
        ORDER BY nome
    """)
    ingredientes = cursor.fetchall()
    conn.close()

    lista_compras = []
    for ing in ingredientes:
        estoque = float(ing[2]) if ing[2] is not None else 0.0
        lead_time = int(ing[5]) if ing[5] is not None else 7
        burn_rate = float(ing[6]) if ing[6] is not None else 0.5
        volatil = bool(ing[7])

        # Consumo projetado para 30 dias (horizonte padrão de reabastecimento).
        consumo_mensal = burn_rate * 30

        # Estoque de Segurança: quantidade que permite operar durante o lead time.
        estoque_seguranca = burn_rate * lead_time

        # Ponto de Pedido: combina o estoque de segurança com o gatilho manual.
        # O gatilho manual (limite_alerta) atua como margem de erro para
        # itens com demanda irregular não capturada pelo burn rate histórico.
        ponto_pedido = estoque_seguranca + (float(ing[4]) if ing[4] else 0.0)

        if estoque <= ponto_pedido or estoque < consumo_mensal:
            falta = (consumo_mensal + estoque_seguranca) - estoque
            sugestao = math.ceil(falta) if falta > 0 else 1

            lista_compras.append({
                "id": ing[0],
                "nome": ing[1],
                "estoque": estoque,
                "unidade": ing[3],
                "lead_time": lead_time,
                "burn_rate": burn_rate,
                "volatil": volatil,
                "ponto_pedido": round(ponto_pedido, 2),
                "comprar": sugestao,
                "limite": float(ing[4]) if ing[4] is not None else 0.0
            })

    return lista_compras


@router.post("/api/recalcular_burn_rates", summary="Recalcular taxas de consumo (burn rates)")
async def recalcular_burn_rates():
    """Recalcula o burn rate diário de todos os ingredientes com base no histórico de consumo.

    Metodologia — Média Histórica de Vida Útil:
        Em vez de usar apenas os últimos N dias (que zeraria o burn rate
        em períodos sem consumo), calculamos a taxa média desde o PRIMEIRO
        consumo registrado até hoje.

        ``burn_rate = total_consumido / dias_desde_primeiro_uso``

        Vantagens:
        - Suaviza naturalmente variações sazonais sem precisar de janelas deslizantes.
        - Nunca zera abruptamente: em períodos sem uso, a taxa dilui gradualmente.
        - Simples de auditar: o número reflete a realidade histórica completa.

    Returns:
        dict: ``{"status": "sucesso", "atualizados": int}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM ingredientes")
    ingredientes = cursor.fetchall()
    itens_atualizados = 0
    agora = datetime.now()

    for (ing_id,) in ingredientes:
        cursor.execute("""
            SELECT SUM(quantidade), MIN(data_hora)
            FROM registro_consumo
            WHERE ingrediente_id = ?
        """, (ing_id,))
        row = cursor.fetchone()
        novo_burn_rate = 0.0

        if row and row[0] is not None:
            total_consumido = row[0]
            dt_primeiro = datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S")

            dias_desde_primeiro_uso = (agora - dt_primeiro).total_seconds() / 86400.0

            # Garante mínimo de 1 dia para evitar divisão por zero
            # (item cadastrado e consumido no mesmo dia).
            if dias_desde_primeiro_uso < 1.0:
                dias_desde_primeiro_uso = 1.0

            novo_burn_rate = round(total_consumido / dias_desde_primeiro_uso, 3)

        cursor.execute(
            "UPDATE ingredientes SET burn_rate_diario = ? WHERE id = ?",
            (novo_burn_rate, ing_id)
        )
        if cursor.rowcount > 0:
            itens_atualizados += 1

    conn.commit()
    conn.close()
    return {"status": "sucesso", "atualizados": itens_atualizados}


@router.get("/estoque_critico", summary="Listar ingredientes em nível crítico")
async def estoque_critico():
    """Retorna ingredientes usados em receitas que estão abaixo do limite de alerta.

    Filtra apenas ingredientes vinculados a receitas ativas para evitar
    alertas irrelevantes de itens sem demanda.

    Returns:
        list[dict]: Ingredientes críticos com estoque, unidade e limite.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT i.id, i.nome, i.estoque_atual, i.unidade, i.limite_alerta
        FROM ingredientes i
        JOIN receita_itens ri ON i.id = ri.ingrediente_id
        WHERE i.estoque_atual <= i.limite_alerta
          AND (i.escondido = 0 OR i.escondido IS NULL)
        ORDER BY i.estoque_atual ASC
    """)
    itens = [
        {"id": r[0], "nome": r[1], "estoque": round(r[2], 2), "unidade": r[3], "limite": r[4]}
        for r in cursor.fetchall()
    ]
    conn.close()
    return itens


@router.get("/previsao_demanda", summary="Previsão de demanda por sabor")
async def previsao_demanda(dias_projecao: int = 15, dias_historico: int = 30):
    """Projeta a demanda de cada sabor com base no histórico de produção.

    Args:
        dias_projecao: Horizonte da projeção em dias (padrão: 15).
        dias_historico: Período histórico de referência em dias (padrão: 30).

    Returns:
        list[dict]: Projeção de demanda por sabor com meta em kg e cubas.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT nome, categoria FROM sabores")
    sabores = cursor.fetchall()
    data_limite = (datetime.now() - timedelta(days=dias_historico)).strftime("%Y-%m-%d %H:%M:%S")
    analise_sabores = []

    for sabor, categoria in sabores:
        sabor_upper = sabor.upper().strip()
        cursor.execute(
            "SELECT SUM(quantidade_kg) FROM producao_diaria WHERE UPPER(sabor) = ? AND data_hora >= ?",
            (sabor_upper, data_limite)
        )
        total_hist = cursor.fetchone()[0] or 0
        media_diaria = total_hist / float(dias_historico)
        meta_kg = round(media_diaria * dias_projecao, 2)

        cursor.execute(
            "SELECT rendimento_cubas FROM receitas WHERE nome_gelato = ?", (sabor_upper,)
        )
        row = cursor.fetchone()
        tem_receita = bool(row)
        rendimento = row[0] if row else 1
        meta_cubas = round(meta_kg / rendimento, 1) if rendimento else 0

        analise_sabores.append({
            "sabor": sabor_upper, "categoria": categoria,
            "meta_kg": meta_kg, "meta_cubas": meta_cubas,
            "rendimento": rendimento, "tem_receita": tem_receita
        })

    conn.close()
    return analise_sabores


@router.post("/gerar_lista_compras", summary="Gerar lista de compras por metas de produção")
async def gerar_lista_compras(dados: ListaMetas):
    """Gera uma lista de compras detalhada com base nas metas de produção informadas.

    Para cada sabor com meta > 0:
    1. Busca a receita correspondente.
    2. Calcula a demanda de ingredientes proporcional à meta.
    3. Subtrai o estoque atual (cozinha + almoxarifado convertido).
    4. Sugere a quantidade a comprar arredondada para a unidade de compra.

    Args:
        dados: Lista de sabores com suas metas de produção em kg.

    Returns:
        dict: ``{"itens": [...], "custo_total": float}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    demanda_ingredientes = {}

    for item in dados.metas:
        if item.meta_kg <= 0:
            continue
        cursor.execute(
            "SELECT id, rendimento_cubas FROM receitas WHERE nome_gelato = ?",
            (item.sabor.upper().strip(),)
        )
        receita = cursor.fetchone()
        if receita:
            rec_id, rendimento = receita
            multiplicador = item.meta_kg / rendimento
            cursor.execute(
                "SELECT ingrediente_id, quantidade FROM receita_itens WHERE receita_id = ?",
                (rec_id,)
            )
            for ing_id, qtd in cursor.fetchall():
                demanda_ingredientes[ing_id] = demanda_ingredientes.get(ing_id, 0) + (qtd * multiplicador)

    lista_compras = []
    custo_total = 0.0

    for ing_id, demanda_producao in demanda_ingredientes.items():
        cursor.execute("""
            SELECT nome, estoque_atual, estoque_almoxarifado, fator_conversao,
                   unidade_almoxarifado, unidade, preco_unitario, limite_alerta
            FROM ingredientes WHERE id = ?
        """, (ing_id,))
        row = cursor.fetchone()
        if row:
            nome, est_coz, est_almox, fator, unid_almox, unid_coz, preco_unitario, limite_alerta = row
            # Converte o estoque do almoxarifado para a unidade da cozinha antes de somar.
            estoque_total_atual = est_coz + (est_almox * fator)
            demanda_total = demanda_producao + limite_alerta

            if estoque_total_atual < demanda_total:
                deficit = demanda_total - estoque_total_atual
                comprar_unidades = math.ceil(deficit / fator) if fator > 0 else math.ceil(deficit)
                custo_estimado = comprar_unidades * preco_unitario
                custo_total += custo_estimado

                lista_compras.append({
                    "ingrediente": nome,
                    "demanda": round(demanda_producao, 2),
                    "estoque_seguranca": limite_alerta,
                    "estoque": round(estoque_total_atual, 2),
                    "unidade_coz": unid_coz,
                    "comprar_qtd": comprar_unidades,
                    "unidade_compra": unid_almox,
                    "custo_estimado": custo_estimado
                })

    conn.close()
    return {"itens": lista_compras, "custo_total": custo_total}


# ==========================================
# NF-e (NOTA FISCAL ELETRÔNICA)
# ==========================================

@router.post("/entrada_nfe", summary="Lançar entrada via NF-e no almoxarifado")
async def entrada_nfe(dados: EntradaNFE):
    """Processa uma Nota Fiscal Eletrônica e lança os itens no almoxarifado.

    Garante idempotência via chave de acesso: a mesma NF-e nunca é lançada
    duas vezes, mesmo que o usuário importe o XML repetidamente.

    Args:
        dados: Chave de acesso da NF-e e lista de itens vinculados.

    Returns:
        dict: ``{"status": "sucesso"}``

    Raises:
        HTTPException: 400 se a nota já foi importada anteriormente.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    data_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        "SELECT 1 FROM registro_nfe WHERE chave_acesso = ?", (dados.chave_acesso,)
    )
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Esta Nota Fiscal já foi lançada anteriormente!")

    for item in dados.itens:
        cursor.execute("""
            UPDATE ingredientes
            SET estoque_almoxarifado = estoque_almoxarifado + ?,
                preco_unitario = ?,
                fator_conversao = ?
            WHERE id = ?
        """, (item.qtd_comprada, item.preco_unitario, item.fator_conversao, item.ingrediente_id))

        cursor.execute(
            "INSERT INTO historico_entradas (ingrediente_id, quantidade, tipo, data_hora) VALUES (?, ?, 'NF-e', ?)",
            (item.ingrediente_id, item.qtd_comprada, data_atual)
        )

        cursor.execute("""
            INSERT OR REPLACE INTO mapeamento_nfe (xprod, ingrediente_id, fator_conversao)
            VALUES (?, ?, ?)
        """, (item.xprod, item.ingrediente_id, item.fator_conversao))

    cursor.execute(
        "INSERT INTO registro_nfe (chave_acesso, data_importacao) VALUES (?, ?)",
        (dados.chave_acesso, data_atual)
    )
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@router.get("/historico_entradas", summary="Histórico de entradas no almoxarifado")
async def ver_historico():
    """Retorna os últimos 50 lançamentos de entrada no almoxarifado.

    Returns:
        list[dict]: Histórico com data, ingrediente, quantidade, unidade e tipo.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT h.data_hora, i.nome, h.quantidade, i.unidade_almoxarifado, h.tipo
        FROM historico_entradas h
        JOIN ingredientes i ON h.ingrediente_id = i.id
        ORDER BY h.id DESC LIMIT 50
    """)
    res = [
        {"data": r[0], "nome": r[1], "qtd": r[2], "unidade": r[3], "tipo": r[4]}
        for r in cursor.fetchall()
    ]
    conn.close()
    return res


@router.get("/mapeamentos_nfe", summary="Obter mapeamentos de produtos da NF-e")
async def obter_mapeamentos():
    """Retorna o dicionário de mapeamento inteligente NF-e → ingrediente.

    Usado para pré-preencher o formulário de importação de notas futuras
    do mesmo fornecedor, reduzindo o trabalho manual do operador.

    Returns:
        dict: Mapeamento ``{xprod: {"ingrediente_id": int, "fator_conversao": float}}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT xprod, ingrediente_id, fator_conversao FROM mapeamento_nfe")
    res = {r[0]: {"ingrediente_id": r[1], "fator_conversao": r[2]} for r in cursor.fetchall()}
    conn.close()
    return res


# ==========================================
# QUEBRAS / PERDAS
# ==========================================

@router.post("/registrar_quebras_lote", summary="Registrar quebras/perdas em lote")
async def registrar_quebras_lote(dados: ListaQuebras):
    """Registra perdas de ingredientes da cozinha com motivo para rastreabilidade.

    Args:
        dados: Lista de quebras com ingrediente, quantidade e motivo.

    Returns:
        dict: ``{"status": "sucesso"}``
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    data_atual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for item in dados.itens:
        cursor.execute(
            "UPDATE ingredientes SET estoque_atual = estoque_atual - ? WHERE id = ?",
            (item.quantidade, item.ingrediente_id)
        )
        cursor.execute(
            "INSERT INTO registro_quebras (ingrediente_id, quantidade, motivo, data_hora) VALUES (?, ?, ?, ?)",
            (item.ingrediente_id, item.quantidade, item.motivo, data_atual)
        )
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


# ==========================================
# RELATÓRIOS E EXPORTAÇÕES
# ==========================================

@router.get("/exportar_excel", summary="Exportar relatório de produção em CSV")
async def exportar():
    """Exporta o histórico completo de produção como arquivo CSV.

    Returns:
        StreamingResponse: Arquivo CSV com separador ponto-e-vírgula (padrão PT-BR).
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, data_hora, sabor, quantidade_kg FROM producao_diaria ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()

    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=';')
    writer.writerow(["ID", "Data", "Produto", "Quantidade (Kg)"])
    for r in rows:
        writer.writerow([r[0], r[1], r[2], str(r[3]).replace('.', ',')])

    return StreamingResponse(
        iter([stream.getvalue().encode('utf-8-sig')]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=relatorio_producao.csv"}
    )


@router.get("/exportar_config_alertas", summary="Exportar configuração de alertas em CSV")
async def exportar_config_alertas():
    """Exporta os parâmetros de MRP de todos os ingredientes ativos como CSV.

    Returns:
        StreamingResponse: Arquivo CSV com parâmetros de estoque e MRP.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT nome, estoque_atual, unidade, limite_alerta, lead_time, burn_rate_diario, volatil
        FROM ingredientes WHERE escondido = 0 ORDER BY nome
    """)
    rows = cursor.fetchall()
    conn.close()

    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=';')
    writer.writerow([
        "Insumo", "Estoque Atual (Cozinha)", "Unidade",
        "Gatilho de Alerta", "Lead Time (Dias)", "Burn Rate/Dia", "Sazonal"
    ])
    for r in rows:
        writer.writerow([
            r[0], str(r[1]).replace('.', ','), r[2],
            str(r[3]).replace('.', ','), r[4],
            str(r[5]).replace('.', ','),
            "Sim" if r[6] else "Não"
        ])

    return StreamingResponse(
        iter([stream.getvalue().encode('utf-8-sig')]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=parametros_estoque.csv"}
    )


@router.get("/download_backup", summary="Fazer download do banco de dados")
async def download_backup():
    """Retorna o arquivo do banco de dados SQLite para backup manual.

    Returns:
        FileResponse: Arquivo .db com timestamp no nome para versionamento.
    """
    data_atual = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_arquivo = f"backup_motor_estoque_{data_atual}.db"
    return FileResponse(DB_FILE, media_type='application/octet-stream', filename=nome_arquivo)


# ==========================================
# E-MAIL (REQUER CONFIGURAÇÃO DE ENV VARS)
# ==========================================

def _smtp_send_sync(msg: MIMEMultipart) -> None:
    """Executa o envio SMTP de forma síncrona, projetada para ser chamada
    via ``asyncio.to_thread`` a partir de handlers assíncronos.

    Motivo: ``smtplib.SMTP`` é uma biblioteca I/O bloqueante. Chamá-la
    diretamente em um handler ``async`` do FastAPI bloquearia o event loop
    de Uvicorn durante toda a negociação TLS + envio (tipicamente 2–5s),
    impedindo o processamento de outras requisições simultâneas.

    Args:
        msg: Mensagem MIME pronta para envio.
    """
    s = smtplib.SMTP('smtp.gmail.com', 587)
    s.starttls()
    s.login(EMAIL_REMETENTE, EMAIL_SENHA)
    s.send_message(msg)
    s.quit()

@router.post("/enviar_email", summary="Enviar relatório de produção por e-mail")
async def enviar_email():
    """Envia o relatório de produção por e-mail para o endereço configurado.

    Requer as variáveis de ambiente EMAIL_REMETENTE e EMAIL_SENHA.

    Returns:
        dict: ``{"status": "sucesso"}`` ou mensagem de erro de configuração.

    Raises:
        HTTPException: 500 em caso de falha no envio.
    """
    if not EMAIL_REMETENTE or not EMAIL_SENHA:
        raise HTTPException(
            status_code=503,
            detail="E-mail não configurado. Defina EMAIL_REMETENTE e EMAIL_SENHA como variáveis de ambiente."
        )
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT id, data_hora, sabor, quantidade_kg FROM producao_diaria ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()

        stream = io.StringIO()
        writer = csv.writer(stream, delimiter=';')
        writer.writerow(["ID", "Data", "Produto", "Quantidade (Kg)"])
        for r in rows:
            writer.writerow([r[0], r[1], r[2], str(r[3]).replace('.', ',')])

        msg = MIMEMultipart()
        msg['From'] = EMAIL_REMETENTE
        msg['To'] = EMAIL_REMETENTE
        msg['Subject'] = f"Relatório de Produção — Motor Gelato — {datetime.now().strftime('%d/%m/%Y')}"
        msg.attach(MIMEText("Relatório completo de produção em anexo.", 'plain'))
        anexo = MIMEApplication(stream.getvalue().encode('utf-8-sig'))
        anexo.add_header('Content-Disposition', 'attachment', filename='producao.csv')
        msg.attach(anexo)

        # Delega o envio bloqueante para uma thread separada,
        # liberando o event loop durante a operação de rede.
        await asyncio.to_thread(_smtp_send_sync, msg)
        return {"status": "sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/enviar_email_compras", summary="Enviar alerta inteligente de estoque por e-mail")
async def enviar_email_compras(dados: RequestEmailCompras):
    """Envia um e-mail HTML com os itens abaixo do ponto de pedido e validades críticas.

    Args:
        dados: Lista de IDs de ingredientes a ignorar neste envio.

    Returns:
        dict: ``{"status": "sucesso"}`` ou ``{"status": "vazio", "mensagem": str}``

    Raises:
        HTTPException: 503 se o e-mail não estiver configurado.
        HTTPException: 500 em caso de falha no envio.
    """
    if not EMAIL_REMETENTE or not EMAIL_SENHA:
        raise HTTPException(
            status_code=503,
            detail="E-mail não configurado. Defina EMAIL_REMETENTE e EMAIL_SENHA como variáveis de ambiente."
        )
    try:
        mrp_lista = await gerar_mrp_visual()

        if dados.ignorados and mrp_lista:
            mrp_lista = [item for item in mrp_lista if item['id'] not in dados.ignorados]

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        mes_atual = datetime.now().strftime("%Y-%m")
        cursor.execute("""
            SELECT nome, validade, estoque_almoxarifado, unidade_almoxarifado
            FROM ingredientes
            WHERE escondido = 0 AND validade <= ? AND validade != ''
        """, (mes_atual,))
        vencimentos = cursor.fetchall()
        conn.close()

        if not mrp_lista and not vencimentos:
            return {"status": "vazio", "mensagem": "Nada a avisar."}

        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #1e293b; background: #f8fafc; padding: 20px;">
            <div style="background: white; padding: 30px; border-radius: 10px; max-width: 600px;
                        margin: auto; border-top: 5px solid #14b8a6; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
                <h2 style="color: #14b8a6; margin-top: 0;">📦 Motor Gelato — Resumo de Compras</h2>
                <p>Situação atualizada do estoque em <b>{datetime.now().strftime('%d/%m/%Y')}</b>.</p>
        """

        if mrp_lista:
            html += """
                <h3 style="color: #1e293b; border-bottom: 2px solid #e2e8f0; padding-bottom: 5px;">
                    🛒 Sugestão de Compras (Abaixo do Gatilho)
                </h3>
                <table style="width: 100%; border-collapse: collapse; margin-bottom: 25px;">
                    <tr style="background: #f8fafc; text-align: left;">
                        <th style="padding: 10px; border-bottom: 1px solid #e2e8f0;">Insumo</th>
                        <th style="padding: 10px; border-bottom: 1px solid #e2e8f0;">Estoque</th>
                        <th style="padding: 10px; border-bottom: 1px solid #e2e8f0;">Comprar</th>
                    </tr>
            """
            for item in mrp_lista:
                html += f"""
                    <tr>
                        <td style="padding: 10px; border-bottom: 1px solid #e2e8f0;"><b>{item['nome']}</b></td>
                        <td style="padding: 10px; border-bottom: 1px solid #e2e8f0; color: #ef4444;">
                            {item['estoque']} {item['unidade']}</td>
                        <td style="padding: 10px; border-bottom: 1px solid #e2e8f0;
                                   color: #14b8a6; font-weight: bold;">
                            {item['comprar']} {item['unidade']}</td>
                    </tr>
                """
            html += "</table>"

        if vencimentos:
            html += """
                <h3 style="color: #ef4444; border-bottom: 2px solid #fecaca; padding-bottom: 5px;">
                    ⚠️ Validades Críticas
                </h3>
                <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
            """
            for v in vencimentos:
                alerta_cor = "#ea580c" if v[1] == mes_atual else "#ef4444"
                html += f"""
                    <tr>
                        <td style="padding: 10px; border-bottom: 1px solid #e2e8f0;"><b>{v[0]}</b></td>
                        <td style="padding: 10px; border-bottom: 1px solid #e2e8f0;">
                            {v[2]} {v[3]}</td>
                        <td style="padding: 10px; border-bottom: 1px solid #e2e8f0;
                                   color: {alerta_cor}; font-weight: bold;">{v[1]}</td>
                    </tr>
                """
            html += "</table>"

        html += """
                <p style="margin-top: 30px; font-size: 12px; color: #94a3b8; text-align: center;">
                    Alerta automático — Motor Gelato ERP
                </p>
            </div>
        </body>
        </html>
        """

        msg = MIMEMultipart("alternative")
        msg['From'] = EMAIL_REMETENTE
        msg['To'] = EMAIL_REMETENTE
        msg['Subject'] = f"📦 Alerta de Estoque — Motor Gelato — {datetime.now().strftime('%d/%m/%Y')}"
        msg.attach(MIMEText(html, "html"))

        # Delega o envio bloqueante para uma thread separada,
        # liberando o event loop durante a operação de rede.
        await asyncio.to_thread(_smtp_send_sync, msg)
        return {"status": "sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
