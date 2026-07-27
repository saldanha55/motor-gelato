// ======================= IMPORTAÇÃO DE NF-E (XML) ========================
let dadosNfeTemp = [];

async function lerXmlNfe(event) {
    const file = event.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = async function (e) {
        try {
            // 1. Limpeza brutal do XML: Remove prefixos (ex: nfe:) e namespaces
            let text = e.target.result;
            text = text.replace(/xmlns(:\w+)?="[^"]*"/g, '');
            text = text.replace(/<\/?\w+:/g, match => match.includes('</') ? '</' : '<');

            const parser = new DOMParser();
            const xmlDoc = parser.parseFromString(text, "text/xml");

            // Verifica se o parser do navegador encontrou um erro fatal no arquivo
            if (xmlDoc.getElementsByTagName("parsererror").length > 0) {
                throw new Error("O ficheiro XML está mal formatado ou corrompido.");
            }

            // 2. Tenta capturar a Chave da NF-e com tolerância a falhas
            let chaveNfe = "CHAVE_DESCONHECIDA";
            const nodeCh = xmlDoc.getElementsByTagName("chNFe")[0];
            if (nodeCh) {
                chaveNfe = nodeCh.textContent;
            } else {
                const nodeInf = xmlDoc.getElementsByTagName("infNFe")[0];
                if (nodeInf && nodeInf.getAttribute("Id")) {
                    chaveNfe = nodeInf.getAttribute("Id").replace("NFe", "");
                }
            }
            window.currentChaveNfe = chaveNfe;

            // 3. Valida se existem produtos (tag 'det')
            const dets = xmlDoc.getElementsByTagName("det");
            if (dets.length === 0) {
                throw new Error("Nenhum produto encontrado. Tem a certeza que é um XML de NF-e válido?");
            }

            // 4. Busca dados do servidor
            const urlServidor = typeof BASE_URL !== 'undefined' ? BASE_URL : '';
            const resp = await fetch(`${urlServidor}/ingredientes`);
            if (!resp.ok) throw new Error("Falha ao comunicar com o servidor (Insumos).");
            const insumos = await resp.json();
            insumos.sort((a, b) => a.nome.localeCompare(b.nome));

            let mapeamentos = {};
            try {
                const respMap = await fetch(`${urlServidor}/mapeamentos_nfe`);
                if (respMap.ok) mapeamentos = await respMap.json();
            } catch (err) {
                console.warn("Mapeamentos automáticos desligados.");
            }

            // 5. Monta a Tabela
            let htmlTabela = "";
            dadosNfeTemp = [];
            const mapaInsumos = {};
            insumos.forEach(ins => { mapaInsumos[ins.id] = ins; });

            for (let i = 0; i < dets.length; i++) {
                const prod = dets[i].getElementsByTagName("prod")[0];
                const xProd = prod.getElementsByTagName("xProd")[0]?.textContent || "Produto Desconhecido";
                const qCom = parseFloat(prod.getElementsByTagName("qCom")[0]?.textContent || 0);
                const vUnCom = parseFloat(prod.getElementsByTagName("vUnCom")[0]?.textContent || 0);

                dadosNfeTemp.push({ xProd, qCom, vUnCom });

                const mapItem = mapeamentos[xProd];
                const defaultIngId = mapItem ? mapItem.ingrediente_id : "";
                const defaultFator = mapItem ? mapItem.fator_conversao : "1";

                let options = `<option value="">-- Ignorar Produto --</option>`;
                insumos.forEach(ins => {
                    const selected = (ins.id == defaultIngId) ? "selected" : "";
                    options += `<option value="${ins.id}" ${selected}>${ins.nome}</option>`;
                });

                let bgFator = mapItem ? "#dcfce7" : "var(--bg-body)";
                let borderFator = mapItem ? "#bbf7d0" : "var(--border)";
                let corFonte = mapItem ? "#1e293b" : "var(--dark)";

                htmlTabela += `<tr>
                    <td style="font-size: 11px; max-width: 200px;"><b>${xProd}</b><br><span style="color:var(--gray); font-size:10px;">${mapItem ? '✔️ Vinculado Auto' : 'Novo Produto'}</span></td>
                    <td><span class="badge badge-box">${qCom}</span></td>
                    <td><select class="select-map-nfe" data-idx="${i}" onchange="sugerirFatorES(this, ${i})" style="width: 180px; font-size: 11px;">${options}</select></td>
                    <td><input type="number" id="nfe-preco-${i}" value="${vUnCom.toFixed(2)}" step="0.01" style="width: 80px; padding: 5px; font-size: 12px;"></td>
                    <td><input type="number" id="nfe-fator-${i}" value="${defaultFator}" step="0.01" style="width: 70px; padding: 5px; font-size: 12px; background: ${bgFator}; border: 1px solid ${borderFator}; color: ${corFonte};"></td>
                </tr>`;
            }

            window.currentInsumosMap = mapaInsumos;
            document.getElementById('tabelaItensNfe').innerHTML = htmlTabela;
            document.getElementById('modalNfe').style.display = 'flex';

        } catch (erro) {
            console.error("ERRO XML DETALHADO:", erro);
            // Agora o alerta vai dizer EXATAMENTE o que falhou!
            alert("Aviso do Sistema: " + erro.message);
        } finally {
            // Limpa o input para permitir importar a mesma nota novamente se necessário
            document.getElementById('inputXmlNfe').value = "";
        }
    };
    reader.readAsText(file);
}

function fecharModalNfe() { document.getElementById('modalNfe').style.display = 'none'; }

async function salvarEntradaNfe() {
    const selects = document.querySelectorAll('.select-map-nfe');
    const itensParaSalvar = [];

    selects.forEach(select => {
        const idInsumo = select.value;
        if (idInsumo !== "") {
            const idx = select.dataset.idx;
            itensParaSalvar.push({
                xprod: dadosNfeTemp[idx].xProd,
                ingrediente_id: parseInt(idInsumo),
                qtd_comprada: dadosNfeTemp[idx].qCom,
                preco_unitario: parseFloat(document.getElementById(`nfe-preco-${idx}`).value),
                fator_conversao: parseFloat(document.getElementById(`nfe-fator-${idx}`).value)
            });
        }
    });

    if (itensParaSalvar.length === 0) return alert("Vincule pelo menos um produto!");

    const btn = document.querySelector('#modalNfe .btn-add');
    const txtOriginal = btn.innerHTML; btn.innerHTML = "⏳ Processando..."; btn.style.opacity = "0.7";

    try {
        const resp = await fetch(`${BASE_URL}/entrada_nfe`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chave_acesso: window.currentChaveNfe, itens: itensParaSalvar })
        });
        if (!resp.ok) return alert("Erro ao importar a nota. Verifique campos em branco.");
        alert("Notas processadas e estoque atualizado!");
        fecharModalNfe(); carregarInventarioMassa();
    } catch (e) { alert("Erro ao processar NF-e."); } finally { btn.innerHTML = txtOriginal; btn.style.opacity = "1"; }
}

function sugerirFatorES(select, idx) {
    const inputFator = document.getElementById(`nfe-fator-${idx}`);
    if (select.value && window.currentInsumosMap[select.value]) {
        inputFator.value = window.currentInsumosMap[select.value].fator_conversao;
        inputFator.style.background = "#fefce8"; inputFator.style.borderColor = "#fde047";
        inputFator.style.color = "#1e293b";
    } else {
        inputFator.value = "1";
        inputFator.style.background = "var(--bg-body)";
        inputFator.style.borderColor = "var(--border)";
        inputFator.style.color = "var(--dark)";
    }
}

async function carregarHistoricoEntradas() {
    try {
        const resp = await fetch(`${BASE_URL}/historico_entradas`);
        const dados = await resp.json();
        const tb = document.getElementById('tabelaHistoricoEntradas');
        tb.innerHTML = "";
        dados.forEach(h => {
            tb.innerHTML += `<tr><td>${h.data}</td><td><b style="color: var(--dark);">${h.nome}</b><br><span style="color: var(--gray); font-size: 11px;">+${h.qtd} ${h.unidade}</span></td><td><span class="badge" style="background:#e0e7ff; color:#4338ca;">${h.tipo}</span></td></tr>`;
        });
    } catch (e) { console.error(e); }
}

// ======================= CONTAGEM MOBILE ========================
let listaContagem = [];
let contagemIndex = 0;

async function iniciarContagemMobile() {
    // FUNÇÃO DE RETOMADA: Se já houver contagem salva na memória, pergunta se quer continuar
    if (listaContagem.length > 0 && contagemIndex < listaContagem.length) {
        if (confirm("Você tem uma contagem pausada. Deseja continuar de onde parou?")) {
            document.getElementById('modalContagemMobile').style.display = 'flex';
            renderizarTelaContagem();
            return;
        }
    }

    try {
        const resp = await fetch(`${BASE_URL}/ingredientes`);
        let ingredientes = await resp.json();
        listaContagem = ingredientes.filter(i => !i.escondido).map(i => ({
            id: i.id, nome: i.nome, unidade: i.unidade_almoxarifado,
            estoque_real: i.estoque_almoxarifado, validade: i.validade || ""
        }));
        if (listaContagem.length === 0) return alert("Não há insumos ativos para contar.");
        contagemIndex = 0;
        document.getElementById('modalContagemMobile').style.display = 'flex';
        renderizarTelaContagem();
    } catch (e) { alert("Erro ao iniciar contagem."); }
}

function renderizarTelaContagem() {
    if (contagemIndex >= listaContagem.length) return mostrarResumoContagem();
    const item = listaContagem[contagemIndex];
    document.getElementById('nomeInsumoMobile').innerText = item.nome;
    document.getElementById('unidadeInsumoMobile').innerText = `Medida: ${item.unidade}`;
    document.getElementById('inputContagemMobile').value = item.estoque_real;
    document.getElementById('inputValidadeMobile').value = item.validade;
    document.getElementById('contadorProgresso').innerText = `${contagemIndex + 1} / ${listaContagem.length}`;

    const btnProximo = document.getElementById('btnProximaContagem');
    if (contagemIndex === listaContagem.length - 1) {
        btnProximo.innerText = "👀 Revisar"; btnProximo.style.background = "#10b981";
    } else {
        btnProximo.innerText = "Próximo ➡️"; btnProximo.style.background = "#38bdf8";
    }
    setTimeout(() => document.getElementById('inputContagemMobile').focus(), 100);
}

function pausarContagem() {
    // Salva o valor atual digitado antes de pausar
    listaContagem[contagemIndex].estoque_real = parseFloat(document.getElementById('inputContagemMobile').value) || 0;
    listaContagem[contagemIndex].validade = document.getElementById('inputValidadeMobile').value;
    document.getElementById('modalContagemMobile').style.display = 'none';
}

function contagemProxima() {
    listaContagem[contagemIndex].estoque_real = parseFloat(document.getElementById('inputContagemMobile').value) || 0;
    listaContagem[contagemIndex].validade = document.getElementById('inputValidadeMobile').value;
    contagemIndex++;
    renderizarTelaContagem();
}

function contagemAnterior() {
    if (contagemIndex > 0) {
        listaContagem[contagemIndex].estoque_real = parseFloat(document.getElementById('inputContagemMobile').value) || 0;
        listaContagem[contagemIndex].validade = document.getElementById('inputValidadeMobile').value;
        contagemIndex--;
        renderizarTelaContagem();
    }
}

// NOVA FUNÇÃO: Tela visual antes de gerar Excel
function mostrarResumoContagem() {
    document.getElementById('modalContagemMobile').style.display = 'none';
    const ul = document.getElementById('listaResumo');
    ul.innerHTML = "";
    listaContagem.forEach(i => {
        let cor = i.estoque_real == 0 ? "color: var(--danger);" : "color: var(--dark);";
        ul.innerHTML += `<li style="padding: 15px 0; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between;">
            <b style="font-size: 14px;">${i.nome}</b>
            <span style="font-size: 16px; font-weight: bold; ${cor}">${i.estoque_real} ${i.unidade}</span>
        </li>`;
    });
    document.getElementById('modalResumoContagem').style.display = 'flex';
}

async function enviarContagemAoServidor() {
    const btn = document.querySelector('#modalResumoContagem .btn-add');
    btn.innerHTML = "Salvando..."; btn.style.opacity = "0.7";
    try {
        await fetch(`${BASE_URL}/salvar_inventario`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ itens: listaContagem })
        });
        
        try {
            await fetch(`${BASE_URL}/api/recalcular_burn_rates`, { method: 'POST' });
            console.log("🧠 Inteligência de Burn Rate recalculada com sucesso!");
        } catch (err) {
            console.warn("Não foi possível recalcular o burn rate automaticamente:", err);
        }
        // -------------------------------------------------------------

        document.getElementById('modalResumoContagem').style.display = 'none';
        listaContagem = []; 
        carregarInventarioMassa();

        document.querySelector('.nav-item[onclick*="aba-mrp"]').click();
        carregarMRPVisual();
    } catch (e) { alert("Erro ao salvar contagem."); } finally { btn.innerHTML = "💾 Confirmar e Salvar"; btn.style.opacity = "1"; }
}

// ======================= CRUD ESTOQUE CENTRAL ========================
async function carregarInventarioMassa() {
    try {
        const resp = await fetch(`${BASE_URL}/ingredientes`);
        const dados = await resp.json();
        const tb = document.getElementById('tabelaConfigAlertas');
        if (!tb) return;
        tb.innerHTML = "";

        // Lê se a caixinha de mostrar ocultos está marcada
        const mostrarOcultos = document.getElementById('checkMostrarOcultos')?.checked;
        const mesAtual = new Date().toISOString().substring(0, 7);

        dados.forEach(i => {
            // Se o item está escondido e a caixinha NÃO está marcada, salta o item
            if (i.escondido && !mostrarOcultos) return;

            let badgeValidade = `<span style="color: var(--text-muted);">Não informada</span>`;
            if (i.validade) {
                if (i.validade < mesAtual) badgeValidade = `<span style="color: #dc2626; font-weight: bold;">⚠️ VENCIDO (${i.validade})</span>`;
                else if (i.validade === mesAtual) badgeValidade = `<span style="color: #ea580c; font-weight: bold;">🚨 Vence este mês (${i.validade})</span>`;
                else badgeValidade = `<span style="color: #10b981;">OK (${i.validade})</span>`;
            }

            // Design Inteligente: Se estiver escondido, a linha fica meio transparente (cinzenta)
            let estiloLinha = i.escondido ? 'opacity: 0.5; background: rgba(0,0,0,0.05);' : '';

            // O botão muda de cor e texto dependendo do estado
            let btnOcultar = i.escondido
                ? `<button class="btn-acao" style="background: var(--primary); color: white; border: none;" onclick="alternarVisibilidadeInsumo(${i.id}, true)">👁️ Mostrar</button>`
                : `<button class="btn-acao" style="background: var(--text-muted); color: white; border: none;" onclick="alternarVisibilidadeInsumo(${i.id}, false)">👁️ Esconder</button>`;

            tb.innerHTML += `<tr style="${estiloLinha}"> 
                <td><b style="color: var(--text-main);">${i.nome} ${i.escondido ? '(OCULTO)' : ''}</b></td> 
                <td><span class="badge badge-box" style="background: rgba(32, 178, 170, 0.1); color: var(--primary); border: 1px solid var(--primary); font-size:14px;">${i.estoque_almoxarifado} ${i.unidade_almoxarifado}</span></td> 
                <td>${badgeValidade}</td>
                <td><b style="color: #ef4444;">${i.limite}</b> ${i.unidade_almoxarifado}</td> 
                <td> 
                    <div class="acoes-tabela"> 
                        <button class="btn-acao btn-edit" style="background: transparent; border: 1px solid var(--primary); color: var(--primary);" onclick="abrirModalEdicao(${i.id}, '${i.nome}', ${i.limite}, ${i.lead_time}, ${i.burn_rate}, ${i.volatil})">✏️ Editar</button> 
                        <button class="btn-acao" style="background: transparent; border: 1px solid var(--success); color: var(--success);" onclick="registrarEntradaManual(${i.id}, '${i.nome.replace(/'/g, "\\'")}')">➕ Entrada</button>
                        ${btnOcultar}
                    </div> 
                </td> 
            </tr>`;
        });
    } catch (e) { console.error(e); }
}

async function registrarEntradaManual(id, nome) {
    const qtdStr = prompt(`Quantas unidades/caixas de ${nome} chegaram sem NF?`);
    if (!qtdStr) return;
    const qtd = parseFloat(qtdStr.replace(',', '.'));
    if (isNaN(qtd) || qtd <= 0) return alert("Quantidade inválida.");
    
    const motivo = prompt("Onde foi comprado e por quem? (Ex: Assaí - Moacyr)");
    if (!motivo) return;
    
    try {
        await fetch(`${BASE_URL}/ingredientes/${id}/entrada_manual`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ quantidade: qtd, motivo: motivo })
        });
        alert("✅ Entrada avulsa registrada e saldo atualizado no estoque!");
        carregarInventarioMassa();
    } catch(e) {
        alert("Erro ao registrar entrada manual.");
    }
}

async function alternarVisibilidadeInsumo(id, estaEscondido) {
    // Muda a pergunta de acordo com o que a gerente quer fazer
    const acao = estaEscondido ? "Tornar este insumo visível novamente?" : "Ocultar este insumo temporariamente?";

    if (confirm(acao)) {
        try {
            await fetch(`${BASE_URL}/ingredientes/${id}/toggle_ocultar`, { method: 'PUT' });
            carregarInventarioMassa();
        } catch (e) { alert("Erro ao alterar o estado do insumo."); }
    }
}

function abrirModalEdicao(id, nome, limite, lead_time, burn_rate, volatil) {
    document.getElementById('editIdInsumo').value = id;
    document.getElementById('editNomeInsumo').value = nome;
    document.getElementById('editLimiteInsumo').value = limite;
    document.getElementById('editLeadTime').value = lead_time || 7;
    document.getElementById('editBurnRate').value = burn_rate || 0.5;
    document.getElementById('editVolatil').checked = volatil;

    document.getElementById('modalEdicaoInsumo').style.display = 'flex';
}

async function salvarEdicaoInsumo() {
    const id = document.getElementById('editIdInsumo').value;
    const limite = parseFloat(document.getElementById('editLimiteInsumo').value) || 0;
    const lead_time = parseInt(document.getElementById('editLeadTime').value) || 7;
    const burn_rate = parseFloat(document.getElementById('editBurnRate').value) || 0.5;
    const volatil = document.getElementById('editVolatil').checked;

    try {
        await fetch(`${BASE_URL}/ingredientes/${id}/parametros`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                novo_limite: limite,
                novo_lead_time: lead_time,
                novo_burn_rate: burn_rate,
                novo_volatil: volatil
            })
        });
        document.getElementById('modalEdicaoInsumo').style.display = 'none';
        carregarInventarioMassa();
        alert("Parâmetros inteligentes atualizados com sucesso!");
    } catch (e) {
        alert("Erro ao salvar os novos parâmetros.");
    }
}

async function salvarIngredienteUnificado() {
    const payload = {
        nome: document.getElementById('ingNome').value,
        preco_unitario: parseFloat(document.getElementById('ingPreco').value || 0),
        unidade_almoxarifado: document.getElementById('ingUnidadeAlmox').value,
        estoque_almoxarifado: parseFloat(document.getElementById('ingEstoqueAlmox').value || 0),
        limite_alerta: parseFloat(document.getElementById('ingLimite').value || 2),
        fator_conversao: 1,
        unidade: document.getElementById('ingUnidadeAlmox').value,
        estoque_atual: 0
    };
    if (!payload.nome) return alert("Preencha o nome do insumo.");
    try {
        await fetch(`${BASE_URL}/ingredientes`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        alert("✅ Insumo cadastrado!");
        document.getElementById('ingNome').value = ""; document.getElementById('ingPreco').value = ""; document.getElementById('ingEstoqueAlmox').value = "";
        document.querySelector('.nav-item[onclick*="aba-estoque"]').click();
    } catch (e) { alert("Erro ao salvar o insumo."); }
}