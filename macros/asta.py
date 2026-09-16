#!/usr/bin/env python3
"""
macros/asta.py
==============
Macro per la gestione dell'asta fantacalcio.

Modalità
--------
  libera   Ogni squadra sceglie il giocatore da mettere all'asta.
           L'ordine di chiamata è random (estratto ogni turno) oppure
           fisso (scelto dall'utente all'inizio).

  random   Il sistema estrae casualmente il prossimo giocatore tra
           quelli ancora liberi.

  importa  Importa i risultati da un file CSV. Formato atteso:
             giocatore_id,id_squadra,prezzo
           oppure con i nomi:
             giocatore,squadra,prezzo

Uso
---
  python macros/asta.py --db data/lega.db --lega 1
  python macros/asta.py --db data/lega.db --lega 1 --mode random
  python macros/asta.py --db data/lega.db --lega 1 --mode importa --file asta.csv
  python macros/asta.py --db data/lega.db --lega 1 --mode libera --ordine fisso
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fantacalciomanager.database import Database
from fantacalciomanager.mercato import Mercato, MercatoError
from fantacalciomanager.models import Ruolo


# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------

COMANDI_CERCA = ("cerca", "c")
COMANDI_BUDGET = ("budget", "b")
COMANDI_ROSA = ("rosa", "r")
COMANDI_SKIP = ("skip", "s", "passa", "p")
COMANDI_FINE = ("fine", "q", "esci", "exit")
COMANDI_HELP = ("help", "h", "?", "aiuto")


# ---------------------------------------------------------------------------
# Helpers I/O
# ---------------------------------------------------------------------------

def _sep(char: str = "─", n: int = 64) -> None:
    print(char * n)


def _header(titolo: str, char: str = "─") -> None:
    _sep(char)
    print(f"  {titolo}")
    _sep(char)


def _input_int(prompt: str, default: int | None = None,
               min_val: int | None = None) -> int | None:
    while True:
        hint = f" [{default}]" if default is not None else ""
        raw = input(f"{prompt}{hint}: ").strip()
        if not raw:
            return default
        try:
            v = int(raw)
            if min_val is not None and v < min_val:
                print(f"  ✗ Valore minimo: {min_val}")
                continue
            return v
        except ValueError:
            print("  ✗ Inserisci un numero intero.")


def _abbr_ruolo(ruolo_int: int) -> str:
    try:
        return Ruolo(ruolo_int).abbreviazione()
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# Visualizzazione
# ---------------------------------------------------------------------------

def mostra_budget(db: Database, id_lega: int) -> None:
    squadre = db.lista_fantasquadre(id_lega)
    print()
    print("  Budget corrente:")
    for fsq in squadre:
        barra = "█" * (fsq.crediti_residui // 20)
        print(f"    [{fsq.id:2d}] {fsq.nome:<22s}  {fsq.crediti_residui:4d} cr  {barra}")
    print()


def mostra_rosa(db: Database, id_fsq: int) -> None:
    rosa = db.rosa_fantasquadra(id_fsq)
    fsq = db.get_fantasquadra(id_fsq)
    nome = fsq.nome if fsq else f"id={id_fsq}"
    print(f"\n  Rosa di {nome}:")
    if not rosa:
        print("    (vuota)")
        return
    for r in sorted(rosa, key=lambda x: x["ruolo"]):
        print(f"    {_abbr_ruolo(r['ruolo'])}  {r['nome']:<25s} "
              f"{r['squadra']:<15s}  Q.{r['prezzo_acquisto']}")
    print()


def _riga_giocatore(g) -> str:
    """Riga di una riga giocatore da cerca_giocatori."""
    return (f"[{g.id:5d}] {_abbr_ruolo(g.ruolo)} "
            f"{g.nome:<26s} {g.squadra:<15s} Q.{g.costo_iniziale}")


# ---------------------------------------------------------------------------
# Ricerca giocatore
# ---------------------------------------------------------------------------

def scegli_giocatore(db: Database, id_lega: int,
                     prompt: str = "  Cerca giocatore (nome/id): ") -> Optional[object]:
    """
    Interattivo: cerca per nome o id.
    Ritorna il giocatore selezionato o None se annullato.
    """
    while True:
        raw = input(prompt).strip()
        if not raw or raw.lower() in COMANDI_FINE:
            return None

        # Ricerca per ID
        if raw.isdigit():
            g = db.get_giocatore(int(raw))
            if g:
                return g
            print("  ✗ Giocatore non trovato.")
            continue

        # Ricerca per nome
        risultati = db.cerca_giocatori(nome=raw)
        liberi = [g for g in risultati
                  if not _in_rosa(db, id_lega, g.id)]
        if not liberi:
            print("  ✗ Nessun giocatore libero trovato.")
            continue
        if len(liberi) == 1:
            return liberi[0]

        print(f"  {len(liberi)} risultati:")
        for g in liberi[:15]:
            print(f"    {_riga_giocatore(g)}")
        id_g = _input_int("  Inserisci l'id del giocatore (invio=annulla)", default=None)
        if id_g is None:
            continue
        g = db.get_giocatore(id_g)
        if g:
            return g
        print("  ✗ Id non valido.")


def _in_rosa(db: Database, id_lega: int, id_giocatore: int) -> bool:
    """True se il giocatore è già in rosa in questa lega."""
    conn = db.connetti()
    row = conn.execute(
        """SELECT 1 FROM rose r
           JOIN fantasquadre f ON f.id = r.id_fantasquadra
           WHERE f.id_lega=? AND r.id_giocatore=? AND r.stato=0""",
        (id_lega, id_giocatore),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Motore d'asta per un singolo giocatore
# ---------------------------------------------------------------------------

def esegui_asta_giocatore(
    db: Database,
    mercato: Mercato,
    id_lega: int,
    anno: int,
    id_giocatore: int,
    id_squadra_chiamante: int | None = None,
) -> bool:
    """
    Gestisce l'asta per un singolo giocatore.

    Flusso:
      - annuncia il giocatore
      - ciclo di rilanci aperti: le squadre dichiarano il loro massimo
        o passano
      - aggiudica al miglior offerente

    Ritorna True se il giocatore è stato aggiudicato, False se saltato.
    """
    giocatore = db.get_giocatore(id_giocatore)
    if not giocatore:
        print(f"  ✗ Giocatore id={id_giocatore} non trovato.")
        return False

    squadre = db.lista_fantasquadre(id_lega)

    _sep()
    print(f"  ⚽  {_abbr_ruolo(giocatore.ruolo)}  {giocatore.nome}"
          f"  ({giocatore.squadra})  — Q.Base: {giocatore.costo_iniziale}")
    mostra_budget(db, id_lega)

    # Stato asta
    offerta_corrente: int = 0
    id_vincitore: int | None = None
    nome_vincitore: str = ""

    # Ciclo rilanci: continuiamo finché qualcuno rilancia
    passaggi_consecutivi = 0
    num_squadre = len(squadre)

    print("  Rilanci aperti — digita il tuo importo o 'passa'.")
    print("  (skip/s per saltare il giocatore, fine/q per terminare l'asta)\n")

    idx = 0   # squadra corrente nel ciclo
    while passaggi_consecutivi < num_squadre:
        fsq = squadre[idx % num_squadre]
        idx += 1

        # Salta squadre senza crediti sufficienti per rilanciare
        minimo = offerta_corrente + 1 if offerta_corrente > 0 else 1
        if fsq.crediti_residui < minimo:
            passaggi_consecutivi += 1
            continue

        # Mostra stato corrente
        if offerta_corrente > 0:
            stato = f"  Offerta: {offerta_corrente} cr ({nome_vincitore})"
        else:
            stato = "  Nessuna offerta ancora."
        prompt = (f"  [{fsq.nome}] ({fsq.crediti_residui} cr disponibili)"
                  f"  min={minimo} — offerta (invio=passa): ")

        raw = input(prompt).strip().lower()

        if raw in COMANDI_FINE:
            print("\n  Asta interrotta.")
            return False

        if raw in COMANDI_SKIP or raw == "":
            passaggi_consecutivi += 1
            continue

        if raw in COMANDI_BUDGET:
            mostra_budget(db, id_lega)
            idx -= 1   # riproponi la stessa squadra
            passaggi_consecutivi = 0
            continue

        try:
            importo = int(raw)
        except ValueError:
            print("  ✗ Inserisci un numero o 'passa'.")
            idx -= 1
            continue

        if importo < minimo:
            print(f"  ✗ Offerta minima: {minimo} cr.")
            idx -= 1
            continue

        if importo > fsq.crediti_residui:
            print(f"  ✗ Crediti insufficienti ({fsq.crediti_residui} disponibili).")
            idx -= 1
            continue

        # Rilancio valido
        offerta_corrente = importo
        id_vincitore = fsq.id
        nome_vincitore = fsq.nome
        passaggi_consecutivi = 0
        print(f"  ✓ {fsq.nome} offre {importo} cr.")

        # Ricarica crediti aggiornati
        squadre = db.lista_fantasquadre(id_lega)

    # Fine asta per questo giocatore
    if id_vincitore is None:
        print(f"\n  — {giocatore.nome} non aggiudicato (nessuna offerta).")
        return False

    # In modalità libera: se nessuno ha rilanciato tranne la squadra
    # chiamante, può aggiudicarsi al prezzo base
    try:
        mercato.acquista(id_vincitore, id_giocatore, offerta_corrente)
        print(f"\n  🏆  {giocatore.nome} → {nome_vincitore} "
              f"a {offerta_corrente} cr")
    except MercatoError as exc:
        print(f"\n  ✗ Errore registrazione: {exc}")
        return False

    return True


# ---------------------------------------------------------------------------
# Modalità LIBERA
# ---------------------------------------------------------------------------

def modalita_libera(db: Database, mercato: Mercato,
                    id_lega: int, anno: int,
                    ordine_random: bool) -> None:
    _header("Modalità LIBERA" + (" — ordine casuale" if ordine_random
                                  else " — ordine fisso"))
    squadre = db.lista_fantasquadre(id_lega)
    if not squadre:
        print("  Nessuna fantasquadra trovata.")
        return

    # Ordine di chiamata
    if ordine_random:
        print("  L'ordine di chiamata sarà estratto casualmente ogni turno.\n")
        turni: list = []   # ricostruiamo ogni volta
    else:
        print("  Definisci l'ordine di chiamata:")
        for i, fsq in enumerate(squadre, 1):
            print(f"    {i}. {fsq.nome}")
        raw = input("  Ordine (es. 3,1,2,4) [invio=ordine attuale]: ").strip()
        if raw:
            try:
                indici = [int(x) - 1 for x in raw.split(",")]
                squadre = [squadre[i] for i in indici if 0 <= i < len(squadre)]
            except (ValueError, IndexError):
                print("  ✗ Ordine non valido, uso quello attuale.")
        turni = list(squadre)

    print("\n  Comandi extra durante la ricerca: budget | rosa <id> | skip | fine\n")

    turno = 0
    while True:
        # Squadra chiamante
        if ordine_random:
            sq_list = db.lista_fantasquadre(id_lega)
            sq_list = [s for s in sq_list if s.crediti_residui > 0]
            if not sq_list:
                print("  Tutte le squadre hanno esaurito i crediti.")
                break
            chiamante = random.choice(sq_list)
        else:
            chiamante = turni[turno % len(turni)]
            turno += 1

        _sep("═")
        print(f"  Turno di chiamata: {chiamante.nome} ({chiamante.crediti_residui} cr)")
        print(f"  (cerca <nome> | id diretto | skip | budget | fine)\n")

        raw = input("  > ").strip().lower()

        if raw in COMANDI_FINE:
            break
        if raw in COMANDI_SKIP:
            continue
        if raw in COMANDI_BUDGET:
            mostra_budget(db, id_lega)
            if not ordine_random:
                turno -= 1
            continue
        if raw.startswith("rosa"):
            parts = raw.split()
            if len(parts) > 1 and parts[1].isdigit():
                mostra_rosa(db, int(parts[1]))
            else:
                mostra_rosa(db, chiamante.id)
            if not ordine_random:
                turno -= 1
            continue

        # Ricerca giocatore: stringa libera o id
        if raw.isdigit():
            giocatore = db.get_giocatore(int(raw))
        elif raw.startswith("cerca "):
            q = raw[6:].strip()
            risultati = [g for g in db.cerca_giocatori(nome=q)
                         if not _in_rosa(db, id_lega, g.id)]
            if not risultati:
                print("  ✗ Nessun risultato.")
                if not ordine_random:
                    turno -= 1
                continue
            if len(risultati) == 1:
                giocatore = risultati[0]
            else:
                print(f"  {len(risultati)} risultati:")
                for g in risultati[:15]:
                    print(f"    {_riga_giocatore(g)}")
                id_g = _input_int("  Id giocatore (invio=torna indietro)", default=None)
                if id_g is None:
                    if not ordine_random:
                        turno -= 1
                    continue
                giocatore = db.get_giocatore(id_g)
        else:
            risultati = [g for g in db.cerca_giocatori(nome=raw)
                         if not _in_rosa(db, id_lega, g.id)]
            if not risultati:
                print("  ✗ Nessun risultato.")
                if not ordine_random:
                    turno -= 1
                continue
            if len(risultati) == 1:
                giocatore = risultati[0]
            else:
                print(f"  {len(risultati)} risultati:")
                for g in risultati[:15]:
                    print(f"    {_riga_giocatore(g)}")
                id_g = _input_int("  Id giocatore (invio=torna indietro)", default=None)
                if id_g is None:
                    if not ordine_random:
                        turno -= 1
                    continue
                giocatore = db.get_giocatore(id_g)

        if not giocatore:
            print("  ✗ Giocatore non trovato.")
            if not ordine_random:
                turno -= 1
            continue

        if _in_rosa(db, id_lega, giocatore.id):
            print("  ✗ Giocatore già in rosa.")
            if not ordine_random:
                turno -= 1
            continue

        esegui_asta_giocatore(db, mercato, id_lega, anno,
                               giocatore.id, chiamante.id)


# ---------------------------------------------------------------------------
# Modalità RANDOM
# ---------------------------------------------------------------------------

def modalita_random(db: Database, mercato: Mercato,
                    id_lega: int, anno: int) -> None:
    _header("Modalità RANDOM — estrazione casuale dei giocatori")

    # Filtra per ruolo opzionalmente
    print("  Filtra per ruolo: P=Portiere D=Difensore C=Centrocampista A=Attaccante")
    raw = input("  Ruoli da includere (invio=tutti): ").strip().upper()
    filtro_ruoli: list[int] | None = None
    if raw:
        mappa = {"P": Ruolo.PORTIERE.value, "D": Ruolo.DIFENSORE.value,
                 "C": Ruolo.CENTROCAMPISTA.value, "A": Ruolo.ATTACCANTE.value}
        filtro_ruoli = [mappa[c] for c in raw if c in mappa]

    # Pool giocatori liberi
    def pool_liberi() -> list:
        conn = db.connetti()
        query = """
            SELECT g.id FROM giocatori g
            WHERE NOT EXISTS (
                SELECT 1 FROM rose r
                JOIN fantasquadre f ON f.id = r.id_fantasquadra
                WHERE f.id_lega=? AND r.id_giocatore=g.id AND r.stato=0
            )
        """
        args: list = [id_lega]
        if filtro_ruoli:
            placeholders = ",".join("?" * len(filtro_ruoli))
            query += f" AND g.ruolo IN ({placeholders})"
            args += filtro_ruoli
        return [row[0] for row in conn.execute(query, args).fetchall()]

    print("  Premi Invio per estrarre il prossimo giocatore, 'fine' per terminare.\n")

    while True:
        liberi = pool_liberi()
        if not liberi:
            print("  Tutti i giocatori sono stati assegnati.")
            break

        raw = input(f"  [{len(liberi)} giocatori liberi] Invio=estrai | budget | fine: ").strip().lower()
        if raw in COMANDI_FINE:
            break
        if raw in COMANDI_BUDGET:
            mostra_budget(db, id_lega)
            continue

        id_estratto = random.choice(liberi)
        esegui_asta_giocatore(db, mercato, id_lega, anno, id_estratto)


# ---------------------------------------------------------------------------
# Modalità IMPORTA da file CSV
# ---------------------------------------------------------------------------

def modalita_importa(db: Database, mercato: Mercato,
                     id_lega: int, anno: int, file_csv: Path) -> None:
    _header(f"Modalità IMPORTA — {file_csv.name}")

    if not file_csv.exists():
        print(f"  ✗ File non trovato: {file_csv}")
        return

    squadre = {fsq.nome.lower(): fsq.id for fsq in db.lista_fantasquadre(id_lega)}
    squadre_id = {fsq.id: fsq for fsq in db.lista_fantasquadre(id_lega)}

    ok = err = 0

    with open(file_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        righe = list(reader)

    print(f"  {len(righe)} righe trovate nel file.\n")

    for i, riga in enumerate(righe, 1):
        # Risolvi giocatore
        if "giocatore_id" in riga:
            id_g = int(riga["giocatore_id"].strip())
            giocatore = db.get_giocatore(id_g)
        elif "giocatore" in riga:
            nome_g = riga["giocatore"].strip()
            risultati = db.cerca_giocatori(nome=nome_g)
            giocatore = risultati[0] if len(risultati) == 1 else None
            if not giocatore:
                print(f"  [{i}] ✗ Giocatore '{nome_g}' non trovato o ambiguo.")
                err += 1
                continue
        else:
            print(f"  [{i}] ✗ Colonna 'giocatore_id' o 'giocatore' mancante.")
            err += 1
            continue

        if not giocatore:
            print(f"  [{i}] ✗ Giocatore non trovato.")
            err += 1
            continue

        # Risolvi squadra
        if "id_squadra" in riga:
            id_sq = int(riga["id_squadra"].strip())
        elif "squadra" in riga:
            nome_sq = riga["squadra"].strip().lower()
            id_sq = squadre.get(nome_sq)
            if not id_sq:
                # Ricerca parziale
                matches = [sid for nome, sid in squadre.items()
                           if nome_sq in nome]
                if len(matches) == 1:
                    id_sq = matches[0]
                else:
                    print(f"  [{i}] ✗ Squadra '{riga['squadra']}' non trovata.")
                    err += 1
                    continue
        else:
            print(f"  [{i}] ✗ Colonna 'id_squadra' o 'squadra' mancante.")
            err += 1
            continue

        # Prezzo
        try:
            prezzo = int(riga.get("prezzo", "1").strip())
        except ValueError:
            prezzo = 1

        fsq = squadre_id.get(id_sq)
        nome_sq = fsq.nome if fsq else f"id={id_sq}"

        try:
            mercato.acquista(id_sq, giocatore.id, prezzo)
            print(f"  [{i}] ✓ {giocatore.nome} → {nome_sq} ({prezzo} cr)")
            ok += 1
        except MercatoError as exc:
            print(f"  [{i}] ✗ {giocatore.nome}: {exc}")
            err += 1

    _sep()
    print(f"  Importazione completata: {ok} ok, {err} errori.")
    mostra_budget(db, id_lega)


# ---------------------------------------------------------------------------
# Riepilogo finale
# ---------------------------------------------------------------------------

def riepilogo_finale(db: Database, id_lega: int) -> None:
    _header("Riepilogo finale asta", "═")
    squadre = db.lista_fantasquadre(id_lega)
    for fsq in squadre:
        rosa = db.rosa_fantasquadra(fsq.id)
        speso = sum(r["prezzo_acquisto"] for r in rosa)
        print(f"\n  {fsq.nome} — crediti residui: {fsq.crediti_residui} "
              f"(spesi: {speso})")
        for r in sorted(rosa, key=lambda x: x["ruolo"]):
            print(f"    {_abbr_ruolo(r['ruolo'])}  "
                  f"{r['nome']:<25s} {r['squadra']:<15s} Q.{r['prezzo_acquisto']}")
    _sep("═")


# ---------------------------------------------------------------------------
# Selezione lega
# ---------------------------------------------------------------------------

def scegli_lega(db: Database, id_lega_arg: int | None) -> tuple[int, int]:
    if id_lega_arg:
        lega = db.get_lega(id_lega_arg)
        if not lega:
            print(f"✗ Lega id={id_lega_arg} non trovata.", file=sys.stderr)
            sys.exit(1)
        return lega.id, lega.anno

    leghe = db.lista_leghe()
    if not leghe:
        print("✗ Nessuna lega trovata.", file=sys.stderr)
        sys.exit(1)
    if len(leghe) == 1:
        return leghe[0].id, leghe[0].anno

    print("\nLeghe disponibili:")
    for l in leghe:
        print(f"  [{l.id}] {l.nome} {l.anno}/{str(l.anno + 1)[-2:]}")
    while True:
        raw = input("Seleziona id lega: ").strip()
        try:
            id_lega = int(raw)
            lega = next((l for l in leghe if l.id == id_lega), None)
            if lega:
                return lega.id, lega.anno
        except ValueError:
            pass
        print("  ✗ Id non valido.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Gestione asta FantaCalcio (libera / random / importa).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Esempi:
  # Asta libera con ordine casuale di chiamata
  python macros/asta.py --db data/lega.db --lega 1 --mode libera

  # Asta libera con ordine fisso
  python macros/asta.py --db data/lega.db --lega 1 --mode libera --ordine fisso

  # Estrazione random dei giocatori
  python macros/asta.py --db data/lega.db --lega 1 --mode random

  # Importa risultati da CSV
  python macros/asta.py --db data/lega.db --lega 1 --mode importa --file asta.csv

Formato CSV importa:
  giocatore_id,id_squadra,prezzo
  1234,1,45
  5678,2,30

  oppure con i nomi:
  giocatore,squadra,prezzo
  Barella,Dream Team,35
  Donnarumma,Galacticos,50
        """,
    )
    p.add_argument("--db",     required=True,  help="Percorso database SQLite")
    p.add_argument("--lega",   type=int,        help="Id lega (default: menu)")
    p.add_argument("--mode",   default="libera",
                   choices=["libera", "random", "importa"],
                   help="Modalità asta (default: libera)")
    p.add_argument("--ordine", default="random",
                   choices=["random", "fisso"],
                   help="Ordine di chiamata in modalità libera (default: random)")
    p.add_argument("--file",   help="File CSV per modalità importa")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    db = Database(args.db)
    id_lega, anno = scegli_lega(db, args.lega)
    mercato = Mercato(db, id_lega=id_lega, anno=anno)

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║           FantaCalcio Manager — Gestione Asta           ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(db.info_lega(id_lega))

    if args.mode == "libera":
        modalita_libera(db, mercato, id_lega, anno,
                        ordine_random=(args.ordine == "random"))

    elif args.mode == "random":
        modalita_random(db, mercato, id_lega, anno)

    elif args.mode == "importa":
        if not args.file:
            print("✗ Specifica il file con --file", file=sys.stderr)
            sys.exit(1)
        modalita_importa(db, mercato, id_lega, anno, Path(args.file))

    riepilogo_finale(db, id_lega)


if __name__ == "__main__":
    main()
