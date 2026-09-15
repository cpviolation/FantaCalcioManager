"""
Fantacalcio Manager - Interfaccia a linea di comando
Uso:
    python -m fcm [COMANDO] [OPZIONI]

Comandi disponibili:
    init              Crea un nuovo database
    lega              Gestione leghe
    squadra           Gestione fantasquadre
    voti              Importa voti giornata
    formazione        Gestione formazioni
    risultati         Calcola risultati giornata
    classifica        Visualizza classifica di lega
    statistiche       Query statistiche (cannonieri, fantamedia, ...)
    mercato           Operazioni di mercato
    info              Info sul database corrente
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import Database, QueryStatistiche, ClassificaLega, Ruolo
from .importa import ImportaVoti
from .mercato import Mercato, MercatoError

# ---------------------------------------------------------------------------
# Helpers di output
# ---------------------------------------------------------------------------

def _tabella(righe: list[dict], campi: list[str],
             intestazioni: list[str]) -> None:
    """Stampa una tabella ASCII semplice."""
    # larghezze colonne
    w = [len(h) for h in intestazioni]
    for r in righe:
        for i, c in enumerate(campi):
            w[i] = max(w[i], len(str(r.get(c, ""))))

    sep = "+" + "+".join("-" * (x + 2) for x in w) + "+"
    fmt = "|" + "|".join(f" {{:<{x}}} " for x in w) + "|"

    print(sep)
    print(fmt.format(*intestazioni))
    print(sep)
    for r in righe:
        vals = [str(r.get(c, "")) for c in campi]
        print(fmt.format(*vals))
    print(sep)
    print(f"  {len(righe)} record")


def _db(args) -> Database:
    path = Path(args.db) if args.db else None
    return Database(path)


# ---------------------------------------------------------------------------
# Comando: init
# ---------------------------------------------------------------------------

def cmd_init(args) -> None:
    db_path = Path(args.db) if args.db else \
        Path.home() / "Documenti" / "FantacalcioManager" / "fcm.db"
    db = Database(db_path)
    db.inizializza()
    print(f"✓ Database inizializzato: {db_path}")


# ---------------------------------------------------------------------------
# Comando: lega
# ---------------------------------------------------------------------------

def cmd_lega(args) -> None:
    db = _db(args)
    sub = args.sub_lega

    if sub == "nuova":
        id_lega = db.crea_lega(
            nome=args.nome,
            anno=args.anno,
            stagione=args.stagione or 1,
            crediti=args.crediti or 500,
            giornate=args.giornate or 38,
        )
        print(f"✓ Lega '{args.nome}' creata (id={id_lega})")

    elif sub == "lista":
        conn = db.connetti()
        rows = conn.execute("SELECT * FROM leghe ORDER BY anno DESC, nome").fetchall()
        if not rows:
            print("Nessuna lega trovata.")
        else:
            _tabella(
                [dict(r) for r in rows],
                ["id", "nome", "anno", "stagione", "crediti_iniziali"],
                ["ID", "Nome", "Anno", "Stagione", "Crediti"],
            )

    else:
        print("Uso: fcm lega {nuova|lista} ...")


# ---------------------------------------------------------------------------
# Comando: squadra
# ---------------------------------------------------------------------------

def cmd_squadra(args) -> None:
    db = _db(args)
    sub = args.sub_squadra

    if sub == "nuova":
        id_sq = db.crea_fantasquadra(
            id_lega=args.id_lega,
            nome=args.nome,
            presidente=args.presidente or "",
        )
        print(f"✓ Fantasquadra '{args.nome}' creata (id={id_sq})")

    elif sub == "lista":
        righe = db.lista_fantasquadre(args.id_lega)
        if not righe:
            print("Nessuna fantasquadra trovata.")
        else:
            _tabella(
                [{"id": f.id, "nome": f.nome, "presidente": f.presidente,
                  "crediti": f.crediti_residui} for f in righe],
                ["id", "nome", "presidente", "crediti"],
                ["ID", "Nome", "Presidente", "Crediti"],
            )

    elif sub == "rosa":
        righe = db.rosa_fantasquadra(args.id_squadra)
        if not righe:
            print("Rosa vuota.")
        else:
            _tabella(
                [{"id": g.id, "nome": g.nome, "squadra": g.squadra,
                  "ruolo": Ruolo(g.ruolo).abbreviazione()} for g in righe],
                ["id", "nome", "squadra", "ruolo"],
                ["ID", "Giocatore", "Squadra", "R"],
            )

    else:
        print("Uso: fcm squadra {nuova|lista|rosa} ...")


# ---------------------------------------------------------------------------
# Comando: voti
# ---------------------------------------------------------------------------

def cmd_voti(args) -> None:
    db = _db(args)
    imp = ImportaVoti(db, anno=args.anno, giornata=args.giornata,
                      id_lega=args.id_lega or 0)

    n = imp.da_csv_gazzetta(args.file)
    print(f"✓ Importati {n} voti — giornata {args.giornata} anno {args.anno}")
    if imp.errori:
        print(f"  ⚠ {len(imp.errori)} errori:")
        for e in imp.errori[:10]:
            print(f"    {e}")


# ---------------------------------------------------------------------------
# Comando: formazione
# ---------------------------------------------------------------------------

def cmd_formazione(args) -> None:
    db = _db(args)
    sub = args.sub_form

    if sub == "salva":
        titolari = [int(x) for x in args.titolari.split(",")]
        panchina = [int(x) for x in args.panchina.split(",")] \
            if args.panchina else []
        form = db.salva_formazione(
            id_fantasquadra=args.id_squadra,
            giornata=args.giornata,
            anno=args.anno,
            titolari=titolari,
            panchina=panchina,
            modulo=args.modulo or "4-3-3",
        )
        print(f"✓ Formazione salvata (id={form.id}, modulo={form.modulo})")

    elif sub == "mostra":
        form = db.get_formazione(args.id_squadra, args.giornata, args.anno)
        if not form:
            print("Formazione non trovata.")
        else:
            print(f"Modulo: {form.modulo}")
            print(f"Titolari: {form.titolari}")
            print(f"Panchina: {form.panchina}")

    else:
        print("Uso: fcm formazione {salva|mostra} ...")


# ---------------------------------------------------------------------------
# Comando: risultati
# ---------------------------------------------------------------------------

def cmd_risultati(args) -> None:
    from .calcoli import calcola_formazione, calcola_risultato_incontro

    db = _db(args)
    conn = db.connetti()

    incontri = db.incontri_giornata(args.id_lega, args.anno, args.giornata)
    if not incontri:
        print("Nessun incontro trovato.")
        return

    regole = db.get_regole(args.id_lega)

    for inc in incontri:
        sq_casa = conn.execute(
            "SELECT nome FROM fantasquadre WHERE id=?", (inc.id_casa,)
        ).fetchone()
        sq_fuori = conn.execute(
            "SELECT nome FROM fantasquadre WHERE id=?", (inc.id_fuori,)
        ).fetchone()
        nome_casa  = sq_casa["nome"]  if sq_casa  else str(inc.id_casa)
        nome_fuori = sq_fuori["nome"] if sq_fuori else str(inc.id_fuori)

        def punteggio_squadra(id_sq: int) -> float:
            form = db.get_formazione(id_sq, args.giornata, args.anno)
            if not form:
                return 0.0
            titolari = []
            for id_g in form.titolari:
                voti = db.voti_giornata(args.giornata, args.anno)
                voto = next((v for v in voti if v["id_giocatore"] == id_g), None)
                if voto:
                    titolari.append({
                        "id_giocatore": id_g,
                        "nome": voto.get("nome", str(id_g)),
                        "ruolo": voto.get("ruolo", 4),
                        "fantapunti": voto.get("fantapunti", 0),
                        "ha_giocato": bool(voto.get("ha_giocato")),
                    })
            panchina = []
            for id_g in form.panchina:
                voti = db.voti_giornata(args.giornata, args.anno)
                voto = next((v for v in voti if v["id_giocatore"] == id_g), None)
                if voto:
                    panchina.append({
                        "id_giocatore": id_g,
                        "nome": voto.get("nome", str(id_g)),
                        "ruolo": voto.get("ruolo", 4),
                        "fantapunti": voto.get("fantapunti", 0),
                        "ha_giocato": bool(voto.get("ha_giocato")),
                    })
            ris = calcola_formazione(titolari, panchina, regole=regole)
            return ris.punteggio_totale

        pt_casa  = punteggio_squadra(inc.id_casa)
        pt_fuori = punteggio_squadra(inc.id_fuori)
        gc, gf   = calcola_risultato_incontro(pt_casa, pt_fuori)

        db.salva_risultato(
            id_incontro=inc.id,
            punti_casa=pt_casa, punti_fuori=pt_fuori,
            gol_casa=gc, gol_fuori=gf,
        )
        print(f"{nome_casa:20s} {gc}-{gf}  {nome_fuori:20s}"
              f"  ({pt_casa:.2f} - {pt_fuori:.2f})")


# ---------------------------------------------------------------------------
# Comando: classifica
# ---------------------------------------------------------------------------

def cmd_classifica(args) -> None:
    db = _db(args)
    cls = ClassificaLega(db, id_lega=args.id_lega, anno=args.anno,
                         id_divisione=args.divisione or 0)
    righe = cls.calcola()

    _tabella(
        righe,
        ["pos", "nome", "pt", "v", "n", "p", "gf", "gs", "dr", "punteggio"],
        ["#", "Squadra", "Pt", "V", "N", "P", "GF", "GS", "DR", "Punteggio"],
    )


# ---------------------------------------------------------------------------
# Comando: statistiche
# ---------------------------------------------------------------------------

def cmd_statistiche(args) -> None:
    db = _db(args)
    qs = QueryStatistiche(
        db, anno=args.anno, id_lega=args.id_lega,
        giornata_da=args.da or 1, giornata_a=args.a or 38,
    )

    query = args.query
    ruolo = Ruolo[args.ruolo.upper()] if args.ruolo else None

    if query == "cannonieri":
        righe = qs.classifica_cannonieri(top=args.top or 0)
        _tabella(righe,
                 ["nome", "squadra", "gol_fatti", "presenze"],
                 ["Giocatore", "Squadra", "Gol", "Pres."])

    elif query == "fantamedia":
        righe = qs.migliori_fantamedia(ruolo=ruolo,
                                        liberi_da_contratto=args.liberi)
        _tabella(righe,
                 ["nome", "squadra", "fantamedia_totale", "affidabilita",
                  "presenze"],
                 ["Giocatore", "Squadra", "FM", "Aff%", "Pres."])

    elif query == "mediavoto":
        righe = qs.migliori_mediavoto(ruolo=ruolo,
                                       liberi_da_contratto=args.liberi)
        _tabella(righe,
                 ["nome", "squadra", "mediavoto_totale", "affidabilita"],
                 ["Giocatore", "Squadra", "MV", "Aff%"])

    elif query == "crescita":
        righe = qs.giocatori_in_crescita(ruolo=ruolo)
        _tabella(righe,
                 ["nome", "squadra", "mediavoto_andamento",
                  "mediavoto_totale"],
                 ["Giocatore", "Squadra", "Trend", "MV"])

    elif query == "fairplay":
        righe = qs.classifica_fairplay(top=args.top or 0)
        _tabella(righe,
                 ["nome", "squadra", "espulsioni", "ammonizioni"],
                 ["Giocatore", "Squadra", "Esp", "Amm"])

    elif query == "difese":
        righe = qs.difese_piu_forti()
        _tabella(righe,
                 ["nome", "squadra", "gol_subiti", "presenze"],
                 ["Portiere", "Squadra", "GolSub", "Pres."])

    elif query == "rendimento":
        righe = qs.migliore_rendimento()
        _tabella(righe,
                 ["nome", "squadra", "rendimento_fm", "fantamedia_totale",
                  "costo"],
                 ["Giocatore", "Squadra", "Rend.", "FM", "Costo"])

    elif query == "liberi":
        righe = qs.giocatori_liberi(ruolo=ruolo)
        _tabella(righe,
                 ["nome", "squadra", "fantamedia_totale", "mediavoto_totale"],
                 ["Giocatore", "Squadra", "FM", "MV"])

    else:
        print(f"Query sconosciuta: {query}")
        print("Query disponibili: cannonieri, fantamedia, mediavoto, "
              "crescita, fairplay, difese, rendimento, liberi")


# ---------------------------------------------------------------------------
# Comando: mercato
# ---------------------------------------------------------------------------

def cmd_mercato(args) -> None:
    db = _db(args)
    m = Mercato(db, id_lega=args.id_lega, anno=args.anno)
    sub = args.sub_mercato

    try:
        if sub == "acquista":
            op = m.acquista(args.id_squadra, args.id_giocatore, args.prezzo)
            print(f"✓ Acquisto completato — spesi {-op.crediti} crediti")

        elif sub == "svincola":
            op = m.svincola(args.id_squadra, args.id_giocatore,
                            args.crediti)
            print(f"✓ Svincolo completato — incassati {op.crediti} crediti")

        elif sub == "budget":
            info = m.riepilogo_budget(args.id_squadra)
            print(f"  Squadra:     {info['nome']}")
            print(f"  Crediti:     {info['crediti_residui']}")
            print(f"  Spesi:       {info['crediti_spesi']}")
            print(f"  Rosa:        {info['giocatori_in_rosa']}"
                  f"/{m.rosa_max}")

        else:
            print("Uso: fcm mercato {acquista|svincola|budget} ...")

    except MercatoError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Comando: info
# ---------------------------------------------------------------------------

def cmd_info(args) -> None:
    db = _db(args)
    conn = db.connetti()

    n_leghe     = conn.execute("SELECT COUNT(*) FROM leghe").fetchone()[0]
    n_squadre   = conn.execute("SELECT COUNT(*) FROM fantasquadre").fetchone()[0]
    n_giocatori = conn.execute("SELECT COUNT(*) FROM giocatori").fetchone()[0]
    n_voti      = conn.execute("SELECT COUNT(*) FROM voti_giornata").fetchone()[0]

    print(f"Database: {db.path}")
    print(f"  Leghe:       {n_leghe}")
    print(f"  Fantasquadre:{n_squadre}")
    print(f"  Giocatori:   {n_giocatori}")
    print(f"  Voti:        {n_voti}")


# ---------------------------------------------------------------------------
# Parser principale
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fcm",
        description="Fantacalcio Manager — rewrite Python di FCM 8.5.0",
    )
    p.add_argument("--db", metavar="PERCORSO",
                   help="percorso al file database .db")

    sub = p.add_subparsers(dest="comando", required=True)

    # ------------------------------------------------------------------
    # init
    sub.add_parser("init", help="Crea/inizializza il database")

    # ------------------------------------------------------------------
    # lega
    pl = sub.add_parser("lega", help="Gestione leghe")
    pl.add_argument("sub_lega", choices=["nuova", "lista"])
    pl.add_argument("--nome")
    pl.add_argument("--anno", type=int)
    pl.add_argument("--stagione", type=int)
    pl.add_argument("--crediti", type=int)
    pl.add_argument("--giornate", type=int)

    # ------------------------------------------------------------------
    # squadra
    ps = sub.add_parser("squadra", help="Gestione fantasquadre")
    ps.add_argument("sub_squadra", choices=["nuova", "lista", "rosa"])
    ps.add_argument("--id-lega", dest="id_lega", type=int, default=1)
    ps.add_argument("--id-squadra", dest="id_squadra", type=int)
    ps.add_argument("--nome")
    ps.add_argument("--presidente")

    # ------------------------------------------------------------------
    # voti
    pv = sub.add_parser("voti", help="Importa voti giornata")
    pv.add_argument("file", help="percorso CSV Gazzetta")
    pv.add_argument("--anno", type=int, required=True)
    pv.add_argument("--giornata", type=int, required=True)
    pv.add_argument("--id-lega", dest="id_lega", type=int, default=0)

    # ------------------------------------------------------------------
    # formazione
    pf = sub.add_parser("formazione", help="Gestione formazioni")
    pf.add_argument("sub_form", choices=["salva", "mostra"])
    pf.add_argument("--id-squadra", dest="id_squadra", type=int, required=True)
    pf.add_argument("--anno", type=int, required=True)
    pf.add_argument("--giornata", type=int, required=True)
    pf.add_argument("--titolari",
                    help="ID giocatori separati da virgola (11)")
    pf.add_argument("--panchina",
                    help="ID giocatori separati da virgola (fino a 7)")
    pf.add_argument("--modulo", default="4-3-3")

    # ------------------------------------------------------------------
    # risultati
    pr = sub.add_parser("risultati", help="Calcola risultati giornata")
    pr.add_argument("--id-lega", dest="id_lega", type=int, required=True)
    pr.add_argument("--anno", type=int, required=True)
    pr.add_argument("--giornata", type=int, required=True)

    # ------------------------------------------------------------------
    # classifica
    pc = sub.add_parser("classifica", help="Classifica di lega")
    pc.add_argument("--id-lega", dest="id_lega", type=int, required=True)
    pc.add_argument("--anno", type=int, required=True)
    pc.add_argument("--divisione", type=int, default=0)

    # ------------------------------------------------------------------
    # statistiche
    pst = sub.add_parser("statistiche", help="Query statistiche")
    pst.add_argument("query",
                     choices=["cannonieri", "fantamedia", "mediavoto",
                               "crescita", "fairplay", "difese",
                               "rendimento", "liberi"])
    pst.add_argument("--id-lega", dest="id_lega", type=int, required=True)
    pst.add_argument("--anno", type=int, required=True)
    pst.add_argument("--da", type=int, default=1)
    pst.add_argument("--a", type=int, default=38)
    pst.add_argument("--ruolo",
                     choices=["portiere", "difensore",
                               "centrocampista", "attaccante"])
    pst.add_argument("--top", type=int, default=0)
    pst.add_argument("--liberi", action="store_true",
                     help="Solo giocatori non tesserati")

    # ------------------------------------------------------------------
    # mercato
    pm = sub.add_parser("mercato", help="Operazioni di mercato")
    pm.add_argument("sub_mercato",
                    choices=["acquista", "svincola", "budget"])
    pm.add_argument("--id-lega", dest="id_lega", type=int, required=True)
    pm.add_argument("--anno", type=int, required=True)
    pm.add_argument("--id-squadra", dest="id_squadra", type=int)
    pm.add_argument("--id-giocatore", dest="id_giocatore", type=int)
    pm.add_argument("--prezzo", type=int)
    pm.add_argument("--crediti", type=int)

    # ------------------------------------------------------------------
    # info
    sub.add_parser("info", help="Info sul database")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "init":        cmd_init,
        "lega":        cmd_lega,
        "squadra":     cmd_squadra,
        "voti":        cmd_voti,
        "formazione":  cmd_formazione,
        "risultati":   cmd_risultati,
        "classifica":  cmd_classifica,
        "statistiche": cmd_statistiche,
        "mercato":     cmd_mercato,
        "info":        cmd_info,
    }
    dispatch[args.comando](args)


if __name__ == "__main__":
    main()
