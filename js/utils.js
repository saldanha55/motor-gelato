// ======================= FUNÇÕES GERAIS DE BUSCA E ORDENAÇÃO ========================
function filtrarTabelaDOM(inputId, tbodyId) {
    const inputPesquisa = document.getElementById(inputId).value.toLowerCase();
    const linhas = document.getElementById(tbodyId).getElementsByTagName('tr');

    for (let i = 0; i < linhas.length; i++) {
        const colunas = linhas[i].getElementsByTagName('td');
        let achouTextoNaLinha = false;
        for (let j = 0; j < colunas.length - 1; j++) {
            if (colunas[j].innerText.toLowerCase().includes(inputPesquisa)) {
                achouTextoNaLinha = true; break;
            }
        }
        linhas[i].style.display = achouTextoNaLinha ? "" : "none";
    }
}

// Quando a página inicia, ele carrega a tabela unificada (já que o dashboard velho morreu)
window.onload = function() {
    if (typeof carregarInventarioMassa === "function") {
        carregarInventarioMassa();
    }
};