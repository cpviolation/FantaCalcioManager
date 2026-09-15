"""
fantacalciomanager.sync
=======================
Ponte tra il database ufficiale dell'ArchivioSerieA (PlayersDatabase)
e il database SQLite di gestione lega (Database).

Funzioni principali
-------------------
importa_giocatori(pdb, db)
    Copia/aggiorna tutti i giocatori e le squadre di Serie A nel db SQLite.

importa_storico(pdb, db)
    Importa le statistiche storiche (anno per anno) dalla tabella Storico del .fca.

crea_lega_da_archivio(pdb, db, nome, anno, squadre, crediti, giornate)
    Crea la lega, importa i giocatori e crea le fantasquadre in un'unica chiamata.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fantacalciomanager.database import Database, PlayersDatabase, PlayerRole
from fantacalciomanager.models import Ruolo


# ---------------------------------------------------------------------------
# Mappa PlayerRole → Ruolo (stessa codifica numerica, diversi enum)
# ---------------------------------------------------------------------------

_ROLE_MAP: dict[int, int] = {
    PlayerRole.GOALKEEPER.value: Ruolo.PORTIERE.value,
    PlayerRole.DEFENDER.value:   Ruolo.DIFENSORE.value,
    PlayerRole.MIDFIELDER.value: Ruolo.CENTROCAMPISTA.value,
    PlayerRole.FORWARD.value:    Ruolo.ATTACCANTE.value,
}


# ---------------------------------------------------------------------------
# Risultato importazione
# ---------------------------------------------------------------------------

@dataclass
class RisultatoImportazione:
    giocatori_inseriti: int = 0
    giocatori_aggiornati: int = 0
    giocatori_senza_ruolo: int = 0
    storico_inserito: int = 0

    def __str__(self) -> str:
        return (
            f"Giocatori inseriti:   {self.giocatori_inseriti}\n"
            f"Giocatori aggiornati: {self.giocatori_aggiornati}\n"
            f"Senza ruolo (skip):   {self.giocatori_senza_ruolo}\n"
            f"Record storici:       {self.storico_inserito}"
        )


# ---------------------------------------------------------------------------
# Importazione giocatori
# ---------------------------------------------------------------------------

def importa_giocatori(
    pdb: PlayersDatabase,
    db: Database,
) -> RisultatoImportazione:
    """
    Copia tutti i giocatori del PlayersDatabase nel db SQLite di FCM.

    - Se il giocatore esiste già (stesso codice Gazzetta) aggiorna squadra,
      ruolo e costo; non tocca i dati di tesseramento.
    - I giocatori senza ruolo valido vengono saltati.

    Parameters
    ----------
    pdb : PlayersDatabase
        Archivio caricato da .fca (es. PlayersDatabase.from_zip(...)).
    db  : Database
        Database SQLite della lega FCM.

    Returns
    -------
    RisultatoImportazione con i contatori dell'operazione.
    """
    res = RisultatoImportazione()
    conn = db.connetti()

    for player in pdb.players.values():
        if not player.role:
            res.giocatori_senza_ruolo += 1
            continue

        ruolo_fcm = _ROLE_MAP.get(player.role.value)
        if ruolo_fcm is None:
            res.giocatori_senza_ruolo += 1
            continue

        team = pdb.teams.get(player.team_id) if player.team_id else None
        squadra = team.name if team else "Sconosciuta"
        codice  = str(player.gazza_code) if player.gazza_code else ""
        costo   = max(1, player.gazza_cost)

        # Upsert basato sul codice Gazzetta (chiave naturale)
        existing = conn.execute(
            "SELECT id FROM giocatori WHERE codice_gazzetta = ? AND codice_gazzetta != ''",
            (codice,),
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE giocatori
                   SET nome=?, squadra=?, ruolo=?, costo_iniziale=?,
                       extracomunitario=?
                   WHERE id=?""",
                (player.name, squadra, ruolo_fcm, costo,
                 int(player.foreign), existing["id"]),
            )
            res.giocatori_aggiornati += 1
        else:
            conn.execute(
                """INSERT INTO giocatori
                   (nome, squadra, ruolo, codice_gazzetta, costo_iniziale,
                    extracomunitario)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (player.name, squadra, ruolo_fcm, codice, costo,
                 int(player.foreign)),
            )
            res.giocatori_inseriti += 1

    conn.commit()
    return res


# ---------------------------------------------------------------------------
# Importazione storico
# ---------------------------------------------------------------------------

def importa_storico(
    pdb: PlayersDatabase,
    db: Database,
) -> RisultatoImportazione:
    """
    Importa le statistiche storiche dal campo Storico del .fca come voti
    aggregati (una riga per stagione, giornata=0 per convenzione).

    Utile per pre-popolare le statistiche degli anni precedenti e usare
    QueryStatistiche.migliori_fantamedia() anche su stagioni passate.
    """
    res = RisultatoImportazione()
    conn = db.connetti()

    # Crea indice temporaneo codice Gazzetta → id nel nostro DB
    gazza_to_id: dict[str, int] = {}
    for row in conn.execute(
        "SELECT id, codice_gazzetta FROM giocatori WHERE codice_gazzetta != ''"
    ).fetchall():
        gazza_to_id[row["codice_gazzetta"]] = row["id"]

    for stat in pdb.history():
        # Trova il giocatore nel DB SQLite tramite il suo id nell'archivio
        player = pdb.players.get(stat.player_id)
        if not player:
            continue
        codice = str(player.gazza_code) if player.gazza_code else ""
        id_g = gazza_to_id.get(codice)
        if not id_g:
            continue

        # Salva come voti_giornata con giornata=0 (aggregato stagionale)
        # ed ha_giocato=True se il giocatore ha almeno una presenza.
        conn.execute(
            """INSERT INTO voti_giornata
               (id_giocatore, giornata, anno, ha_giocato, voto, fantapunti,
                gol_fatti, assist)
               VALUES (?, 0, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id_giocatore, giornata, anno) DO UPDATE SET
                 ha_giocato = excluded.ha_giocato,
                 voto       = excluded.voto,
                 fantapunti = excluded.fantapunti,
                 gol_fatti  = excluded.gol_fatti,
                 assist     = excluded.assist
            """,
            (
                id_g, stat.year,
                1 if stat.appearances > 0 else 0,
                stat.avg_vote,
                stat.fanta_avg,
                stat.goals,
                stat.assists,
            ),
        )
        res.storico_inserito += 1

    conn.commit()
    return res


# ---------------------------------------------------------------------------
# Setup lega completo
# ---------------------------------------------------------------------------

@dataclass
class ConfigLega:
    """
    Configurazione per crea_lega_da_archivio().

    Parameters
    ----------
    nome      : nome della lega
    anno      : anno di inizio stagione (es. 2024 per 2024/25)
    squadre   : lista di tuple (nome_squadra, nome_presidente[, email])
    crediti   : crediti iniziali per fantasquadra (default 500)
    giornate  : giornate totali della stagione (default 38)
    stagione  : numero d'edizione della lega (default 1)
    """
    nome: str
    anno: int
    squadre: list[tuple]        # (nome, presidente) o (nome, presidente, email)
    crediti: int = 500
    giornate: int = 38
    stagione: int = 1


@dataclass
class RisultatoSetup:
    id_lega: int
    id_fantasquadre: dict[str, int]   # nome squadra → id
    importazione: RisultatoImportazione

    def __str__(self) -> str:
        squadre = "\n".join(
            f"  [{sid}] {nome}"
            for nome, sid in self.id_fantasquadre.items()
        )
        return (
            f"Lega creata (id={self.id_lega})\n"
            f"Fantasquadre:\n{squadre}\n"
            f"Importazione:\n{self.importazione}"
        )


def crea_lega_da_archivio(
    pdb: PlayersDatabase,
    db: Database,
    config: ConfigLega,
    *,
    importa_storico_anni: bool = False,
) -> RisultatoSetup:
    """
    Crea la lega, importa i giocatori e registra le fantasquadre.

    Steps interni
    -------------
    1. Crea la lega nel database SQLite.
    2. Importa tutti i giocatori dell'ArchivioSerieA (``importa_giocatori``).
    3. Crea ciascuna fantasquadra con i crediti iniziali della lega.
    4. (Opzionale) Importa lo storico voti.

    Parameters
    ----------
    pdb    : PlayersDatabase caricato dall'archivio ufficiale.
    db     : Database SQLite FCM.
    config : ConfigLega con nome, anno, squadre e impostazioni.
    importa_storico_anni : se True importa anche i dati storici del .fca.

    Returns
    -------
    RisultatoSetup con gli id creati e i contatori di importazione.
    """
    # 1. Lega
    id_lega = db.crea_lega(
        nome=config.nome,
        anno=config.anno,
        stagione=config.stagione,
        crediti=config.crediti,
        giornate=config.giornate,
    )

    # 2. Giocatori
    res_imp = importa_giocatori(pdb, db)

    # 3. Fantasquadre
    id_fsq: dict[str, int] = {}
    for entry in config.squadre:
        nome_sq   = entry[0]
        presidente = entry[1] if len(entry) > 1 else ""
        email     = entry[2] if len(entry) > 2 else ""
        sid = db.crea_fantasquadra(id_lega, nome_sq, presidente, email)
        id_fsq[nome_sq] = sid

    # 4. Storico (opzionale)
    if importa_storico_anni:
        res_st = importa_storico(pdb, db)
        res_imp.storico_inserito = res_st.storico_inserito

    return RisultatoSetup(
        id_lega=id_lega,
        id_fantasquadre=id_fsq,
        importazione=res_imp,
    )
