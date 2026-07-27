"""
models.py — Motor Gelato
=========================

Schemas Pydantic para validação e serialização dos dados de entrada da API.

Cada classe representa o corpo (body) esperado em endpoints específicos do FastAPI.
O Pydantic garante que os dados recebidos respeitem os tipos e constraints definidos,
retornando automaticamente HTTP 422 para payloads inválidos.
"""

from pydantic import BaseModel
from typing import Literal


class Producao(BaseModel):
    """Payload para registrar uma nova produção no módulo Cozinha.

    Attributes:
        sabor: Nome do produto/sabor produzido (será normalizado para uppercase).
        quantidade: Peso em kg ou unidade do lote produzido.
    """
    sabor: str
    quantidade: float


class Alerta(BaseModel):
    """Payload para disparar um alerta de reposição via WebSocket.

    Quando o balcão (PDV) aciona este endpoint, um sinal em tempo real
    é enviado para todos os clientes WebSocket conectados na tela de Cozinha.

    Attributes:
        sabor: Nome do produto que necessita reposição urgente.
    """
    sabor: str


class EdicaoSabores(BaseModel):
    """Payload para substituição completa do cardápio de sabores/produtos.

    Attributes:
        fixos: Lista de nomes de produtos permanentes no mix.
        rotativos: Lista de nomes de produtos sazonais/rotativos.
    """
    fixos: list[str]
    rotativos: list[str]


class SaborMeta(BaseModel):
    """Meta de produção individual para um sabor específico.

    Attributes:
        sabor: Nome do produto.
        meta_kg: Quantidade-alvo em kg para o período de planejamento.
    """
    sabor: str
    meta_kg: float


class RegistroQuebra(BaseModel):
    """Registro de uma perda/quebra de um ingrediente do estoque da Cozinha.

    Attributes:
        ingrediente_id: ID do ingrediente afetado.
        quantidade: Volume perdido na unidade de medida do ingrediente.
        motivo: Descrição textual do motivo da quebra (ex: "Vencimento", "Derramamento").
    """
    ingrediente_id: int
    quantidade: float
    motivo: str


class ListaQuebras(BaseModel):
    """Lista de quebras para lançamento em lote.

    Attributes:
        itens: Conjunto de registros de quebra a serem processados atomicamente.
    """
    itens: list[RegistroQuebra]


class ListaMetas(BaseModel):
    """Lista de metas de produção para geração da lista de compras (MRP).

    Attributes:
        metas: Conjunto de pares sabor/meta_kg para o cálculo de demanda.
    """
    metas: list[SaborMeta]


class ItemNFE(BaseModel):
    """Representa um item individual dentro de uma Nota Fiscal Eletrônica.

    Attributes:
        xprod: Descrição do produto conforme consta na NF-e (usado para mapeamento).
        ingrediente_id: ID do ingrediente no sistema ao qual este item será vinculado.
        qtd_comprada: Quantidade adquirida na unidade do almoxarifado (ex: caixas).
        preco_unitario: Preço unitário do item, atualizado no cadastro do ingrediente.
        fator_conversao: Quantas unidades internas (ex: kg) equivalem a 1 unidade de compra.
    """
    xprod: str
    ingrediente_id: int
    qtd_comprada: float
    preco_unitario: float
    fator_conversao: float


class EntradaNFE(BaseModel):
    """Payload completo de uma Nota Fiscal Eletrônica para entrada no almoxarifado.

    Attributes:
        chave_acesso: Chave de 44 dígitos da NF-e (garante idempotência — sem lançamento duplo).
        itens: Lista de produtos vinculados da nota.
    """
    chave_acesso: str
    itens: list[ItemNFE]


class ItemEstoquePronto(BaseModel):
    """Item do estoque de produto acabado na Vitrine/PDV.

    Attributes:
        sabor: Nome do produto acabado.
        quantidade_cubas: Quantidade de unidades (cubas/potes) disponíveis.
    """
    sabor: str
    quantidade_cubas: float


class ListaEstoquePronto(BaseModel):
    """Lista para atualização em lote do estoque de produto acabado.

    Attributes:
        itens: Conjunto de itens com suas quantidades atualizadas.
    """
    itens: list[ItemEstoquePronto]


class NovoIngrediente(BaseModel):
    """Payload para cadastro de um novo ingrediente/insumo no sistema.

    Attributes:
        nome: Nome do ingrediente (será normalizado para uppercase).
        unidade: Unidade de medida na cozinha (ex: "KG", "L", "UN").
        preco_unitario: Custo por unidade de compra (usado no cálculo de CMV).
        estoque_atual: Quantidade inicial na cozinha.
        limite_alerta: Gatilho de alerta antigo (substituído pelo modelo de burn rate).
        estoque_almoxarifado: Quantidade inicial no almoxarifado.
        unidade_almoxarifado: Unidade de compra (ex: "Caixa", "Fardo", "Saco").
        fator_conversao: Quantas unidades de cozinha equivalem a 1 unidade de compra.
        lead_time: Dias de espera entre o pedido e a entrega do fornecedor.
        burn_rate_diario: Taxa de consumo diária estimada (calculada automaticamente).
        volatil: Indica se a demanda é sensível a fatores externos (ex: clima).
    """
    nome: str
    unidade: str
    preco_unitario: float
    estoque_atual: float
    limite_alerta: float
    estoque_almoxarifado: float
    unidade_almoxarifado: str
    fator_conversao: float
    lead_time: int = 7
    burn_rate_diario: float = 0.5
    volatil: bool = False


class AtualizacaoParametros(BaseModel):
    """Payload para atualização dos parâmetros de inteligência de compras.

    Esses parâmetros alimentam o modelo MRP (Material Requirements Planning)
    que calcula automaticamente o ponto de pedido e a quantidade a comprar.

    Attributes:
        novo_limite: Gatilho de alerta manual (complementa o burn rate).
        novo_lead_time: Novo prazo de entrega do fornecedor em dias.
        novo_burn_rate: Nova taxa de consumo diária (pode ser ajustada manualmente).
        novo_volatil: Indica se o item é sensível a variações de demanda externas.
    """
    novo_limite: float
    novo_lead_time: int
    novo_burn_rate: float
    novo_volatil: bool


class Transferencia(BaseModel):
    """Payload para transferência de um insumo entre níveis de estoque.

    Attributes:
        item_id: ID do ingrediente a ser transferido.
        qtd_transferida: Quantidade a mover na unidade do almoxarifado.
    """
    item_id: int
    qtd_transferida: float


class TransferenciaEstoque(BaseModel):
    """Payload tipado para transferência explícita entre os 3 níveis de estoque.

    Permite rastreabilidade completa ao registrar origem e destino de forma
    explícita, em vez de inferir a direção pelo contexto da rota.

    Attributes:
        item_id: ID do ingrediente ou produto a ser movido.
        qtd_transferida: Quantidade a mover na unidade da origem.
        origem: Nível de estoque de onde o item sai.
        destino: Nível de estoque para onde o item vai.
        observacao: Nota opcional para o registro de auditoria.
    """
    item_id: int
    qtd_transferida: float
    origem: Literal["almoxarifado", "cozinha"]
    destino: Literal["cozinha", "vitrine"]
    observacao: str = ""


class ItemReceita(BaseModel):
    """Ingrediente individual dentro de uma ficha técnica de produção.

    Attributes:
        ingrediente_id: ID do ingrediente utilizado na receita.
        quantidade: Quantidade necessária na unidade de cozinha (ex: kg, L).
    """
    ingrediente_id: int
    quantidade: float


class NovaReceita(BaseModel):
    """Payload para criação ou atualização de uma ficha técnica de produção.

    Attributes:
        nome_gelato: Nome do produto fabricado com esta receita.
        rendimento_cubas: Quantidade de unidades (cubas/potes) que a receita produz.
        itens: Lista de ingredientes e quantidades da receita.
    """
    nome_gelato: str
    rendimento_cubas: int
    itens: list[ItemReceita]


class AtualizacaoEstoque(BaseModel):
    """Payload para atualização direta de quantidade de estoque (inventário manual).

    Attributes:
        novo_estoque: Novo valor absoluto de estoque a ser registrado.
    """
    novo_estoque: float


class AtualizacaoPreco(BaseModel):
    """Payload para atualização do preço unitário de um ingrediente.

    Attributes:
        novo_preco: Novo preço unitário em reais (R$).
    """
    novo_preco: float


class AtualizacaoLimite(BaseModel):
    """Payload para atualização do gatilho de alerta de estoque mínimo.

    Attributes:
        novo_limite: Novo valor de estoque mínimo que aciona o alerta.
    """
    novo_limite: float


class AtualizacaoFator(BaseModel):
    """Payload para atualização do fator de conversão de unidades.

    Attributes:
        novo_fator: Novo fator de conversão (ex: 1 Caixa = 12 KG → fator = 12).
    """
    novo_fator: float


class ItemInventario(BaseModel):
    """Item individual durante um processo de contagem de inventário.

    Attributes:
        id: ID do ingrediente sendo contado.
        estoque_real: Quantidade física contada (substituirá o valor do sistema).
        validade: Data de validade do lote mais antigo no formato YYYY-MM.
    """
    id: int
    estoque_real: float
    validade: str = ""


class ListaInventario(BaseModel):
    """Lista completa de uma sessão de inventário físico.

    Attributes:
        itens: Conjunto de contagens realizadas durante o inventário.
    """
    itens: list[ItemInventario]


class LoginRequest(BaseModel):
    """Payload de autenticação via PIN numérico.

    Attributes:
        pin: Código PIN numérico do usuário (máximo 8 dígitos).
    """
    pin: str


class RegistroCubaVazia(BaseModel):
    """Payload para registrar o esvaziamento completo de uma cuba/unidade na vitrine.

    Attributes:
        sabor: Nome do produto cuja cuba foi esvaziada no PDV.
    """
    sabor: str


class RegistroRefill(BaseModel):
    """Payload para registrar a troca (refill) de uma cuba na vitrine.

    O sistema calcula o consumo real subtraindo o peso restante da cuba antiga
    do peso que havia sido enviado originalmente, garantindo acuracidade no CMV.

    Attributes:
        sabor: Nome do produto cuja cuba está sendo trocada.
        peso_restante: Peso em kg que sobrou na cuba que está sendo retirada.
        peso_novo: Peso em kg da nova cuba que entrou no lugar.
    """
    sabor: str
    peso_restante: float
    peso_novo: float


class RegistroVitrine(BaseModel):
    """Payload para operações de envio e retorno da vitrine refrigerada.

    Attributes:
        sabor: Nome do produto sendo manipulado na vitrine.
        peso: Peso em kg registrado (enviado ou retornado).
    """
    sabor: str
    peso: float


class NovoProdutoPDV(BaseModel):
    """Payload para cadastro de um produto no PDV com peso teórico de referência.

    Attributes:
        nome: Nome do produto no PDV.
        peso_teorico: Peso padrão de referência da embalagem do produto.
    """
    nome: str
    peso_teorico: float


class EntradaManual(BaseModel):
    """Payload para lançamento avulso de entrada de estoque (sem NF-e).

    Útil para compras em dinheiro ou doações que não geram nota fiscal eletrônica.

    Attributes:
        quantidade: Volume recebido na unidade do almoxarifado.
        motivo: Descrição da origem desta entrada (ex: "Compra mercado local").
    """
    quantidade: float
    motivo: str


class RequestEmailCompras(BaseModel):
    """Payload para disparo do e-mail de alerta de reposição de estoque.

    Attributes:
        ignorados: Lista de IDs de ingredientes a excluir do relatório desta vez.
                   Permite que o gestor suprima itens irrelevantes pontualmente.
    """
    ignorados: list[int] = []
