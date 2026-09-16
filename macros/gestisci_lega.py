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
# Loop principale
# ---------------------------------------------------------------------------

MENU = """\
Cosa vuoi fare?
  [1] Modifica parametri lega
  [2] Modifica una fantasquadra
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
