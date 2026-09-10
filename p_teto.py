# -*- coding: utf-8 -*-
import ast, io, os
A = "casar_por_endereco.py"
t = io.open(A, encoding="utf-8").read()
if "rv.TETO_M" in t:
    print("ja aplicado"); raise SystemExit(0)

V = '''        print("      acima de 1 km: %d de %d vínculos"
              % (sum(1 for d in distancias if d > 1000), len(distancias)))'''
if V not in t: raise SystemExit("nao achei o resumo de distancia")
N = '''        print("      acima de 1 km: %d de %d vínculos"
              % (sum(1 for d in distancias if d > 1000), len(distancias)))
        # QUANTOS SOBREVIVEM AO TETO. Desde 10/09/2026 a regra recusa o par
        # acima de `rv.TETO_M`, INCLUSIVE com rua e numero batendo — e este
        # modulo existe justamente para o POI cuja coordenada esta errada e
        # por isso esta longe. Sem esta linha, o casamento parece um sucesso
        # de 16 mil vinculos e a revisao seguinte apaga a maior parte deles
        # sem ninguem entender por que.
        dentro = sum(1 for d in distancias if d <= rv.TETO_M)
        print("\n   ATENCAO — a regra atual recusa acima de %d m:"
              % round(rv.TETO_M))
        print("      passam no teto ............ %6d" % dentro)
        print("      caem na proxima revisao ... %6d" % (len(distancias) - dentro))
        print("      (a menos que a coordenada do POI seja corrigida para a "
              "ligacao — e' o que faz `--mover`)")'''
t = t.replace(V, N, 1)
with io.open(A + ".novo", "w", encoding="utf-8", newline="\n") as f:
    f.write(t)
ast.parse(io.open(A + ".novo", encoding="utf-8").read())
os.replace(A + ".novo", A)
print("casar_por_endereco.py: conta quantos passam no teto")
