#!/usr/bin/env python3
"""
macros/gestisci_lega.py
=======================
Macro interattiva per visualizzare e modificare i parametri di una lega
e delle sue fantasquadre.

Uso
---
    python macros/gestisci_lega.py --db data/lega.db --lega 1
    python macros/gestisci_lega.py --db data/lega.db          # sceglie la lega da menu
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fantacalciomanager.database import Database


# ---------------------------------------------------------------------------
# Utilità I/O
# ---------------------------------------------------------------------------

def chiedi(prompt: str, default: str = "") -> str:
    """Input con valore di default mostrato tra parentesi."""
    hint = f" [{default}]" if default else ""
    risposta = input(f"{prompt}{hint}: ").strip()
    return risposta if risposta else default


def chiedi_int(prompt: str, default: int | None = None) -> int | None:
    """Input intero opzionale; None se lasciato vuoto e default=None."""
    hint = f" [{default}]" if default is not None else " (invio per saltare)"
    raw = input(f"{prompt}{hint}: ").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print("  ✗ Valore non valido, campo ignorato.")
        return default


def separatore() -> None:
    print("\n" + "─" * 56)


# ---------------------------------------------------------------------------
# Selezione lega
# ---------------------------------------------------------------------------

def scegli_lega(db: Database) -> int:
    leghe = db.lista_leghe()
    if not leghe:
        print("Nessuna lega trovata nel database.")
        sys.exit(1)
    if len(leghe) == 1:
        return leghe[0].id

    print("\nLeghe disponibili:")
    for l in leghe:
        print(f"  [{l.id}] {l.nome} {l.anno}/{str(l.anno + 1)[-2:]} — stagione {l.stagione}")
    while True:
        raw = input("Seleziona id lega: ").strip()
        try:
            id_lega = int(raw)
            if any(l.id == id_lega for l in leghe):
                return id_lega
        except ValueError:
            pass
        print("  ✗ Id non valido.")


# ---------------------------------------------------------------------------
# Menu modifiche lega
# ---------------------------------------------------------------------------

def modifica_lega(db: Database, id_lega: int) -> None:
    lega = db.get_lega(id_lega)
    if lega is None:
        print("Lega non trovata.")
        return

    print(f"\n── Modifica lega: {lega.nome} ──")
    print("  Lascia vuoto per mantenere il valore attuale.\n")

    nome     = chiedi("  Nuovo nome",               default=lega.nome)
    crediti  = chiedi_int("  Crediti iniziali",      default=lega.crediti_iniziali)
    giornate = chiedi_int("  Giornate totali",        default=lega.giornate_totali)
    stagione = chiedi_int("  Numero stagione",        default=lega.stagione)

    campi: dict = {}
    if nome != lega.nome:
        campi["nome"] = nome
    if crediti != lega.crediti_iniziali:
        campi["crediti"] = crediti
    if giornate != lega.giornate_totali:
        campi["giornate"] = giornate
    if stagione != lega.stagione:
        campi["stagione"] = stagione

    if not campi:
        print("  Nessuna modifica.")
        return

    db.aggiorna_lega(id_lega, **campi)
    print("  ✓ Lega aggiornata.")

    # Se i crediti iniziali sono cambiati, offri di allineare le squadre
    if "crediti" in campi:
        nuovi_crediti = campi["crediti"]
        risposta = input(
            f"\n  Vuoi impostare i crediti residui di tutte le fantasquadre "
            f"a {nuovi_crediti}? [s/N]: "
        ).strip().lower()
        if risposta == "s":
            squadre = db.lista_fantasquadre(id_lega)
            for fsq in squadre:
                db.imposta_crediti(fsq.id, nuovi_crediti)
            print(f"  ✓ Crediti di {len(squadre)} fantasquadre impostati a {nuovi_crediti}.")


# ---------------------------------------------------------------------------
# Menu modifiche fantasquadra
# ---------------------------------------------------------------------------

def modifica_fantasquadra(db: Database, id_lega: int) -> None:
    squadre = db.lista_fantasquadre(id_lega)
    if not squadre:
        print("Nessuna fantasquadra trovata.")
        return

    print("\nFantasquadre disponibili:")
    for fsq in squadre:
        print(f"  [{fsq.id:2d}]  {fsq.nome:<20s}  {fsq.presidente:<20s}  cred: {fsq.crediti_residui}")

    while True:
        raw = input("\nId fantasquadra da modificare (invio per annullare): ").strip()
        if not raw:
            return
        try:
            id_fsq = int(raw)
            fsq = next((f for f in squadre if f.id == id_fsq), None)
            if fsq:
                break
        except ValueError:
            pass
        print("  ✗ Id non valido.")

    print(f"\n── Modifica fantasquadra: {fsq.nome} ──")
    print("  Lascia vuoto per mantenere il valore attuale.\n")

    nome       = chiedi("  Nuovo nome",        default=fsq.nome)
    presidente = chiedi("  Presidente",         default=fsq.presidente)
    email      = chiedi("  Email",              default=fsq.email)
    crediti    = chiedi_int("  Crediti residui (valore assoluto)", default=fsq.crediti_residui)

    # Dati anagrafici
    campi_anag: dict = {}
    if nome != fsq.nome:
        campi_anag["nome"] = nome
    if presidente != fsq.presidente:
        campi_anag["presidente"] = presidente
    if email != fsq.email:
        campi_anag["email"] = email

    if campi_anag:
        db.aggiorna_fantasquadra(id_fsq, **campi_anag)
        print("  ✓ Dati anagrafici aggiornati.")

    # Crediti
    if crediti is not None and crediti != fsq.crediti_residui:
        db.imposta_crediti(id_fsq, crediti)
        print(f"  ✓ Crediti impostati a {crediti}.")

    if not campi_anag and (crediti is None or crediti == fsq.crediti_residui):
        print("  Nessuna modifica.")


# ---------------------------------------------------------------------------
# Visualizza bilancio
# ---------------------------------------------------------------------------

def _formato_valore(v: int) -> str:
    return f"+{v}" if v >= 0 else str(v)


def visualizza_bilancio(db: Database, id_lega: int) -> None:
    squadre = db.lista_fantasquadre(id_lega)
    if not squadre:
        print("Nessuna fantasquadra trovata.")
        return

    print("\n  Visualizza bilancio per:")
    print("  [0] Tutta la lega")
    for fsq in squadre:
        print(f"  [{fsq.id:2d}] {fsq.nome}")

    raw = input("\n  Scelta (invio=annulla): ").strip()
    if not raw:
        return

    if raw == "0":
        # Bilancio completo lega
        movimenti = db.bilancio_lega(id_lega)
        if not movimenti:
            print("\n  (nessun movimento registrato)")
            return

        print()
        squadra_corrente = ""
        totale_squadra = 0
        for m in movimenti:
            if m["nome_squadra"] != squadra_corrente:
                if squadra_corrente:
                    print(f"  {'─'*50}")
                    print(f"  Totale: {_formato_valore(totale_squadra)} cr")
                squadra_corrente = m["nome_squadra"]
                totale_squadra = 0
                print(f"\n  ── {squadra_corrente} ──")
                print(f"  {'Data':<12} {'Valore':>7}  Descrizione")
                print(f"  {'─'*50}")
            data = m.get("data") or "—"
            valore = int(m["valore"])
            totale_squadra += valore
            print(f"  {str(data):<12} {_formato_valore(valore):>7}  {m['descrizione']}")
        if squadra_corrente:
            print(f"  {'─'*50}")
            print(f"  Totale: {_formato_valore(totale_squadra)} cr")

    else:
        # Bilancio singola squadra
        try:
            id_fsq = int(raw)
        except ValueError:
            print("  ✗ Scelta non valida.")
            return

        fsq = next((f for f in squadre if f.id == id_fsq), None)
        if not fsq:
            print("  ✗ Fantasquadra non trovata.")
            return

        movimenti = db.bilancio_fantasquadra(id_fsq)
        print(f"\n  ── Bilancio: {fsq.nome} ──")

        if not movimenti:
            print("  (nessun movimento registrato)")
        else:
            print(f"  {'Data':<12} {'Valore':>7}  Descrizione")
            print(f"  {'─'*50}")
            totale = 0
            for m in movimenti:
                data = m.get("data") or "—"
                valore = int(m["valore"])
                totale += valore
                print(f"  {str(data):<12} {_formato_valore(valore):>7}  {m['descrizione']}")
            print(f"  {'─'*50}")
            print(f"  Totale: {_formato_valore(totale)} cr")

        print(f"\n  Crediti residui attuali: {fsq.crediti_residui} cr")


# ---------------------------------------------------------------------------
# Aggiunta crediti
# ---------------------------------------------------------------------------

def aggiungi_crediti(db: Database, id_lega: int) -> None:
    """
    Aggiunge o toglie crediti a una o più fantasquadre.
    Ogni operazione è registrata nel bilancio.
    """
    squadre = db.lista_fantasquadre(id_lega)
    if not squadre:
        print("Nessuna fantasquadra trovata.")
        return

    print("\n  Aggiungi/togli crediti\n")
    print("  [0] Applica a tutte le squadre")
    for fsq in squadre:
        print(f"  [{fsq.id:2d}] {fsq.nome:<22s}  (cred. attuali: {fsq.crediti_residui})")

    raw = input("\n  Scelta (invio=annulla): ").strip()
    if not raw:
        return

    try:
        scelta = int(raw)
    except ValueError:
        print("  ✗ Scelta non valida.")
        return

    if scelta == 0:
        target = squadre
    else:
        fsq = next((f for f in squadre if f.id == scelta), None)
        if not fsq:
            print("  ✗ Fantasquadra non trovata.")
            return
        target = [fsq]

    # Delta crediti
    raw_delta = input("  Crediti da aggiungere (negativo per togliere): ").strip()
    try:
        delta = int(raw_delta)
    except ValueError:
        print("  ✗ Valore non valido.")
        return

    if delta == 0:
        print("  Nessuna modifica.")
        return

    # Descrizione
    default_desc = "Integrazione crediti pre-asta" if delta > 0 else "Riduzione crediti"
    raw_desc = input(f"  Descrizione [{default_desc}]: ").strip()
    descrizione = raw_desc if raw_desc else default_desc

    # Conferma
    elenco = ", ".join(f.nome for f in target)
    segno = "+" if delta >= 0 else ""
    risposta = input(
        f"\n  {segno}{delta} cr a: {elenco}\n"
        f"  Descrizione: «{descrizione}»\n"
        f"  Confermi? [s/N]: "
    ).strip().lower()
    if risposta != "s":
        print("  Annullato.")
        return

    for fsq in target:
        db.aggiungi_crediti(fsq.id, delta, descrizione)
        print(f"  ✓ {fsq.nome}: {segno}{delta} cr → ora {fsq.crediti_residui + delta} cr")

    print(f"\n  ✓ Operazione completata su {len(target)} fantasquadra/e.")


# ---------------------------------------------------------------------------
# Loop principale
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Riparazione bilancio
# ---------------------------------------------------------------------------

def _ripara_bilancio(db: Database, id_lega: int) -> None:
    """Ricrea le voci di bilancio mancanti per acquisti gia in rose."""
    print()
    print("  Crea le voci di bilancio mancanti per gli acquisti presenti")
    print("  nella rosa ma privi di movimento nel bilancio.")
    risposta = input("\n  Procedi? [s/N]: ").strip().lower()
    if risposta != "s":
        print("  Annullato.")
        return
    n = db.ripara_bilancio(id_lega)
    if n == 0:
        print("  \u2713 Nessuna voce mancante — bilancio gia corretto.")
    else:
        print(f"  \u2713 Create {n} voci di bilancio mancanti.")

MENU = """\
Cosa vuoi fare?
  [1] Modifica parametri lega
  [2] Modifica una fantasquadra
  [3] Visualizza bilancio movimenti
  [4] Aggiungi / togli crediti
  [5] Ripara bilancio (ricrea voci mancanti)
  [0] Esci
"""

def loop(db: Database, id_lega: int) -> None:
    while True:
        separatore()
        print(db.info_lega(id_lega))
        separatore()
        print(MENU)

        scelta = input("Scelta: ").strip()

        if scelta == "1":
            modifica_lega(db, id_lega)
        elif scelta == "2":
            modifica_fantasquadra(db, id_lega)
        elif scelta == "3":
            visualizza_bilancio(db, id_lega)
        elif scelta == "4":
            aggiungi_crediti(db, id_lega)
        elif scelta == "5":
            _ripara_bilancio(db, id_lega)
        elif scelta == "0":
            print("Arrivederci.")
            break
        else:
            print("  ✗ Scelta non valida.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Visualizza e modifica i parametri di una lega FCM."
    )
    parser.add_argument("--db",   required=True, help="Percorso del database SQLite")
    parser.add_argument("--lega", type=int,      help="Id lega (default: menu di scelta)")
    args = parser.parse_args()

    db = Database(args.db)
    id_lega = args.lega if args.lega else scegli_lega(db)
    loop(db, id_lega)


if __name__ == "__main__":
    main()
