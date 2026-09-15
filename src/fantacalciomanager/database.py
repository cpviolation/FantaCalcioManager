"""Read FantaCalcio Manager .fca (MS Access / Jet) databases via mdbtools."""
from __future__ import annotations
import csv
import enum
import io
import sqlite3
import subprocess
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from fantacalciomanager.models import (
    Lega, Divisione, Fantasquadra, Giocatore, Rosa,
    VotoGiornata, Formazione, Incontro, MovimentoBilancio,
    Ruolo, StatoGiocatore
)

DEFAULT_DB = str(Path.home() / "Documenti" / "FantacalcioManager" / "fcm.db")

from fantacalciomanager.archive import extract_archive, find_fca_file

# Database SQLite per la gestione completa delle leghe FCM.
class Database:
    """
    Wrapper sul database SQLite con tutti i metodi CRUD
    necessari a FCM.
    """

    def __init__(self, percorso=DEFAULT_DB):
        percorso = str(percorso)  # accetta Path e str
        os.makedirs(os.path.dirname(os.path.abspath(percorso)), exist_ok=True)
        self.percorso = percorso
        self._conn: Optional[sqlite3.Connection] = None
        self._inizializza()

    @property
    def path(self) -> str:
        return self.percorso

    # ------------------------------------------------------------------
    # Connessione
    # ------------------------------------------------------------------

    def connetti(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.percorso,
                detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
        return self._conn

    def chiudi(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transazione(self):
        conn = self.connetti()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _inizializza(self) -> None:
        conn = self.connetti()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS leghe (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                nome            TEXT    NOT NULL,
                anno            INTEGER NOT NULL,
                stagione        INTEGER DEFAULT 1,
                crediti_iniziali INTEGER DEFAULT 500,
                giornate_totali INTEGER DEFAULT 38,
                crediti_mercato_libero INTEGER DEFAULT 0,
                note            TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS divisioni (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                id_lega INTEGER NOT NULL REFERENCES leghe(id),
                nome    TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fantasquadre (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                id_lega         INTEGER NOT NULL REFERENCES leghe(id),
                nome            TEXT    NOT NULL,
                presidente      TEXT    NOT NULL,
                email           TEXT DEFAULT '',
                telefono_casa   TEXT DEFAULT '',
                telefono_lavoro TEXT DEFAULT '',
                cellulare       TEXT DEFAULT '',
                id_divisione    INTEGER DEFAULT 0,
                crediti_residui INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS giocatori (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                nome              TEXT NOT NULL,
                squadra           TEXT NOT NULL,
                ruolo             INTEGER NOT NULL CHECK(ruolo BETWEEN 1 AND 4),
                codice_gazzetta   TEXT DEFAULT '',
                costo_iniziale    INTEGER DEFAULT 1,
                extracomunitario  INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS rose (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                id_fantasquadra     INTEGER NOT NULL REFERENCES fantasquadre(id),
                id_giocatore        INTEGER NOT NULL REFERENCES giocatori(id),
                stato               INTEGER DEFAULT 0,  -- 0=rosa,1=svincolato,2=infortunato
                contratto           INTEGER DEFAULT 1,  -- -1=inf,-2=prestito
                prezzo_acquisto     INTEGER DEFAULT 1,
                prezzo_svincolo     INTEGER DEFAULT 0,
                giornate_infortunio INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS voti_giornata (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                id_giocatore            INTEGER NOT NULL REFERENCES giocatori(id),
                giornata                INTEGER NOT NULL,
                anno                    INTEGER NOT NULL,
                ha_giocato              INTEGER DEFAULT 0,
                voto                    REAL    DEFAULT 0,
                gol_fatti               INTEGER DEFAULT 0,
                gol_fatti_su_rigore     INTEGER DEFAULT 0,
                gol_subiti              INTEGER DEFAULT 0,
                gol_subiti_su_rigore    INTEGER DEFAULT 0,
                assist                  INTEGER DEFAULT 0,
                autogol                 INTEGER DEFAULT 0,
                ammonizioni             INTEGER DEFAULT 0,
                espulsioni              INTEGER DEFAULT 0,
                rigori_parati           INTEGER DEFAULT 0,
                rigori_sbagliati        INTEGER DEFAULT 0,
                fantapunti              REAL    DEFAULT 0,
                UNIQUE(id_giocatore, giornata, anno)
            );

            CREATE TABLE IF NOT EXISTS formazioni (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                id_fantasquadra INTEGER NOT NULL REFERENCES fantasquadre(id),
                giornata        INTEGER NOT NULL,
                anno            INTEGER NOT NULL,
                titolari        TEXT DEFAULT '',   -- CSV di id_giocatore
                panchina        TEXT DEFAULT '',   -- CSV di id_giocatore
                modulo          TEXT DEFAULT '4-3-3',
                note            TEXT DEFAULT '',
                UNIQUE(id_fantasquadra, giornata, anno)
            );

            CREATE TABLE IF NOT EXISTS incontri (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                id_lega     INTEGER NOT NULL REFERENCES leghe(id),
                giornata    INTEGER NOT NULL,
                anno        INTEGER NOT NULL,
                id_casa     INTEGER NOT NULL REFERENCES fantasquadre(id),
                id_fuori    INTEGER NOT NULL REFERENCES fantasquadre(id),
                punti_casa  REAL DEFAULT 0,
                punti_fuori REAL DEFAULT 0,
                gol_casa    INTEGER DEFAULT 0,
                gol_fuori   INTEGER DEFAULT 0,
                giocata     INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS bilanci (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                id_fantasquadra INTEGER NOT NULL REFERENCES fantasquadre(id),
                descrizione     TEXT NOT NULL,
                valore          INTEGER NOT NULL,  -- positivo=entrata, negativo=uscita
                data            DATE DEFAULT CURRENT_DATE
            );

            CREATE TABLE IF NOT EXISTS regole_punteggio (
                id_lega                     INTEGER PRIMARY KEY REFERENCES leghe(id),
                gol_portiere                REAL DEFAULT 10.0,
                gol_difensore               REAL DEFAULT 6.0,
                gol_centrocampista          REAL DEFAULT 4.5,
                gol_attaccante              REAL DEFAULT 3.0,
                bonus_assist                REAL DEFAULT 1.0,
                bonus_rigore_parato         REAL DEFAULT 3.0,
                malus_autogol               REAL DEFAULT 2.0,
                malus_ammonizione           REAL DEFAULT 0.5,
                malus_espulsione            REAL DEFAULT 1.0,
                malus_rigore_sbagliato      REAL DEFAULT 3.0,
                malus_gol_subito_portiere   REAL DEFAULT 1.0
            );
        """)
        conn.commit()

    # ------------------------------------------------------------------
    # LEGHE
    # ------------------------------------------------------------------

    def crea_lega(self, nome: str, anno: int, stagione: int = 1,
                  crediti: int = 500, giornate: int = 38) -> int:
        with self.transazione() as conn:
            cur = conn.execute(
                "INSERT INTO leghe(nome,anno,stagione,crediti_iniziali,giornate_totali)"
                " VALUES(?,?,?,?,?)",
                (nome, anno, stagione, crediti, giornate)
            )
            id_lega = cur.lastrowid
            conn.execute(
                "INSERT INTO regole_punteggio(id_lega) VALUES(?)", (id_lega,)
            )
        assert id_lega is not None
        return id_lega

    def get_lega(self, id_lega: int) -> Optional[Lega]:
        row = self.connetti().execute(
            "SELECT * FROM leghe WHERE id=?", (id_lega,)
        ).fetchone()
        return self._row_to_lega(row) if row else None

    def lista_leghe(self) -> list[Lega]:
        rows = self.connetti().execute(
            "SELECT * FROM leghe ORDER BY anno DESC, nome"
        ).fetchall()
        return [self._row_to_lega(r) for r in rows]

    def _row_to_lega(self, row) -> Lega:
        return Lega(
            id=row["id"], nome=row["nome"], anno=row["anno"],
            stagione=row["stagione"],
            crediti_iniziali=row["crediti_iniziali"],
            giornate_totali=row["giornate_totali"],
        )

    # ------------------------------------------------------------------
    # FANTASQUADRE
    # ------------------------------------------------------------------

    def crea_fantasquadra(self, id_lega: int, nome: str,
                          presidente: str, email: str = "",
                          id_divisione: int = 0) -> int:
        lega = self.get_lega(id_lega)
        crediti = lega.crediti_iniziali if lega else 500
        with self.transazione() as conn:
            cur = conn.execute(
                "INSERT INTO fantasquadre"
                "(id_lega,nome,presidente,email,id_divisione,crediti_residui)"
                " VALUES(?,?,?,?,?,?)",
                (id_lega, nome, presidente, email, id_divisione, crediti)
            )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def get_fantasquadra(self, id_fsq: int) -> Optional[Fantasquadra]:
        row = self.connetti().execute(
            "SELECT * FROM fantasquadre WHERE id=?", (id_fsq,)
        ).fetchone()
        return self._row_to_fsq(row) if row else None

    def lista_fantasquadre(self, id_lega: int) -> list[Fantasquadra]:
        rows = self.connetti().execute(
            "SELECT * FROM fantasquadre WHERE id_lega=? ORDER BY nome",
            (id_lega,)
        ).fetchall()
        return [self._row_to_fsq(r) for r in rows]

    def aggiorna_crediti(self, id_fsq: int, delta: int) -> None:
        with self.transazione() as conn:
            conn.execute(
                "UPDATE fantasquadre SET crediti_residui=crediti_residui+? WHERE id=?",
                (delta, id_fsq)
            )

    def _row_to_fsq(self, row) -> Fantasquadra:
        return Fantasquadra(
            id=row["id"], id_lega=row["id_lega"], nome=row["nome"],
            presidente=row["presidente"], email=row["email"],
            telefono_casa=row["telefono_casa"],
            telefono_lavoro=row["telefono_lavoro"],
            cellulare=row["cellulare"],
            id_divisione=row["id_divisione"],
            crediti_residui=row["crediti_residui"],
        )

    # ------------------------------------------------------------------
    # GIOCATORI
    # ------------------------------------------------------------------

    def aggiungi_giocatore(self, nome: str, squadra: str,
                           ruolo: Ruolo, codice: str = "",
                           costo: int = 1) -> int:
        with self.transazione() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO giocatori"
                "(nome,squadra,ruolo,codice_gazzetta,costo_iniziale)"
                " VALUES(?,?,?,?,?)",
                (nome, squadra, int(ruolo), codice, costo)
            )
            if cur.lastrowid == 0:
                row = conn.execute(
                    "SELECT id FROM giocatori WHERE nome=? AND squadra=?",
                    (nome, squadra)
                ).fetchone()
                return row["id"]
            assert cur.lastrowid is not None
            return cur.lastrowid

    def get_giocatore(self, id_g: int) -> Optional[Giocatore]:
        row = self.connetti().execute(
            "SELECT * FROM giocatori WHERE id=?", (id_g,)
        ).fetchone()
        return self._row_to_giocatore(row) if row else None

    def cerca_giocatori(self, nome: str = "", squadra: str = "",
                        ruolo: Optional[Ruolo] = None) -> list[Giocatore]:
        query = "SELECT * FROM giocatori WHERE 1=1"
        params: list = []
        if nome:
            query += " AND nome LIKE ?"
            params.append(f"%{nome}%")
        if squadra:
            query += " AND squadra LIKE ?"
            params.append(f"%{squadra}%")
        if ruolo:
            query += " AND ruolo=?"
            params.append(int(ruolo))
        query += " ORDER BY nome"
        rows = self.connetti().execute(query, params).fetchall()
        return [self._row_to_giocatore(r) for r in rows]

    def _row_to_giocatore(self, row) -> Giocatore:
        return Giocatore(
            id=row["id"], nome=row["nome"], squadra=row["squadra"],
            ruolo=Ruolo(row["ruolo"]),
            codice_gazzetta=row["codice_gazzetta"],
            costo_iniziale=row["costo_iniziale"],
            extracomunitario=bool(row["extracomunitario"]),
        )

    # ------------------------------------------------------------------
    # ROSA
    # ------------------------------------------------------------------

    def tesserare_giocatore(self, id_fsq: int, id_giocatore: int,
                            prezzo: int, contratto: int = 1) -> int:
        with self.transazione() as conn:
            cur = conn.execute(
                "INSERT INTO rose"
                "(id_fantasquadra,id_giocatore,prezzo_acquisto,contratto)"
                " VALUES(?,?,?,?)",
                (id_fsq, id_giocatore, prezzo, contratto)
            )
            conn.execute(
                "UPDATE fantasquadre SET crediti_residui=crediti_residui-? WHERE id=?",
                (prezzo, id_fsq)
            )
            conn.execute(
                "INSERT INTO bilanci(id_fantasquadra,descrizione,valore)"
                " VALUES(?,?,?)",
                (id_fsq, f"Acquisto giocatore", -prezzo)
            )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def svincolare_giocatore(self, id_rosa: int, prezzo: int = 0) -> None:
        with self.transazione() as conn:
            row = conn.execute(
                "SELECT id_fantasquadra FROM rose WHERE id=?", (id_rosa,)
            ).fetchone()
            if not row:
                return
            id_fsq = row["id_fantasquadra"]
            conn.execute(
                "UPDATE rose SET stato=1, prezzo_svincolo=? WHERE id=?",
                (prezzo, id_rosa)
            )
            if prezzo > 0:
                conn.execute(
                    "UPDATE fantasquadre SET crediti_residui=crediti_residui+? WHERE id=?",
                    (prezzo, id_fsq)
                )
                conn.execute(
                    "INSERT INTO bilanci(id_fantasquadra,descrizione,valore)"
                    " VALUES(?,?,?)",
                    (id_fsq, "Svincolo giocatore", prezzo)
                )

    def rosa_fantasquadra(self, id_fsq: int,
                          stato: Optional[StatoGiocatore] = None) -> list[dict]:
        query = """
            SELECT r.*, g.nome, g.squadra, g.ruolo, g.costo_iniziale
            FROM rose r
            JOIN giocatori g ON g.id = r.id_giocatore
            WHERE r.id_fantasquadra = ?
        """
        params: list = [id_fsq]
        if stato is not None:
            query += " AND r.stato = ?"
            params.append(int(stato))
        query += " ORDER BY g.ruolo, g.nome"
        rows = self.connetti().execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def giocatori_liberi(self, id_lega: int,
                         ruolo: Optional[Ruolo] = None) -> list[dict]:
        """Restituisce i giocatori non tesserati in nessuna fantasquadra della lega."""
        query = """
            SELECT g.*
            FROM giocatori g
            WHERE g.id NOT IN (
                SELECT r.id_giocatore
                FROM rose r
                JOIN fantasquadre f ON f.id = r.id_fantasquadra
                WHERE f.id_lega = ? AND r.stato != 1
            )
        """
        params: list = [id_lega]
        if ruolo:
            query += " AND g.ruolo=?"
            params.append(int(ruolo))
        query += " ORDER BY g.ruolo, g.nome"
        rows = self.connetti().execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # VOTI GIORNATA
    # ------------------------------------------------------------------

    def salva_voto(self, voto: VotoGiornata) -> None:
        with self.transazione() as conn:
            conn.execute("""
                INSERT INTO voti_giornata
                (id_giocatore,giornata,anno,ha_giocato,voto,
                 gol_fatti,gol_fatti_su_rigore,gol_subiti,gol_subiti_su_rigore,
                 assist,autogol,ammonizioni,espulsioni,
                 rigori_parati,rigori_sbagliati,fantapunti)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id_giocatore,giornata,anno) DO UPDATE SET
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
                  fantapunti=excluded.fantapunti
            """, (
                voto.id_giocatore, voto.giornata, voto.anno,
                int(voto.ha_giocato), voto.voto,
                voto.gol_fatti, voto.gol_fatti_su_rigore,
                voto.gol_subiti, voto.gol_subiti_su_rigore,
                voto.assist, voto.autogol,
                voto.ammonizioni, voto.espulsioni,
                voto.rigori_parati, voto.rigori_sbagliati,
                voto.fantapunti,
            ))

    def voti_giornata(self, giornata: int, anno: int) -> list[dict]:
        rows = self.connetti().execute("""
            SELECT v.*, g.nome, g.squadra, g.ruolo
            FROM voti_giornata v
            JOIN giocatori g ON g.id = v.id_giocatore
            WHERE v.giornata=? AND v.anno=?
            ORDER BY g.ruolo, g.nome
        """, (giornata, anno)).fetchall()
        return [dict(r) for r in rows]

    def voti_giocatore(self, id_giocatore: int, anno: int) -> list[dict]:
        rows = self.connetti().execute("""
            SELECT * FROM voti_giornata
            WHERE id_giocatore=? AND anno=?
            ORDER BY giornata
        """, (id_giocatore, anno)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # FORMAZIONI
    # ------------------------------------------------------------------

    def salva_formazione(self, f: Formazione) -> None:
        titolari_csv = ",".join(str(x) for x in f.titolari)
        panchina_csv = ",".join(str(x) for x in f.panchina)
        with self.transazione() as conn:
            conn.execute("""
                INSERT INTO formazioni
                (id_fantasquadra,giornata,anno,titolari,panchina,modulo,note)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(id_fantasquadra,giornata,anno) DO UPDATE SET
                  titolari=excluded.titolari,
                  panchina=excluded.panchina,
                  modulo=excluded.modulo,
                  note=excluded.note
            """, (f.id_fantasquadra, f.giornata, f.anno,
                  titolari_csv, panchina_csv, f.modulo, f.note))

    def get_formazione(self, id_fsq: int, giornata: int, anno: int) -> Optional[Formazione]:
        row = self.connetti().execute(
            "SELECT * FROM formazioni WHERE id_fantasquadra=? AND giornata=? AND anno=?",
            (id_fsq, giornata, anno)
        ).fetchone()
        if not row:
            return None
        titolari = [int(x) for x in row["titolari"].split(",") if x]
        panchina = [int(x) for x in row["panchina"].split(",") if x]
        return Formazione(
            id=row["id"], id_fantasquadra=row["id_fantasquadra"],
            giornata=row["giornata"], anno=row["anno"],
            titolari=titolari, panchina=panchina,
            modulo=row["modulo"], note=row["note"],
        )

    # ------------------------------------------------------------------
    # INCONTRI
    # ------------------------------------------------------------------

    def crea_incontro(self, id_lega: int, giornata: int, anno: int,
                      id_casa: int, id_fuori: int) -> int:
        with self.transazione() as conn:
            cur = conn.execute(
                "INSERT INTO incontri(id_lega,giornata,anno,id_casa,id_fuori)"
                " VALUES(?,?,?,?,?)",
                (id_lega, giornata, anno, id_casa, id_fuori)
            )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def salva_risultato(self, id_incontro: int,
                        punti_casa: float, punti_fuori: float,
                        gol_casa: int, gol_fuori: int) -> None:
        with self.transazione() as conn:
            conn.execute("""
                UPDATE incontri
                SET punti_casa=?,punti_fuori=?,
                    gol_casa=?,gol_fuori=?,giocata=1
                WHERE id=?
            """, (punti_casa, punti_fuori, gol_casa, gol_fuori, id_incontro))

    def incontri_giornata(self, id_lega: int, giornata: int, anno: int) -> list[dict]:
        rows = self.connetti().execute("""
            SELECT i.*,
                   fc.nome AS nome_casa, ff.nome AS nome_fuori
            FROM incontri i
            JOIN fantasquadre fc ON fc.id = i.id_casa
            JOIN fantasquadre ff ON ff.id = i.id_fuori
            WHERE i.id_lega=? AND i.giornata=? AND i.anno=?
        """, (id_lega, giornata, anno)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # BILANCIO
    # ------------------------------------------------------------------

    def aggiungi_movimento(self, id_fsq: int, descrizione: str,
                           valore: int) -> None:
        with self.transazione() as conn:
            conn.execute(
                "INSERT INTO bilanci(id_fantasquadra,descrizione,valore)"
                " VALUES(?,?,?)",
                (id_fsq, descrizione, valore)
            )

    def bilancio_fantasquadra(self, id_fsq: int) -> list[dict]:
        rows = self.connetti().execute(
            "SELECT * FROM bilanci WHERE id_fantasquadra=? ORDER BY data,id",
            (id_fsq,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # REGOLE PUNTEGGIO
    # ------------------------------------------------------------------

    def get_regole(self, id_lega: int) -> dict:
        row = self.connetti().execute(
            "SELECT * FROM regole_punteggio WHERE id_lega=?", (id_lega,)
        ).fetchone()
        return dict(row) if row else {}

    def salva_regole(self, id_lega: int, regole: dict) -> None:
        cols = ", ".join(f"{k}=?" for k in regole)
        vals = list(regole.values()) + [id_lega]
        with self.transazione() as conn:
            conn.execute(
                f"UPDATE regole_punteggio SET {cols} WHERE id_lega=?", vals
            )


class PlayerRole(enum.IntEnum):
    GOALKEEPER = 1
    DEFENDER = 2
    MIDFIELDER = 3
    FORWARD = 4

    @classmethod
    def _missing_(cls, value: object) -> "PlayerRole | None":
        return None

    def label(self) -> str:
        return {
            PlayerRole.GOALKEEPER: "P",
            PlayerRole.DEFENDER: "D",
            PlayerRole.MIDFIELDER: "C",
            PlayerRole.FORWARD: "A",
        }[self]


@dataclass
class Team:
    id: int
    name: str
    point_penalty: int = 0


@dataclass
class Player:
    id: int
    gazza_code: int
    role: PlayerRole | None
    gazza_cost: int
    name: str
    birthplace: str
    birthdate: date | None
    height: int
    weight: int
    foreign: bool = False
    team_id: int | None = None

    @property
    def team(self) -> str | None:
        return None  # resolved by PlayersDatabase.players_with_teams()


@dataclass
class HistoricalStat:
    player_id: int
    year: int
    avg_vote: float
    fanta_avg: float
    appearances: int
    goals: int
    assists: int


@dataclass
class PlayersDatabase:
    fca_path: Path
    teams: dict[int, Team] = field(default_factory=dict)
    players: dict[int, Player] = field(default_factory=dict)
    # IDGiocatore -> IDSquadra for the current season (giornata 1)
    player_team: dict[int, int] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_fca(cls, fca_path: str | Path) -> "PlayersDatabase":
        """Load a database from a .fca file on disk."""
        db = cls(fca_path=Path(fca_path))
        db._load()
        return db

    @classmethod
    def from_zip(cls, zip_path: str | Path, extract_to: str | Path | None = None) -> "PlayersDatabase":
        """Extract a zip archive and load the first .fca file found inside."""
        extract_dir = extract_archive(zip_path, extract_to)
        fca = find_fca_file(extract_dir)
        return cls.from_fca(fca)

    # ------------------------------------------------------------------
    # Internal loading
    # ------------------------------------------------------------------

    def _export(self, table: str) -> list[dict[str, str]]:
        result = subprocess.run(
            ["mdb-export", str(self.fca_path), table],
            capture_output=True,
            text=True,
            check=True,
        )
        reader = csv.DictReader(io.StringIO(result.stdout))
        return list(reader)

    def _load(self) -> None:
        self._load_teams()
        self._load_players()
        self._load_player_teams()

    def _load_teams(self) -> None:
        for row in self._export("SquadraDiA"):
            self.teams[int(row["ID"])] = Team(
                id=int(row["ID"]),
                name=row["Nome"],
                point_penalty=int(row.get("Penalizzazione") or 0),
            )

    def _load_players(self) -> None:
        for row in self._export("GiocatoreA"):
            birthdate: date | None = None
            raw_date = row.get("DataDiNascita", "").strip()
            if raw_date and raw_date != "-":
                try:
                    # mdb-export format: MM/DD/YY HH:MM:SS
                    from datetime import datetime
                    birthdate = datetime.strptime(raw_date, "%m/%d/%y %H:%M:%S").date()
                except ValueError:
                    pass

            role_val = int(row.get("Ruolo") or 0)
            self.players[int(row["ID"])] = Player(
                id=int(row["ID"]),
                gazza_code=int(row.get("CodiceGazza") or 0),
                role=PlayerRole(role_val) if role_val else None,
                gazza_cost=int(row.get("CostoGazza") or 0),
                name=row["Nome"],
                birthplace=row.get("LuogoDiNascita", "").strip() or "",
                birthdate=birthdate,
                height=int(row.get("Altezza") or 0),
                weight=int(row.get("Peso") or 0),
                foreign=bool(int(row.get("Extracom") or 0)),
            )

    def _load_player_teams(self) -> None:
        """Load player–team assignments from the GiocaIn table (giornata 1)."""
        seen: set[int] = set()
        for row in self._export("GiocaIn"):
            pid = int(row["IDGiocatore"])
            if pid not in seen:
                self.player_team[pid] = int(row["IDSquadra"])
                seen.add(pid)
        for pid, tid in self.player_team.items():
            if pid in self.players:
                self.players[pid].team_id = tid

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_player(self, player_id: int) -> Player | None:
        return self.players.get(player_id)

    def get_team(self, team_id: int) -> Team | None:
        return self.teams.get(team_id)

    def players_by_role(self, role: PlayerRole) -> list[Player]:
        return [p for p in self.players.values() if p.role == role]

    def players_by_team(self, team_id: int) -> list[Player]:
        return [p for p in self.players.values() if p.team_id == team_id]

    def search_players(self, query: str) -> list[Player]:
        q = query.lower()
        return [p for p in self.players.values() if q in p.name.lower()]

    def history(self) -> list[HistoricalStat]:
        stats: list[HistoricalStat] = []
        for row in self._export("Storico"):
            if not row.get("IDGiocatore"):
                continue
            def _f(key: str) -> float:
                try:
                    return float(row.get(key) or 0)
                except ValueError:
                    return 0.0

            def _i(key: str) -> int:
                try:
                    return int(float(row.get(key) or 0))
                except ValueError:
                    return 0

            # mdb-export exports up to 3 vote columns; use column 1 (Gazzetta)
            stats.append(HistoricalStat(
                player_id=_i("IDGiocatore"),
                year=_i("Anno"),
                avg_vote=_f("MediaVoto1"),
                fanta_avg=_f("FantaMedia1"),
                appearances=_i("Presenze"),
                goals=_i("GolFatti"),
                assists=_i("Assist"),
            ))
        return stats
