/**
 * config.js — Motor Gelato
 * ==========================
 *
 * Configuração global compartilhada entre todos os módulos JavaScript.
 *
 * Design decision — URL dinâmica:
 *   Usamos window.location.hostname em vez de 'localhost' para que o sistema
 *   funcione quando acessado de outros dispositivos na mesma rede (ex: tablet
 *   na cozinha acessando o servidor no computador do escritório).
 */

/** URL base da API FastAPI, detectada dinamicamente pelo hostname atual. */
const IP_SERVIDOR = window.location.hostname;
const BASE_URL = `http://${IP_SERVIDOR}:8050`;

// Inicialização global de componentes de navegação
document.addEventListener('DOMContentLoaded', function () {

    // Fecha dropdowns de navegação ao clicar fora deles
    document.addEventListener('click', function (e) {
        document.querySelectorAll('details.nav-group').forEach(detalhe => {
            if (!detalhe.contains(e.target)) {
                detalhe.removeAttribute('open');
            }
        });
    });

    // Fecha dropdown automaticamente ao selecionar um item interno
    document.querySelectorAll('.nav-group-content .nav-item').forEach(item => {
        item.addEventListener('click', function () {
            this.closest('details').removeAttribute('open');
        });
    });
});
