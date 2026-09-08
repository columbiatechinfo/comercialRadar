-- 0079 · condominio residencial nao e estabelecimento.
--
-- Tres categorias do catalogo descreviam MORADIA, e estavam marcadas para
-- avaliar. Isso gerou o falso positivo achado na auditoria de 07/09/2026: o
-- POI 376599, "Condomínio Medianeira", recebeu `aprovado_exato` — a IA viu um
-- predio grande com portaria e leu como estabelecimento. E um predio de
-- apartamentos, sentado numa ligacao residencial, cobrado corretamente.
--
-- Nao adianta ensinar o prompt a recusar: a resposta e sempre a mesma, e
-- pagar uma chamada de modelo para chegar nela e desperdicio com risco. A
-- categoria ja diz o que o lugar e.
--
-- FICAM MARCADAS as que so PARECEM residenciais: "Móveis e Residencial Loja",
-- "Segurança Residencial", "Funerária Residencial", "Residencial Limpeza".
-- Essas sao negocios que ATENDEM residencias — comercio legitimo, e o
-- criterio e o que o estabelecimento faz, nao a palavra no nome.
update radar_comercial.categoria_catalogo
   set avaliar = false
 where (fonte = 'estadual' and valor = 'Apartamentos Or Condo')
    or (fonte = 'maps'     and valor = 'Complexo de condomínio')
    or (fonte = 'maps'     and valor = 'Complexo residencial');
