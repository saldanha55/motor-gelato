/**
 * dashboard.js — Motor Gelato
 * =============================
 *
 * Módulo JavaScript do painel de Gestão de Estoque.
 *
 * Responsabilidades:
 *   - Carregar e renderizar a lista de compras inteligente (MRP).
 *   - Integrar com a API de previsão meteorológica para ajuste de demanda.
 *   - Gerenciar o sistema de ocultação temporária de itens no MRP.
 *   - Controlar a navegação entre abas e o toggle de tema claro/escuro.
 */

/** Cache da última lista MRP calculada — usado para exportar em CSV. */
let ultimaListaMRP = [];


// ==========================================
// INTELIGÊNCIA CLIMÁTICA + MRP
// ==========================================

/**
 * Busca a previsão meteorológica dos próximos 7 dias via Open-Meteo (API gratuita).
 *
 * Retorna um multiplicador de demanda para ajustar a sugestão de compra de
 * produtos marcados como "voláteis" (sensíveis a variações climáticas):
 *   - Calor extremo (≥27°C, chuva < 20mm): +25% de compra
 *   - Frio ou chuvas intensas (< 21°C ou > 40mm): -20% de compra
 *   - Clima neutro: multiplicador = 1.0 (sem ajuste)
 *
 * O AbortController garante que a chamada seja cancelada em 5 segundos,
 * evitando que a API lenta bloqueie o carregamento do MRP.
 *
 * @returns {Promise<number>} Multiplicador de demanda (ex: 1.25, 0.8, 1.0).
 */
async function buscarTendenciaMensal() {
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);

        const resp = await fetch(
            'https://api.open-meteo.com/v1/forecast' +
            '?latitude=-22.23&longitude=-45.93' +
            '&daily=temperature_2m_max,precipitation_sum' +
            '&timezone=America%2FSao_Paulo',
            { signal: controller.signal }
        );
        clearTimeout(timeoutId);

        const data = await resp.json();
        const temps = data.daily.temperature_2m_max;
        const chuvas = data.daily.precipitation_sum;
        const tempMedia = temps.reduce((a, b) => a + b, 0) / temps.length;
        const chuvaTotal = chuvas.reduce((a, b) => a + b, 0);

        const hoje = new Date();
        const mesAlvo = hoje.getDate() <= 14 ? 'mês atual' : 'próximo mês';

        let multiplicador = 1.0;
        let emoji = '☁️';
        let titulo = `Projeção Estável para o ${mesAlvo}`;
        let mensagem = `Clima ameno previsto (${tempMedia.toFixed(1)}°C). Produtos voláteis mantêm a compra base de 30 dias.`;

        if (tempMedia >= 27 && chuvaTotal < 20) {
            multiplicador = 1.25;
            emoji = '🔥';
            titulo = `Alerta de Alta Demanda (${mesAlvo})!`;
            mensagem = `Previsão de onda de calor (${tempMedia.toFixed(1)}°C). Sugestão dos produtos VOLÁTEIS aumentada em 25% para evitar ruptura.`;
        } else if (tempMedia < 21 || chuvaTotal > 40) {
            multiplicador = 0.8;
            emoji = '🌧️';
            titulo = `Contenção de Compras (${mesAlvo})`;
            mensagem = `Previsão de frio ou chuvas intensas. Sugestão dos produtos VOLÁTEIS reduzida em 20% para proteger o fluxo de caixa.`;
        }

        document.getElementById('cardClima').style.display = 'flex';
        document.getElementById('iconeClima').innerText = emoji;
        document.getElementById('tituloClima').innerText = titulo;
        document.getElementById('textoClima').innerText = mensagem;

        return multiplicador;
    } catch (e) {
        // API indisponível ou timeout — opera sem ajuste climático.
        return 1.0;
    }
}

/**
 * Carrega e renderiza a lista inteligente de compras (MRP) na tabela correspondente.
 *
 * Sequência de execução:
 * 1. Busca a tendência climática (com timeout de 5s).
 * 2. Busca a lista MRP do backend (/api/mrp).
 * 3. Filtra os itens ocultados pelo usuário hoje (via localStorage).
 * 4. Aplica o multiplicador climático nos produtos voláteis.
 * 5. Renderiza a tabela com as sugestões ajustadas.
 */
async function carregarMRPVisual() {
    const tb = document.getElementById('tabelaMRPVisual');
    tb.innerHTML = "<tr><td colspan='6' style='text-align:center; padding: 20px;'>⏳ Calculando Burn Rate e cruzando matriz climática...</td></tr>";

    try {
        const fatorClima = await buscarTendenciaMensal();
        const resp = await fetch(`${BASE_URL}/api/mrp`);
        let dadosBase = await resp.json();

        // Filtra itens que o usuário optou por ocultar hoje.
        // Usamos a data como chave no localStorage para que os itens
        // voltem automaticamente no dia seguinte — sem precisar de limpeza manual.
        const hojeStr = new Date().toISOString().split('T')[0];
        const ignoradosHoje = JSON.parse(localStorage.getItem(`mrp_ocultos_${hojeStr}`)) || [];
        dadosBase = dadosBase.filter(item => !ignoradosHoje.includes(item.id));

        if (dadosBase.length === 0) {
            tb.innerHTML = "<tr><td colspan='6' style='text-align:center; color: var(--primary); font-weight: bold; padding: 20px;'>✅ Estoque saudável para os próximos 30 dias!</td></tr>";
            return;
        }

        tb.innerHTML = '';
        ultimaListaMRP = [];

        dadosBase.forEach(item => {
            // Aplica fator climático apenas em produtos marcados como voláteis.
            let compraAjustada = item.volatil ? Math.ceil(item.comprar * fatorClima) : item.comprar;
            if (compraAjustada < 1) compraAjustada = 1;
            item.comprar = compraAjustada;
            ultimaListaMRP.push(item);

            const seloVolatil = item.volatil
                ? '<span title="Sensível ao Clima" style="font-size: 12px; margin-left: 5px;">🌡️</span>'
                : '';

            tb.innerHTML += `<tr>
                <td style="padding: 12px;"><b>${item.nome}</b>${seloVolatil}</td>
                <td style="padding: 12px; color: var(--text-muted);">${item.burn_rate} ${item.unidade}/dia</td>
                <td style="padding: 12px; color: var(--text-muted);">🚚 ${item.lead_time} dias</td>
                <td style="padding: 12px;"><span style="color: var(--danger); font-weight: bold;">${item.estoque} ${item.unidade}</span></td>
                <td style="padding: 12px;"><span class="badge badge-kg" style="font-size: 14px;">+ ${compraAjustada} ${item.unidade}</span></td>
                <td style="padding: 12px; text-align: center;">
                    <button class="btn-acao btn-edit"
                        onclick="abrirModalEdicao(${item.id}, '${item.nome.replace(/'/g, "\\'")}', ${item.limite}, ${item.lead_time}, ${item.burn_rate}, ${item.volatil})">
                        ✏️ Editar
                    </button>
                    <button class="btn-acao" style="background: transparent; border: 1px solid var(--border); color: var(--text-muted);"
                        onclick="ocultarItemHojeMRP(${item.id})">
                        👁 Ocultar
                    </button>
                </td>
            </tr>`;
        });
    } catch (e) {
        tb.innerHTML = "<tr><td colspan='6' style='text-align:center; color: var(--danger); padding: 20px;'>❌ Erro ao processar o motor MRP.</td></tr>";
    }
}

/**
 * Oculta um item da lista MRP pelo restante do dia atual (usando localStorage).
 *
 * @param {number} id - ID do ingrediente a ocultar.
 */
window.ocultarItemHojeMRP = function (id) {
    const hojeStr = new Date().toISOString().split('T')[0];
    const ignoradosHoje = JSON.parse(localStorage.getItem(`mrp_ocultos_${hojeStr}`)) || [];
    if (!ignoradosHoje.includes(id)) {
        ignoradosHoje.push(id);
        localStorage.setItem(`mrp_ocultos_${hojeStr}`, JSON.stringify(ignoradosHoje));
    }
    carregarMRPVisual();
};

/**
 * Remove todos os itens ocultados hoje, reexibindo-os na lista MRP.
 */
window.resetarItensOcultosHoje = function () {
    const hojeStr = new Date().toISOString().split('T')[0];
    localStorage.removeItem(`mrp_ocultos_${hojeStr}`);
    carregarMRPVisual();
};


// ==========================================
// EXPORTAÇÃO E E-MAIL
// ==========================================

/**
 * Envia o relatório de compras e validades por e-mail, respeitando os itens
 * que o usuário ocultou hoje no navegador.
 */
async function dispararEmailAutomatico() {
    if (!confirm('Isto irá enviar o relatório de compras e validades para o e-mail configurado no servidor. Continuar?')) return;

    const btn = document.querySelector('button[onclick="dispararEmailAutomatico()"]');
    const textoAntigo = btn.innerText;
    btn.innerText = '⏳ Enviando...';
    btn.style.opacity = '0.7';

    try {
        const hojeStr = new Date().toISOString().split('T')[0];
        const ignoradosHoje = JSON.parse(localStorage.getItem(`mrp_ocultos_${hojeStr}`)) || [];

        const resp = await fetch(`${BASE_URL}/enviar_email_compras`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ignorados: ignoradosHoje })
        });
        const dados = await resp.json();

        if (!resp.ok) {
            alert('❌ Erro: ' + (dados.detail || 'Falha no envio.'));
            return;
        }

        if (dados.status === 'vazio') {
            alert('✅ Tudo OK! Não há itens críticos. E-mail não enviado para evitar spam.');
        } else {
            alert('📧 E-mail enviado! Verifique sua caixa de entrada (e a pasta de SPAM).');
        }
    } catch (e) {
        alert('🔌 Falha de conexão com o servidor.');
    } finally {
        btn.innerText = textoAntigo;
        btn.style.opacity = '1';
    }
}

/**
 * Exporta a lista MRP atual como arquivo CSV diretamente no navegador,
 * sem requisição ao servidor — usando a memória da última consulta.
 */
function baixarExcelMRP() {
    if (ultimaListaMRP.length === 0) {
        alert('A lista de compras está vazia ou não foi atualizada.');
        return;
    }

    let csv = 'Insumo;Burn Rate (Unid./Dia);Lead Time (Dias);Estoque Atual;Comprar (30 Dias)\n';
    ultimaListaMRP.forEach(i => {
        const volatilStr = i.volatil ? ' (Sazonal)' : '';
        csv += `${i.nome}${volatilStr};${String(i.burn_rate).replace('.', ',')};${i.lead_time};${String(i.estoque).replace('.', ',')} ${i.unidade};${String(i.comprar).replace('.', ',')} ${i.unidade}\n`;
    });

    const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'mrp_motor_estoque.csv';
    link.click();
}


// ==========================================
// NAVEGAÇÃO ENTRE ABAS
// ==========================================

/**
 * Alterna a aba ativa no painel de gestão e dispara o carregamento de dados
 * específico de cada seção.
 *
 * @param {string} id - ID da div da aba a ser ativada.
 * @param {HTMLElement} btn - Botão de navegação clicado (para marcar como ativo).
 */
function mudarAba(id, btn) {
    document.querySelectorAll('.aba').forEach(a => a.classList.remove('aba-ativa'));
    document.querySelectorAll('.nav-item').forEach(m => m.classList.remove('nav-active'));
    document.getElementById(id).classList.add('aba-ativa');
    btn.classList.add('nav-active');

    // Carrega dados sob demanda — evita requisições desnecessárias na inicialização.
    if (id === 'aba-estoque') carregarInventarioMassa();
    if (id === 'aba-nfe') carregarHistoricoEntradas();
    if (id === 'aba-mrp') carregarMRPVisual();
    if (id === 'aba-transferencias') carregarTransferencias();
}


// ==========================================
// TEMA CLARO / ESCURO
// ==========================================

/**
 * Alterna entre os temas claro e escuro, persistindo a preferência no localStorage.
 */
function toggleTheme() {
    const body = document.body;
    if (body.getAttribute('data-theme') === 'dark') {
        body.removeAttribute('data-theme');
        localStorage.setItem('theme', 'light');
    } else {
        body.setAttribute('data-theme', 'dark');
        localStorage.setItem('theme', 'dark');
    }
}

// Restaura o tema salvo na inicialização da página.
window.addEventListener('DOMContentLoaded', () => {
    if (localStorage.getItem('theme') === 'dark') {
        document.body.setAttribute('data-theme', 'dark');
    }
});


// ==========================================
// HISTÓRICO DE TRANSFERÊNCIAS
// ==========================================

/**
 * Carrega e renderiza o histórico de transferências entre os 3 níveis de estoque.
 *
 * Cada linha mostra: ingrediente, quantidade, nível de origem → destino e data.
 */
async function carregarTransferencias() {
    const tb = document.getElementById('tabelaTransferencias');
    if (!tb) return;
    tb.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:20px;">⏳ Carregando...</td></tr>';

    try {
        const resp = await fetch(`${BASE_URL}/transferencias`);
        const dados = await resp.json();

        if (dados.length === 0) {
            tb.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:20px; color: var(--text-muted);">Nenhuma transferência registrada ainda.</td></tr>';
            return;
        }

        const labelNivel = {
            almoxarifado: '<span class="nivel-badge nivel-almoxarifado">📦 Almoxarifado</span>',
            cozinha: '<span class="nivel-badge nivel-cozinha">👨‍🍳 Cozinha</span>',
            vitrine: '<span class="nivel-badge nivel-vitrine">🏪 Vitrine</span>',
        };

        tb.innerHTML = dados.map(t => `
            <tr>
                <td style="padding:12px;">${t.data_hora}</td>
                <td style="padding:12px;"><b>${t.ingrediente || '—'}</b></td>
                <td style="padding:12px;">${t.qtd_origem.toFixed(2)}</td>
                <td style="padding:12px;">${labelNivel[t.origem] || t.origem}</td>
                <td style="padding:12px;">${labelNivel[t.destino] || t.destino}</td>
                <td style="padding:12px; color: var(--text-muted); font-size: 12px;">${t.observacao || '—'}</td>
            </tr>
        `).join('');
    } catch (e) {
        tb.innerHTML = '<tr><td colspan="6" style="text-align:center; color: var(--danger); padding:20px;">Erro ao carregar transferências.</td></tr>';
    }
}
