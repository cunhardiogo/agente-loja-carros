/**
 * Realtime da planilha de agendamentos -> Agente Loja SB.
 * A cada edição da planilha, avisa o agente pra sincronizar na hora.
 *
 * Como instalar (uma vez):
 * 1. Na planilha: menu Extensões > Apps Script
 * 2. Apague o conteúdo e cole este arquivo. Salve (ícone de disquete).
 * 3. No menu da esquerda, ícone de relógio (Acionadores/Triggers) > "Adicionar acionador":
 *      - Função: syncAgenda
 *      - Origem do evento: Da planilha
 *      - Tipo de evento: Ao editar
 *    Salvar e autorizar (login Google + permitir).
 * Pronto: toda edição dispara a sincronização em segundos.
 */
function syncAgenda() {
  var url = "https://agente-loja-carros.onrender.com/cron/sync-planilha?token=sb_0c055ed071841f14c9ba5a522974001a";
  UrlFetchApp.fetch(url, { muteHttpExceptions: true });
}
