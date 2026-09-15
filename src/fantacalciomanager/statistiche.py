"""
Fantacalcio Manager - Motore statistiche e query
Replica le query .qry di FCM 8.5.0 in Python/pandas.

Le query originali operavano su una vista "Statistiche" (Access)
che aggregava i voti per stagione. Qui aggiungiamo la stessa
logica in Python, con la possibilità di filtrare per giornate.
"""
from __future__ import annotations
from typing import Optional

from .database import Database
from .calcoli import calcola_statistiche_aggregate, calcola_rendimento
from .models import Ruolo


class QueryStatistiche:
    """
    Esegui query statistiche sull'archivio voti, equivalenti alle
    query .qry di FCM (Classifica Cannonieri, Migliori Fantamedia, ecc.)
    """

    def __init__(self, db: Database, anno: int, id_lega: int,
                 giornata_da: int = 1, giornata_a: int = 38):
        self.db = db
        self.anno = anno
        self.id_lega = id_lega
        self.giornata_da = giornata_da
        self.giornata_a = giornata_a
        self._cache: dict[int, dict] = {}  # id_giocatore -> stats

    # ------------------------------------------------------------------
    # Costruzione tabella statistiche (su tutti i giocatori della lega)
    # ------------------------------------------------------------------

    def _stats_giocatore(self, id_giocatore: int) -> dict:
        if id_giocatore in self._cache:
            return self._cache[id_giocatore]

        voti = [
            v for v in self.db.voti_giocatore(id_giocatore, self.anno)
            if self.giornata_da <= v["giornata"] <= self.giornata_a
        ]
        s = calcola_statistiche_aggregate(voti, giornate_totali=38)
        self._cache[id_giocatore] = s
        return s

    def _tutti_i_giocatori_lega(self) -> list[dict]:
        """
        Recupera tutti i giocatori presenti in una fantasquadra della lega
        (o anche i liberi che hanno voti).
        """
        conn = self.db.connetti()
        rows = conn.execute("""
            SELECT DISTINCT g.id, g.nome, g.squadra, g.ruolo,
                            g.costo_iniziale,
                            r.id_fantasquadra,
                            r.prezzo_acquisto
            FROM giocatori g
            LEFT JOIN rose r ON r.id_giocatore = g.id
                AND r.id_fantasquadra IN (
                    SELECT id FROM fantasquadre WHERE id_lega=?
                )
            WHERE g.id IN (
                SELECT DISTINCT id_giocatore FROM voti_giornata
                WHERE anno=? AND giornata BETWEEN ? AND ?
            )
            ORDER BY g.nome
        """, (self.id_lega, self.anno,
              self.giornata_da, self.giornata_a)).fetchall()
        return [dict(r) for r in rows]

    def build_statistiche(self) -> list[dict]:
        """
        Costruisce la tabella completa statistiche (equivalente alla
        vista 'Statistiche' di FCM).
        """
        risultati = []
        for g in self._tutti_i_giocatori_lega():
            s = self._stats_giocatore(g["id"])
            costo = g.get("prezzo_acquisto") or g.get("costo_iniziale") or 1
            s["rendimento_mv"] = calcola_rendimento(s["mediavoto_totale"], costo)
            s["rendimento_fm"] = calcola_rendimento(s["fantamedia_totale"], costo)
            s["id"] = g["id"]
            s["nome"] = g["nome"]
            s["squadra"] = g["squadra"]
            s["ruolo"] = g["ruolo"]
            s["costo"] = costo
            s["id_fantasquadra"] = g.get("id_fantasquadra") or 0
            risultati.append(s)
        return risultati

    # ------------------------------------------------------------------
    # Query equivalenti ai .qry di FCM
    # ------------------------------------------------------------------

    def classifica_cannonieri(self, top: int = 0) -> list[dict]:
        """
        SELECT ... WHERE GolFatti > 0 ORDER BY GolFatti DESC, Nome
        """
        rows = [r for r in self.build_statistiche() if r["gol_fatti"] > 0]
        rows.sort(key=lambda r: (-r["gol_fatti"], r["nome"]))
        return rows[:top] if top else rows

    def classifica_fairplay(self, top: int = 3) -> list[dict]:
        """
        SELECT ... WHERE Amm>=1 OR Esp>=1 ORDER BY Esp DESC, Amm DESC
        (peggiori: più espulsioni/ammonizioni)
        """
        rows = [r for r in self.build_statistiche()
                if r["ammonizioni"] >= 1 or r["espulsioni"] >= 1]
        rows.sort(key=lambda r: (-r["espulsioni"], -r["ammonizioni"]))
        return rows[:top] if top else rows

    def migliori_fantamedia(self, affidabilita_min: float = 25,
                            ruolo: Optional[Ruolo] = None,
                            liberi_da_contratto: bool = False) -> list[dict]:
        """
        WHERE Affidabilita>=25 ORDER BY FMT DESC, MVT DESC, Squadra
        """
        rows = [r for r in self.build_statistiche()
                if r["affidabilita"] >= affidabilita_min]
        if ruolo:
            rows = [r for r in rows if r["ruolo"] == int(ruolo)]
        if liberi_da_contratto:
            rows = [r for r in rows if r["id_fantasquadra"] == 0]
        rows.sort(key=lambda r: (-r["fantamedia_totale"],
                                 -r["mediavoto_totale"],
                                 r["squadra"]))
        return rows

    def migliori_mediavoto(self, affidabilita_min: float = 25,
                           ruolo: Optional[Ruolo] = None,
                           liberi_da_contratto: bool = False) -> list[dict]:
        rows = [r for r in self.build_statistiche()
                if r["affidabilita"] >= affidabilita_min]
        if ruolo:
            rows = [r for r in rows if r["ruolo"] == int(ruolo)]
        if liberi_da_contratto:
            rows = [r for r in rows if r["id_fantasquadra"] == 0]
        rows.sort(key=lambda r: (-r["mediavoto_totale"], r["squadra"]))
        return rows

    def giocatori_in_crescita(self, affidabilita_min: float = 20,
                              ruolo: Optional[Ruolo] = None) -> list[dict]:
        """
        WHERE Affidabilita>=20 ORDER BY MVAnd DESC, MVT DESC
        (andamento positivo = in crescita)
        """
        rows = [r for r in self.build_statistiche()
                if r["affidabilita"] >= affidabilita_min]
        if ruolo:
            rows = [r for r in rows if r["ruolo"] == int(ruolo)]
        rows.sort(key=lambda r: (-r["mediavoto_andamento"],
                                 -r["mediavoto_totale"]))
        return rows

    def difese_piu_forti(self) -> list[dict]:
        """
        WHERE Presenze>0 AND Ruolo=1 ORDER BY GolSubiti ASC
        (portieri meno battuti)
        """
        rows = [r for r in self.build_statistiche()
                if r["presenze"] > 0 and r["ruolo"] == Ruolo.PORTIERE.value]
        rows.sort(key=lambda r: r["gol_subiti"])
        return rows

    def pararigori(self) -> list[dict]:
        """
        WHERE RigPar > 0 ORDER BY RigPar DESC
        """
        rows = [r for r in self.build_statistiche() if r["rigori_parati"] > 0]
        rows.sort(key=lambda r: -r["rigori_parati"])
        return rows

    def rigoristi_mancati(self) -> list[dict]:
        """
        WHERE RigSba > 0 ORDER BY RigSba DESC
        """
        rows = [r for r in self.build_statistiche()
                if r["rigori_sbagliati"] > 0]
        rows.sort(key=lambda r: -r["rigori_sbagliati"])
        return rows

    def marcatori_autogol(self) -> list[dict]:
        """Autogolisti."""
        rows = [r for r in self.build_statistiche() if r["autogol"] > 0]
        rows.sort(key=lambda r: -r["autogol"])
        return rows

    def migliore_rendimento(self, costo_min: int = 1,
                            affidabilita_min: float = 20) -> list[dict]:
        """
        Giocatori con il miglior rendimento FM/costo.
        """
        rows = [r for r in self.build_statistiche()
                if r["affidabilita"] >= affidabilita_min
                and r["costo"] >= costo_min]
        rows.sort(key=lambda r: -r["rendimento_fm"])
        return rows

    def peggiori_nella_squadra(self) -> list[dict]:
        """
        Per ogni squadra reale, il giocatore con la fantamedia più bassa
        (che abbia giocato almeno una volta).
        """
        tutti = [r for r in self.build_statistiche() if r["presenze"] > 0]
        squadre: dict[str, list[dict]] = {}
        for r in tutti:
            squadre.setdefault(r["squadra"], []).append(r)
        peggiori = []
        for sq, giocatori in squadre.items():
            giocatori.sort(key=lambda r: r["fantamedia_totale"])
            peggiori.append(giocatori[0])
        peggiori.sort(key=lambda r: r["fantamedia_totale"])
        return peggiori

    def giocatori_liberi(self, ruolo: Optional[Ruolo] = None,
                         ordine: str = "nome") -> list[dict]:
        """
        Giocatori non tesserati in nessuna fantasquadra.
        """
        rows = [r for r in self.build_statistiche()
                if r["id_fantasquadra"] == 0]
        if ruolo:
            rows = [r for r in rows if r["ruolo"] == int(ruolo)]
        if ordine == "ruolo":
            rows.sort(key=lambda r: (r["ruolo"], r["nome"]))
        elif ordine == "squadra":
            rows.sort(key=lambda r: (r["squadra"], r["nome"]))
        else:
            rows.sort(key=lambda r: r["nome"])
        return rows

    def tutti_i_dati(self) -> list[dict]:
        """Equivalente di 'Tutti i dati.qry': tutti i giocatori con tutte le info."""
        rows = self.build_statistiche()
        rows.sort(key=lambda r: r["nome"])
        return rows


# ---------------------------------------------------------------------------
# Classifica di lega
# ---------------------------------------------------------------------------

class ClassificaLega:
    """Calcola e restituisce la classifica di una lega/divisione."""

    PUNTI_VITTORIA  = 3
    PUNTI_PAREGGIO  = 1
    PUNTI_SCONFITTA = 0

    def __init__(self, db: Database, id_lega: int, anno: int,
                 id_divisione: int = 0):
        self.db = db
        self.id_lega = id_lega
        self.anno = anno
        self.id_divisione = id_divisione

    def calcola(self) -> list[dict]:
        """
        Restituisce la classifica ordinata per punti, poi gol fatti.
        """
        fantasquadre = self.db.lista_fantasquadre(self.id_lega)
        if self.id_divisione:
            fantasquadre = [f for f in fantasquadre
                            if f.id_divisione == self.id_divisione]

        cls: dict[int, dict] = {}
        for f in fantasquadre:
            cls[f.id] = {
                "id": f.id,
                "nome": f.nome,
                "presidente": f.presidente,
                "pt": 0, "v": 0, "n": 0, "p": 0,
                "gf": 0, "gs": 0, "punteggio": 0.0,
                "giocate": 0,
            }

        # Scorri tutti gli incontri giocati
        conn = self.db.connetti()
        rows = conn.execute("""
            SELECT * FROM incontri
            WHERE id_lega=? AND anno=? AND giocata=1
        """, (self.id_lega, self.anno)).fetchall()

        for r in rows:
            id_c, id_f = r["id_casa"], r["id_fuori"]
            if id_c not in cls or id_f not in cls:
                continue
            gc, gf = r["gol_casa"], r["gol_fuori"]
            pc, pf = r["punti_casa"], r["punti_fuori"]

            cls[id_c]["giocate"] += 1
            cls[id_f]["giocate"] += 1
            cls[id_c]["gf"] += gc
            cls[id_c]["gs"] += gf
            cls[id_f]["gf"] += gf
            cls[id_f]["gs"] += gc
            cls[id_c]["punteggio"] += pc
            cls[id_f]["punteggio"] += pf

            if gc > gf:
                cls[id_c]["pt"] += self.PUNTI_VITTORIA
                cls[id_c]["v"]  += 1
                cls[id_f]["p"]  += 1
            elif gc < gf:
                cls[id_f]["pt"] += self.PUNTI_VITTORIA
                cls[id_f]["v"]  += 1
                cls[id_c]["p"]  += 1
            else:
                cls[id_c]["pt"] += self.PUNTI_PAREGGIO
                cls[id_f]["pt"] += self.PUNTI_PAREGGIO
                cls[id_c]["n"]  += 1
                cls[id_f]["n"]  += 1

        result = sorted(
            cls.values(),
            key=lambda x: (-x["pt"], -x["gf"] + x["gs"], -x["punteggio"]),
        )

        for i, r in enumerate(result, 1):
            r["pos"] = i
            r["dr"] = r["gf"] - r["gs"]

        return result
