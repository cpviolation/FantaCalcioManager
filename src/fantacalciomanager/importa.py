"""
Fantacalcio Manager - Importazione voti da fonti esterne
Supporta:
  - CSV Gazzetta dello Sport (formato classico)
  - CSV generico (colonne configurabili)
  - Excel (.xlsx) con openpyxl (opzionale)
  - Dizionario Python (per API / scraping)

Formato CSV Gazzetta atteso (separatore ";"):
  CodG;Calciatore;Sq;R;Voto;Gf;Gs;Rp;Rs;Rb;Ass;Amm;Esp;Au
dove:
  CodG = codice Gazzetta
  R    = ruolo (P/D/C/A)
  Gf   = gol fatti
  Gs   = gol subiti
  Rp   = rigori parati
  Rs   = rigori sbagliati
  Rb   = rigori battuti (totale)
  Ass  = assist
  Amm  = ammonizioni (0/1)
  Esp  = espulsioni (0/1)
  Au   = autogol
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Optional

from .database import Database
from .calcoli import calcola_fantapunti
from .models import Ruolo, RegolePunteggio

# ---------------------------------------------------------------------------
# Mappa ruolo da stringa a Ruolo enum
# ---------------------------------------------------------------------------
_RUOLO_MAP: dict[str, int] = {
    "P": Ruolo.PORTIERE.value,
    "POR": Ruolo.PORTIERE.value,
    "PORTIERE": Ruolo.PORTIERE.value,
    "D": Ruolo.DIFENSORE.value,
    "DIF": Ruolo.DIFENSORE.value,
    "DIFENSORE": Ruolo.DIFENSORE.value,
    "C": Ruolo.CENTROCAMPISTA.value,
    "CEN": Ruolo.CENTROCAMPISTA.value,
    "CENTROCAMPISTA": Ruolo.CENTROCAMPISTA.value,
    "M": Ruolo.CENTROCAMPISTA.value,   # centroMedio
    "A": Ruolo.ATTACCANTE.value,
    "ATT": Ruolo.ATTACCANTE.value,
    "ATTACCANTE": Ruolo.ATTACCANTE.value,
}


def _parse_float(val: str, default: float = 0.0) -> float:
    try:
        return float(val.replace(",", "."))
    except (ValueError, AttributeError):
        return default


def _parse_int(val: str, default: int = 0) -> int:
    try:
        return int(val)
    except (ValueError, AttributeError):
        return default


def _ruolo_str_to_int(ruolo_str: str) -> int:
    return _RUOLO_MAP.get(ruolo_str.strip().upper(), Ruolo.ATTACCANTE.value)


# ---------------------------------------------------------------------------
# Parsing righe CSV Gazzetta
# ---------------------------------------------------------------------------

def _parse_riga_gazzetta(row: dict[str, str]) -> dict:
    """
    Converte una riga del CSV Gazzetta in un dict voto.
    Chiavi attese (case-insensitive, con/senza BOM):
      CodG, Calciatore, Sq, R, Voto, Gf, Gs, Rp, Rs, Rb, Ass, Amm, Esp, Au
    """
    # normalizza chiavi
    r = {k.strip().lstrip("﻿").upper(): v.strip() for k, v in row.items()}

    voto_raw = _parse_float(r.get("VOTO", "0"))
    ruolo_int = _ruolo_str_to_int(r.get("R", "A"))

    return {
        "codice_gazzetta": r.get("CODG", "").strip(),
        "nome": r.get("CALCIATORE", r.get("NOME", "")).strip(),
        "squadra": r.get("SQ", r.get("SQUADRA", "")).strip(),
        "ruolo": ruolo_int,
        "ha_giocato": voto_raw > 0,
        "voto": voto_raw,
        "gol_fatti": _parse_int(r.get("GF", "0")),
        "gol_fatti_su_rigore": _parse_int(r.get("RS", r.get("RB", "0"))),
        "gol_subiti": _parse_int(r.get("GS", "0")),
        "gol_subiti_su_rigore": 0,   # non sempre presente nel CSV
        "assist": _parse_int(r.get("ASS", "0")),
        "autogol": _parse_int(r.get("AU", "0")),
        "ammonizioni": _parse_int(r.get("AMM", "0")),
        "espulsioni": _parse_int(r.get("ESP", "0")),
        "rigori_parati": _parse_int(r.get("RP", "0")),
        "rigori_sbagliati": _parse_int(r.get("RS", "0")),
    }


# ---------------------------------------------------------------------------
# Importazione da CSV
# ---------------------------------------------------------------------------

class ImportaVoti:
    """
    Importa i voti di una giornata nel database.

    Esempio::

        imp = ImportaVoti(db, anno=2024, giornata=1)
        n = imp.da_csv_gazzetta("voti_g1.csv")
        print(f"Importati {n} giocatori")
    """

    def __init__(
        self,
        db: Database,
        anno: int,
        giornata: int,
        regole: Optional[RegolePunteggio] = None,
        id_lega: int = 0,
    ):
        self.db = db
        self.anno = anno
        self.giornata = giornata
        self.regole = regole or RegolePunteggio()
        self.id_lega = id_lega
        self.errori: list[str] = []
        self.importati: int = 0

    # ------------------------------------------------------------------
    # Metodi pubblici
    # ------------------------------------------------------------------

    def da_csv_gazzetta(
        self,
        percorso: str | Path,
        encoding: str = "utf-8-sig",
        sep: str = ";",
    ) -> int:
        """
        Importa dal formato CSV Gazzetta dello Sport.
        Restituisce il numero di record salvati.
        """
        percorso = Path(percorso)
        testo = percorso.read_text(encoding=encoding, errors="replace")
        return self._importa_csv(testo, sep=sep, parser=_parse_riga_gazzetta)

    def da_csv_generico(
        self,
        percorso: str | Path,
        colonne: dict[str, str],
        encoding: str = "utf-8-sig",
        sep: str = ",",
    ) -> int:
        """
        Importa da un CSV con colonne configurabili.

        colonne: mappa nome_campo_fcm -> nome_colonna_csv
        Esempio::
            colonne = {
                "nome": "Player",
                "squadra": "Team",
                "ruolo": "Role",
                "voto": "Score",
                "gol_fatti": "Goals",
            }
        """
        percorso = Path(percorso)
        testo = percorso.read_text(encoding=encoding, errors="replace")

        def parser(row: dict[str, str]) -> dict:
            mapped: dict[str, str] = {}
            for campo_fcm, col_csv in colonne.items():
                mapped[campo_fcm.upper()] = row.get(col_csv, "")
            return _parse_riga_gazzetta(mapped)

        return self._importa_csv(testo, sep=sep, parser=parser)

    def da_testo_csv(self, testo: str, sep: str = ";") -> int:
        """
        Importa da stringa CSV (utile per test o dati in memoria).
        """
        return self._importa_csv(testo, sep=sep, parser=_parse_riga_gazzetta)

    def da_lista(self, dati: list[dict]) -> int:
        """
        Importa da una lista di dizionari Python già strutturati.
        Ogni dict può avere i campi del formato Gazzetta o già
        normalizzati (chiavi minuscole).
        """
        salvati = 0
        for i, d in enumerate(dati):
            try:
                # normalizza: accetta sia maiuscole che minuscole
                if any(k.isupper() for k in d):
                    riga = _parse_riga_gazzetta(d)
                else:
                    riga = d  # già nel formato interno
                self._salva_riga(riga)
                salvati += 1
            except Exception as exc:
                self.errori.append(f"Riga {i}: {exc}")
        self.importati += salvati
        return salvati

    def da_excel(
        self,
        percorso: str | Path,
        foglio: int | str = 0,
        sep_decimale: str = ".",
    ) -> int:
        """
        Importa da file Excel (.xlsx). Richiede openpyxl.
        La prima riga deve contenere le intestazioni Gazzetta.
        """
        try:
            import openpyxl  # type: ignore
        except ImportError:
            raise ImportError(
                "openpyxl non trovato. Installalo con: pip install openpyxl"
            )

        wb = openpyxl.load_workbook(percorso, data_only=True)
        if isinstance(foglio, int):
            ws = wb.worksheets[foglio]
        else:
            ws = wb[foglio]

        righe = list(ws.values)
        if not righe:
            return 0

        intestazioni = [str(c).strip() if c is not None else "" for c in righe[0]]
        dati_csv = [dict(zip(intestazioni, r)) for r in righe[1:]]
        # converti in stringhe
        str_dati = [
            {k: str(v) if v is not None else "" for k, v in r.items()}
            for r in dati_csv
        ]
        return self._importa_csv_rows(str_dati, parser=_parse_riga_gazzetta)

    # ------------------------------------------------------------------
    # Metodi interni
    # ------------------------------------------------------------------

    def _importa_csv(
        self,
        testo: str,
        sep: str,
        parser,
    ) -> int:
        reader = csv.DictReader(io.StringIO(testo), delimiter=sep)
        rows = list(reader)
        return self._importa_csv_rows(rows, parser=parser)

    def _importa_csv_rows(self, rows: list[dict], parser) -> int:
        salvati = 0
        for i, row in enumerate(rows):
            try:
                riga = parser(row)
                if not riga.get("nome"):
                    continue
                self._salva_riga(riga)
                salvati += 1
            except Exception as exc:
                self.errori.append(f"Riga {i + 2}: {exc}")
        self.importati += salvati
        return salvati

    def _salva_riga(self, riga: dict) -> None:
        """
        Trova (o crea) il giocatore nel DB e salva il voto.
        """
        conn = self.db.connetti()

        # Cerca giocatore per codice Gazzetta o nome+squadra
        id_giocatore = None
        codice = riga.get("codice_gazzetta", "")
        if codice:
            row = conn.execute(
                "SELECT id FROM giocatori WHERE codice_gazzetta=?", (codice,)
            ).fetchone()
            if row:
                id_giocatore = row["id"]

        if id_giocatore is None:
            row = conn.execute(
                "SELECT id FROM giocatori WHERE nome=? AND squadra=?",
                (riga["nome"], riga["squadra"]),
            ).fetchone()
            if row:
                id_giocatore = row["id"]

        # Crea giocatore se non esiste
        if id_giocatore is None:
            cur = conn.execute(
                """INSERT INTO giocatori
                   (nome, squadra, ruolo, codice_gazzetta, costo_iniziale)
                   VALUES (?, ?, ?, ?, 1)""",
                (riga["nome"], riga["squadra"], riga["ruolo"], codice),
            )
            conn.commit()
            id_giocatore = cur.lastrowid

        # Calcola fantapunti
        regole = self._regole_lega()
        fantapunti = calcola_fantapunti(
            voto=riga["voto"],
            ruolo=Ruolo(riga["ruolo"]),
            gol_fatti=riga.get("gol_fatti", 0),
            gol_fatti_su_rigore=riga.get("gol_fatti_su_rigore", 0),
            gol_subiti=riga.get("gol_subiti", 0),
            gol_subiti_su_rigore=riga.get("gol_subiti_su_rigore", 0),
            assist=riga.get("assist", 0),
            autogol=riga.get("autogol", 0),
            ammonizioni=riga.get("ammonizioni", 0),
            espulsioni=riga.get("espulsioni", 0),
            rigori_parati=riga.get("rigori_parati", 0),
            rigori_sbagliati=riga.get("rigori_sbagliati", 0),
            regole=regole,
        )

        # Salva voto (upsert)
        conn.execute(
            """INSERT INTO voti_giornata
               (id_giocatore, giornata, anno, ha_giocato, voto,
                gol_fatti, gol_fatti_su_rigore, gol_subiti, gol_subiti_su_rigore,
                assist, autogol, ammonizioni, espulsioni,
                rigori_parati, rigori_sbagliati, fantapunti)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id_giocatore, giornata, anno) DO UPDATE SET
                 ha_giocato=excluded.ha_giocato,
                 voto=excluded.voto,
                 gol_fatti=excluded.gol_fatti,
                 gol_fatti_su_rigore=excluded.gol_fatti_su_rigore,
                 gol_subiti=excluded.gol_subiti,
                 gol_subiti_su_rigore=excluded.gol_subiti_su_rigore,
                 assist=excluded.assist,
                 autogol=excluded.autogol,
                 ammonizioni=excluded.ammonizioni,
                 espulsioni=excluded.espulsioni,
                 rigori_parati=excluded.rigori_parati,
                 rigori_sbagliati=excluded.rigori_sbagliati,
                 fantapunti=excluded.fantapunti""",
            (
                id_giocatore, self.giornata, self.anno,
                1 if riga["ha_giocato"] else 0,
                riga["voto"],
                riga.get("gol_fatti", 0),
                riga.get("gol_fatti_su_rigore", 0),
                riga.get("gol_subiti", 0),
                riga.get("gol_subiti_su_rigore", 0),
                riga.get("assist", 0),
                riga.get("autogol", 0),
                riga.get("ammonizioni", 0),
                riga.get("espulsioni", 0),
                riga.get("rigori_parati", 0),
                riga.get("rigori_sbagliati", 0),
                fantapunti,
            ),
        )
        conn.commit()

    def _regole_lega(self) -> RegolePunteggio:
        if self.id_lega:
            try:
                return self.db.get_regole(self.id_lega)
            except Exception:
                pass
        return self.regole
