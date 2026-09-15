#!/usr/bin/env python3
"""
macros/setup_lega.py
====================
Macro interattiva per configurare una lega FantaCalcio dall'inizio.

Esegue i passi 1-6 descritti nella documentazione:
  1. Scarica l'ArchivioSerieA dell'anno scelto
  2. Carica i dati giocatori dal file .fca
  3. Crea (o apre) il database SQLite della lega
  4. Importa tutti i giocatori nel database
  5. Crea le fantasquadre
  6. Assegna giocatori tramite asta (acquisiti da riga di comando o interattiva)

Uso rapido (non interattivo)
----------------------------
  python macros/setup_lega.py \\
      --anno 2024 \\
      --lega "Lega Sanremo" \\
      --squadre "Galacticos:Mario Rossi" "Dream Team:Luigi Bianchi" \\
      --db data/lega_sanremo.db

Uso interattivo (senza argomenti)
----------------------------------
  python macros/setup_lega.py

Acquisizioni post-setup
-----------------------
  python macros/setup_lega.py --db data/lega_sanremo.db --acquista
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Aggiungi src/ al path per importare il pacchetto senza installarlo
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fantacalciomanager.database import Database, PlayersDatabase
from fantacalciomanager.mercato import Mercato, MercatoError
from fantacalciomanager.sync import (
    ConfigLega,
    RisultatoSetup,
    crea_lega_da_archivio,
    importa_giocatori,
)
from fantacalciomanager.web import download_season_archive

DATA_DIR = Path(__file__).parent.parent / "data"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sep(char: str = "─", n: int = 60) -> None:
    print(char * n)


def _header(titolo: str) -> None:
    _sep()
    print(f"  {titolo}")
    _sep()


def _input_int(prompt: str, default: int | None = None) -> int:
    while True:
        raw = input(prompt).strip()
        if not raw and default is not None:
            return default
        try:
            return int(raw)
        except ValueError:
            print("  ⚠  Inserisci un numero intero.")


def _input_str(prompt: str, default: str = "") -> str:
    raw = input(prompt).strip()
    return raw or default


# ---------------------------------------------------------------------------
# Passo 1-2: download + caricamento archivio
# ---------------------------------------------------------------------------

def step_archivio(anno: int, force_download: bool = False) -> PlayersDatabase:
    DATA_DIR.mkdir(exist_ok=True)
    _header(f"Passo 1/5 — Archivio SerieA {anno}")

    zip_name = f"ArchivioA{anno}SerieA.zip"
    zip_path = DATA_DIR / zip_name

    if zip_path.exists() and not force_download:
        print(f"  ✓ Archivio già presente: {zip_path}")
    else:
        print(f"  Scaricamento {zip_name} ...")
        try:
            zip_path = download_season_archive(
                anno, DATA_DIR, force=force_download, show_progress=True
            )
            print(f"  ✓ Salvato in {zip_path}")
        except Exception as exc:
            print(f"  ✗ Errore download: {exc}")
            print("  Puoi copiare manualmente il file ZIP in:")
            print(f"    {zip_path}")
            sys.exit(1)

    _header("Passo 2/5 — Caricamento giocatori dal .fca")
    print("  (richiede mdbtools installato: apt install mdbtools)")
    try:
        pdb = PlayersDatabase.from_zip(zip_path)
        print(f"  ✓ {len(pdb.teams)} squadre, {len(pdb.players)} giocatori caricati")
        return pdb
    except FileNotFoundError as exc:
        print(f"  ✗ File .fca non trovato nell'archivio: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"  ✗ Errore caricamento: {exc}")
        print("  Assicurati che mdbtools sia installato (apt install mdbtools)")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Passo 3: database + lega
# ---------------------------------------------------------------------------

def step_database(db_path: Path, pdb: PlayersDatabase,
                  nome_lega: str, anno: int,
                  squadre_raw: list[str],
                  crediti: int, giornate: int) -> tuple[Database, RisultatoSetup]:
    _header("Passo 3/5 — Database e lega")

    db = Database(db_path)
    print(f"  ✓ Database: {db_path}")

    # Controlla se esiste già una lega con stesso nome+anno
    for lega in db.lista_leghe():
        if lega.nome == nome_lega and lega.anno == anno:
            print(f"  ✓ Lega già esistente (id={lega.id}) — salto la creazione")
            return db, RisultatoSetup(
                id_lega=lega.id,
                id_fantasquadre={
                    f.nome: f.id for f in db.lista_fantasquadre(lega.id)
                },
                importazione=importa_giocatori(pdb, db),
            )

    # Parsa le squadre: "Nome Squadra:Presidente" o "Nome Squadra"
    squadre: list[tuple] = []
    for s in squadre_raw:
        parts = s.split(":", 1)
        nome_sq = parts[0].strip()
        presidente = parts[1].strip() if len(parts) > 1 else ""
        squadre.append((nome_sq, presidente))

    config = ConfigLega(
        nome=nome_lega,
        anno=anno,
        squadre=squadre,
        crediti=crediti,
        giornate=giornate,
    )

    _header("Passo 4/5 — Importazione giocatori")
    setup = crea_lega_da_archivio(pdb, db, config)
    print(setup.importazione)

    print()
    print(f"  ✓ Lega '{nome_lega}' creata (id={setup.id_lega})")
    for nome_sq, sid in setup.id_fantasquadre.items():
        fsq = db.get_fantasquadra(sid)
        crediti_disp = fsq.crediti_residui if fsq else crediti
        print(f"     [{sid}] {nome_sq} — {crediti_disp} crediti")

    return db, setup


# ---------------------------------------------------------------------------
# Passo 5: fantasquadre interattive (se non passate da CLI)
# ---------------------------------------------------------------------------

def step_squadre_interattivo(db: Database, id_lega: int,
                              crediti: int) -> dict[str, int]:
    _header("Passo 3b/5 — Creazione fantasquadre")
    n = _input_int("  Quante fantasquadre? [2-20]: ", default=8)
    id_fsq: dict[str, int] = {}
    for i in range(1, n + 1):
        print(f"\n  — Squadra {i} —")
        nome_sq   = _input_str(f"    Nome squadra: ")
        presidente = _input_str(f"    Presidente:  ")
        email      = _input_str(f"    Email:       ")
        sid = db.crea_fantasquadra(id_lega, nome_sq, presidente, email)
        id_fsq[nome_sq] = sid
        print(f"    ✓ Creata (id={sid}), {crediti} crediti")
    return id_fsq


# ---------------------------------------------------------------------------
# Passo 6: acquisizioni interattive
# ---------------------------------------------------------------------------

def step_acquisizioni(db: Database, id_lega: int, anno: int,
                      id_fantasquadre: dict[str, int]) -> None:
    _header("Passo 5/5 — Acquisizioni (asta)")
    m = Mercato(db, id_lega=id_lega, anno=anno)
    conn = db.connetti()

    print("  Comandi: acquista | lista | budget | fine")
    print("  Cerca giocatori: cerca <nome>")
    print()

    sq_list = list(id_fantasquadre.items())

    while True:
        try:
            raw = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Acquisizioni terminate.")
            break

        if not raw or raw == "fine" or raw == "q":
            break

        elif raw.startswith("cerca "):
            q = raw[6:].strip()
            risultati = db.cerca_giocatori(nome=q)
            if not risultati:
                print("    Nessun risultato.")
            else:
                for g in risultati[:15]:
                    from fantacalciomanager.models import Ruolo
                    print(f"    [{g.id:4d}] {Ruolo(g.ruolo).abbreviazione()} "
                          f"{g.nome:25s} {g.squadra:15s} "
                          f"Q.{g.costo_iniziale}")

        elif raw == "lista":
            for i, (nome_sq, sid) in enumerate(sq_list, 1):
                print(f"    [{i}] {nome_sq} (id={sid})")

        elif raw == "budget":
            for nome_sq, sid in sq_list:
                info = m.riepilogo_budget(sid)
                print(f"    {nome_sq:25s}  crediti={info['crediti_residui']:4d}  "
                      f"giocatori={info['giocatori_in_rosa']}")

        elif raw == "acquista":
            print("    Squadre disponibili:")
            for i, (nome_sq, sid) in enumerate(sq_list, 1):
                print(f"      [{i}] {nome_sq}")
            idx_sq = _input_int("    Numero squadra: ", default=1) - 1
            if not (0 <= idx_sq < len(sq_list)):
                print("    ✗ Numero non valido.")
                continue
            nome_sq, id_sq = sq_list[idx_sq]

            id_g  = _input_int("    ID giocatore (usa 'cerca'): ")
            prezzo = _input_int("    Prezzo di acquisto [1]: ", default=1)

            giocatore = db.get_giocatore(id_g)
            if not giocatore:
                print("    ✗ Giocatore non trovato.")
                continue

            try:
                m.acquista(id_sq, id_g, prezzo)
                print(f"    ✓ {giocatore.nome} → {nome_sq} ({prezzo} crediti)")
            except MercatoError as exc:
                print(f"    ✗ {exc}")

        else:
            print("    Comandi: cerca <nome> | acquista | lista | budget | fine")

    # Riepilogo finale
    _sep()
    print("  Riepilogo rose:")
    for nome_sq, sid in id_fasquadre_list(id_fantasquadre):
        rosa = db.rosa_fantasquadra(sid)
        info = m.riepilogo_budget(sid)
        print(f"\n  {nome_sq} — {info['crediti_residui']} crediti rimasti")
        for r in rosa:
            from fantacalciomanager.models import Ruolo
            print(f"    {Ruolo(r['ruolo']).abbreviazione()} "
                  f"{r['nome']:25s} {r['squadra']:15s} "
                  f"Q.{r['prezzo_acquisto']}")


def id_fasquadre_list(d: dict[str, int]) -> list[tuple[str, int]]:
    return list(d.items())


# ---------------------------------------------------------------------------
# Punto d'ingresso
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Setup completo di una lega FantaCalcio (passi 1-6).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Esempi:
  # Setup non interattivo con 3 squadre
  python macros/setup_lega.py \\
      --anno 2024 --lega "Lega Sanremo" \\
      --squadre "Galacticos:Mario Rossi" "Dream Team:Luigi Bianchi" \\
                "Gli Invincibili:Anna Verdi" \\
      --db data/lega.db

  # Solo fase acquisizioni su lega già creata
  python macros/setup_lega.py --db data/lega.db --acquista
        """,
    )
    p.add_argument("--anno", type=int,
                   help="Anno di inizio stagione (es. 2024)")
    p.add_argument("--lega", dest="nome_lega",
                   help="Nome della lega")
    p.add_argument("--squadre", nargs="+", metavar="NOME:PRESIDENTE",
                   help="Fantasquadre nel formato 'Nome:Presidente'")
    p.add_argument("--crediti", type=int, default=500,
                   help="Crediti iniziali per squadra (default: 500)")
    p.add_argument("--giornate", type=int, default=38,
                   help="Giornate totali (default: 38)")
    p.add_argument("--db", dest="db_path",
                   help="Percorso al file database .db")
    p.add_argument("--acquista", action="store_true",
                   help="Avvia solo la fase acquisizioni su un DB esistente")
    p.add_argument("--force-download", action="store_true",
                   help="Ri-scarica l'archivio anche se già presente")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # ------------------------------------------------------------------ #
    #  Modalità: solo acquisizioni su DB esistente                        #
    # ------------------------------------------------------------------ #
    if args.acquista:
        if not args.db_path:
            print("✗ Specifica il database con --db", file=sys.stderr)
            sys.exit(1)
        db = Database(args.db_path)
        leghe = db.lista_leghe()
        if not leghe:
            print("✗ Nessuna lega trovata nel database.", file=sys.stderr)
            sys.exit(1)
        lega = leghe[0]
        id_fsq = {f.nome: f.id for f in db.lista_fantasquadre(lega.id)}
        step_acquisizioni(db, lega.id, lega.anno, id_fsq)
        return

    # ------------------------------------------------------------------ #
    #  Modalità interattiva se mancano argomenti obbligatori              #
    # ------------------------------------------------------------------ #
    interattivo = not (args.anno and args.nome_lega and args.squadre)

    if interattivo:
        print()
        print("╔══════════════════════════════════════════════════╗")
        print("║       FantaCalcio Manager — Setup Lega           ║")
        print("╚══════════════════════════════════════════════════╝")
        print()

    anno = args.anno or _input_int("  Anno stagione (es. 2024): ")
    nome_lega = args.nome_lega or _input_str("  Nome della lega:          ")
    crediti   = args.crediti
    giornate  = args.giornate

    db_default = str(DATA_DIR / f"lega_{nome_lega.lower().replace(' ', '_')}_{anno}.db")
    db_path = Path(args.db_path or _input_str(
        f"  Percorso database [{db_default}]: ", default=db_default
    ))

    # ------------------------------------------------------------------ #
    #  Passi 1-2: archivio                                               #
    # ------------------------------------------------------------------ #
    pdb = step_archivio(anno, force_download=args.force_download)

    # ------------------------------------------------------------------ #
    #  Passi 3-4: DB + lega + importazione                               #
    # ------------------------------------------------------------------ #
    if args.squadre:
        squadre_raw = args.squadre
    elif interattivo:
        # Creeremo le squadre dopo aver aperto il DB
        squadre_raw = []
    else:
        squadre_raw = []

    db, setup = step_database(
        db_path, pdb, nome_lega, anno,
        squadre_raw, crediti, giornate,
    )
    id_fantasquadre = setup.id_fantasquadre

    # Squadre interattive se non già create
    if interattivo and not id_fantasquadre:
        _header("Passo 4/5 — Importazione giocatori")
        res_imp = importa_giocatori(pdb, db)
        print(res_imp)
        id_fantasquadre = step_squadre_interattivo(db, setup.id_lega, crediti)

    # ------------------------------------------------------------------ #
    #  Passo 5: acquisizioni                                              #
    # ------------------------------------------------------------------ #
    _header("Passo 5/5 — Acquisizioni (asta)")
    vai_asta = True
    if interattivo:
        r = _input_str("  Vuoi iniziare subito le acquisizioni? [S/n]: ",
                       default="s").lower()
        vai_asta = r in ("s", "si", "sì", "y", "yes", "")

    if vai_asta:
        step_acquisizioni(db, setup.id_lega, anno, id_fantasquadre)
    else:
        print()
        print("  Puoi fare le acquisizioni in seguito con:")
        print(f"    python macros/setup_lega.py --db {db_path} --acquista")

    # ------------------------------------------------------------------ #
    #  Riepilogo finale                                                   #
    # ------------------------------------------------------------------ #
    _sep("═")
    print(f"  Setup completato!")
    print(f"  Database: {db_path}")
    print(f"  Lega '{nome_lega}' — id={setup.id_lega}")
    _sep("═")


if __name__ == "__main__":
    main()
