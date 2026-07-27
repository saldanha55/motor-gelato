let receitasData = [], listaTemporaria = [], receitaEditandoId = null;

// ======================= RECEITAS ========================
async function carregarDropdownInsumos() {
    try {
        const resp = await fetch(`${BASE_URL}/ingredientes`); 
        const d = await resp.json();
        const sel = document.getElementById('recSelectIngrediente'); 
        sel.innerHTML = "<option value=''>Selecione o insumo...</option>";
        
        d.forEach(i => { 
            // Injetamos o preço no atributo dataset para o JS ler depois
            sel.innerHTML += `<option value="${i.id}" data-preco="${i.preco}">${i.nome} (${i.unidade})</option>`; 
        });
    } catch (e) { console.error("Erro dropdown:", e); }
}

function addIngredienteLinha() {
    const sel = document.getElementById('recSelectIngrediente');
    const qtd = document.getElementById('recQtdIngrediente').value;
    
    if (!sel.value || !qtd) return;
    
    const opt = sel.options[sel.selectedIndex];
    const text = opt.text.split('(')[0].trim(); // Pega só o nome
    const preco = parseFloat(opt.dataset.preco) || 0; // Captura o preço
    
    listaTemporaria.push({ ingrediente_id: parseInt(sel.value), quantidade: parseFloat(qtd), nome: text, preco: preco });
    
    renderLista(); 
    document.getElementById('recQtdIngrediente').value = "";
}

function renderLista() {
    const ul = document.getElementById('listaIngredientesReceita'); 
    ul.innerHTML = "";
    
    listaTemporaria.forEach((x, i) => {
        // Regra do alerta de preço
        let alertaPreco = (x.preco === 0 || x.preco === 1) 
            ? `<span style="color:#dc2626; font-size:10px; background:#fee2e2; padding:2px 4px; border-radius:4px; margin-left:8px;" title="Custo fixado em R$ ${x.preco}">⚠️ Insumo Sem Preço</span>` 
            : "";
            
        ul.innerHTML += `<li style="padding: 8px 0; display:flex; justify-content:space-between; align-items: center; border-bottom:1px solid #e2e8f0;"> 
            <span>✔️ ${x.quantidade}x <b>${x.nome}</b> ${alertaPreco}</span>  
            <button onclick="listaTemporaria.splice(${i},1);renderLista()" class="btn-acao btn-del" style="padding: 4px 8px; font-size: 11px;">❌ Remover</button> 
        </li>`;
    });
}

async function carregarReceitas() {
    try {
        const resp = await fetch(`${BASE_URL}/receitas`);
        receitasData = await resp.json();
        renderizarReceitas();
        document.getElementById('buscaReceita').value = "";
    } catch (e) {
        console.error("Erro ao carregar receitas:", e);
    }
}

function renderizarReceitas() {
    const tb = document.getElementById('tabelaReceitasDetalhada'); 
    tb.innerHTML = "";
    
    receitasData.forEach(r => {
        let badgeCusto = (r.custo > 0 && !r.tem_preco_zerado)
            ? `<span class="badge badge-money">R$ ${r.custo.toFixed(2)}</span>`
            : `<span class="badge" style="background:#fee2e2; color:#dc2626; border: 1px solid #fecaca;" title="Ficha vazia ou com insumos a R$ 0 ou R$ 1">⚠️ R$ ${r.custo.toFixed(2)} (INCOMPLETO)</span>`;

        tb.innerHTML += `<tr>
            <td><b style="color: var(--dark);">${r.nome}</b></td>
            <td>${badgeCusto}</td>
            <td><span class="badge badge-kg">${r.rendimento}</span></td>
            <td>
                <div class="acoes-tabela">
                    <button class="btn-acao btn-edit" onclick="editarRec(${r.id})">✏️ Editar</button>
                    <button class="btn-acao btn-del" onclick="deletarReceita(${r.id})">❌</button>
                </div>
            </td>
        </tr>`;
    });
}

function ordenarReceitas(campo) {
    if (campo === 'custo') receitasData.sort((a, b) => b.custo - a.custo);
    else if (campo === 'rendimento') receitasData.sort((a, b) => b.rendimento - a.rendimento);
    else receitasData.sort((a, b) => a.nome.localeCompare(b.nome));
    
    renderizarReceitas();
}

async function editarRec(id) {
    try {
        const resp = await fetch(`${BASE_URL}/receitas/${id}`); 
        const d = await resp.json();
        
        receitaEditandoId = id; 
        document.getElementById('recNome').value = d.nome_gelato; 
        document.getElementById('recRendimento').value = d.rendimento_cubas;
        
        listaTemporaria = d.itens; 
        renderLista();
        
        document.getElementById('tituloPainelReceita').innerText = "✏️ Editando Ficha"; 
        document.getElementById('btnSalvarReceita').innerText = "Atualizar Ficha Técnica"; 
        document.getElementById('btnCancelarEdicao').style.display = "block";
        document.getElementById('painelReceita').scrollIntoView({ behavior: 'smooth' });
    } catch (e) {
        console.error("Erro ao carregar detalhes da receita:", e);
    }
}

function cancelarEdicao() {
    receitaEditandoId = null; 
    document.getElementById('recNome').value = ""; 
    listaTemporaria = []; 
    renderLista();
    
    document.getElementById('tituloPainelReceita').innerText = "➕ Montar Ficha Técnica (Gelato ou Sobremesa)"; 
    document.getElementById('btnSalvarReceita').innerText = "💾 Salvar Ficha Técnica"; 
    document.getElementById('btnCancelarEdicao').style.display = "none";
}

async function salvarReceita() {
    const payload = { 
        nome_gelato: document.getElementById('recNome').value, 
        rendimento_cubas: parseInt(document.getElementById('recRendimento').value), 
        itens: listaTemporaria 
    };
    
    if (!payload.nome_gelato || listaTemporaria.length === 0) return alert("Preencha o nome e os ingredientes.");
    
    const m = receitaEditandoId ? 'PUT' : 'POST'; 
    const url = receitaEditandoId ? `${BASE_URL}/receitas/${receitaEditandoId}` : `${BASE_URL}/receitas`;
    
    try {
        await fetch(url, { 
            method: m, 
            headers: { 'Content-Type': 'application/json' }, 
            body: JSON.stringify(payload) 
        });
        cancelarEdicao(); 
        carregarReceitas();
    } catch (e) {
        console.error("Erro ao salvar receita:", e);
        alert("Erro ao salvar a ficha técnica.");
    }
}

async function deletarReceita(id) {
    if (confirm("Tem certeza que deseja apagar esta receita definitivamente?")) {
        try {
            await fetch(`${BASE_URL}/receitas/${id}`, { method: 'DELETE' });
            carregarReceitas();
        } catch (e) {
            console.error("Erro ao deletar receita:", e);
        }
    }
}

// ======================= CONFIG INSUMOS ========================
async function carregarIngredientesConfig() {
    try {
        const resp = await fetch(`${BASE_URL}/ingredientes`); 
        const dados = await resp.json();
        const tb = document.getElementById('tabelaIngredientesConfig'); 
        tb.innerHTML = "";
        
        dados.forEach(i => {
            let badgePreco = (i.preco > 0 && i.preco !== 1) 
                ? `<span class="badge badge-money">R$ ${i.preco.toFixed(2)}</span>` 
                : `<span class="badge" style="background:#fee2e2; color:#dc2626; border: 1px solid #fecaca;" title="Cadastre o preço para cálculos corretos">⚠️ SEM PREÇO</span>`;
            
            tb.innerHTML += `<tr> 
                <td><b style="color: var(--dark);">${i.nome}</b><br><span style="font-size:11px; color:var(--gray);">1 ${i.unidade_almoxarifado} = ${i.fator_conversao} ${i.unidade}</span></td> 
                <td>${badgePreco}</td> 
                <td><b style="color: var(--danger);">${i.limite}</b> ${i.unidade}</td> 
                <td> 
                    <div class="acoes-tabela"> 
                        <button class="btn-acao btn-edit" onclick="ajusteRapido(${i.id}, '${i.nome.replace(/'/g, "\\'")}', ${i.preco}, 'preco', '')">💲 Preço</button> 
                        <button class="btn-acao btn-edit" onclick="ajusteRapido(${i.id}, '${i.nome.replace(/'/g, "\\'")}', ${i.limite}, 'limite', '')">🔔 Alerta</button> 
                        <button class="btn-acao btn-edit" onclick="ajusteRapido(${i.id}, '${i.nome.replace(/'/g, "\\'")}', ${i.fator_conversao}, 'fator', '')">🔄 Fator</button> 
                        <button class="btn-acao btn-del" onclick="deletarIngrediente(${i.id})">❌ Excluir</button> 
                    </div> 
                </td> 
            </tr>`;
        });
        document.getElementById('buscaBancoInsumos').value = "";
    } catch (e) {
        console.error("Erro ao carregar configurações de ingredientes:", e);
    }
}

async function ajusteRapido(id, nome, valorAtual, tipo, unidade = "") {
    let texto = "";
    if (tipo === 'almoxarifado') texto = `Novo saldo de ${unidade} do almoxarifado`;
    else if (tipo === 'preco') texto = 'Novo custo unitário';
    else if (tipo === 'limite') texto = 'Novo gatilho de alerta de stock';
    else if (tipo === 'fator') texto = 'Novo Fator de Conversão (Rendimento na Cozinha)';

    // Substituição do prompt() nativo pelo modal customizado!
    let val = await pedirValorModal(
        `✏️ Ajuste: ${nome}`,
        `${texto}:\n(Valor atual: ${valorAtual})`
    );
    
    // Usamos !== null porque se o utilizador clicar em Cancelar, o modal retorna null
    if (val !== null) {
        let payload = {};
        // Troca vírgula por ponto para evitar erros matemáticos
        let valorTratado = parseFloat(val.replace(',', '.'));

        if (isNaN(valorTratado)) {
            return alert("Por favor, digite um número válido.");
        }

        if (tipo === 'almoxarifado') payload = { novo_estoque: valorTratado };
        if (tipo === 'preco') payload = { novo_preco: valorTratado };
        if (tipo === 'limite') payload = { novo_limite: valorTratado };
        if (tipo === 'fator') payload = { novo_fator: valorTratado };

        try {
            const resp = await fetch(`${BASE_URL}/ingredientes/${id}/${tipo}`, { 
                method: 'PUT', 
                headers: { 'Content-Type': 'application/json' }, 
                body: JSON.stringify(payload) 
            });
            
            if(!resp.ok) {
                alert("Erro ao atualizar a informação.");
                return;
            }

            // Atualiza a tabela correta dependendo de onde o botão foi clicado
            if (tipo === 'almoxarifado') carregarAlmoxarifado();
            else carregarIngredientesConfig();

        } catch (e) {
            console.error(`Erro ao atualizar ${tipo}:`, e);
            alert("Falha de conexão ao salvar.");
        }
    }
}

async function salvarIngrediente() {
    const payload = {
        nome: document.getElementById('ingNome').value,
        preco_unitario: parseFloat(document.getElementById('ingPreco').value || 0),
        unidade_almoxarifado: document.getElementById('ingUnidadeAlmox').value,
        estoque_almoxarifado: parseFloat(document.getElementById('ingEstoqueAlmox').value || 0),
        fator_conversao: parseFloat(document.getElementById('ingFator').value || 1),
        unidade: document.getElementById('ingUnidade').value,
        estoque_atual: parseFloat(document.getElementById('ingEstoque').value || 0),
        limite_alerta: parseFloat(document.getElementById('ingLimite').value || 5)
    };
    
    if (!payload.nome) return alert("Preencha o nome do insumo.");
    
    try {
        await fetch(`${BASE_URL}/ingredientes`, { 
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' }, 
            body: JSON.stringify(payload) 
        });
        
        document.getElementById('ingNome').value = ""; 
        document.getElementById('ingPreco').value = ""; 
        document.getElementById('ingEstoqueAlmox').value = "";
        document.getElementById('ingFator').value = "1"; 
        document.getElementById('ingEstoque').value = "0"; 
        document.getElementById('ingLimite').value = "5";
        
        carregarIngredientesConfig();
    } catch (e) {
        console.error("Erro ao salvar ingrediente:", e);
        alert("Erro ao salvar o insumo.");
    }
}

async function deletarIngrediente(id) {
    if (confirm("CUIDADO EXTREMO: Apagar este insumo vai removê-lo de TODAS as Fichas Técnicas que o utilizam. Esta ação não pode ser desfeita. Continuar?")) {
        try {
            await fetch(`${BASE_URL}/ingredientes/${id}`, { method: 'DELETE' });
            carregarIngredientesConfig();
        } catch (e) {
            console.error("Erro ao deletar ingrediente:", e);
        }
    }
}