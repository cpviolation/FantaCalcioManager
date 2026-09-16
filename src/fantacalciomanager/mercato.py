"""
Fantacalcio Manager - Gestione mercato e aste
Replica le funzionalità di:
  - Asta iniziale (draft all'italiana con rilanci)
  - Calciomercato invernale (scambi, svincoli, acquisti dal mercato libero)
  - Registro movimenti bilancio
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .database import Database
from .models import (
    Giocatore, Fantasquadra, Rosa,
    StatoGiocatore, CONTRATTO_INFORTUNATO, CONTRATTO_PRESTITO,
)


# ---------------------------------------------------------------------------
# Eccezioni
# ---------------------------------------------------------------------------

class MercatoError(Exception):
    """Errore generico di mercato."""


class CreditiInsufficienti(MercatoError):
    pass


class GiocatoreGiaInRosa(MercatoError):
    pass


class GiocatoreNonInRosa(MercatoError):
    pass


class RosaCompleta(MercatoError):
    """La rosa ha già raggiunto il numero massimo di giocatori."""


# ---------------------------------------------------------------------------
# Risultato di un'operazione di mercato
# ---------------------------------------------------------------------------

@dataclass
class OperazioneMercato:
    tipo: str             # "acquisto", "cessione", "scambio", "svincolo"
    id_fantasquadra: int
    id_giocatore: int
    crediti: int = 0      # positivo = incasso, negativo = spesa
    note: str = ""
    data: date = field(default_factory=date.today)


# ---------------------------------------------------------------------------
# Motore mercato
# ---------------------------------------------------------------------------

class Mercato:
    """
    Gestisce tutte le operazioni di mercato di una lega.

    Regole FCM:
    - Ogni fantasquadra ha un budget di crediti_iniziali (default 500)
    - Un giocatore acquistato ha un prezzo_acquisto >= 1
    - Lo svincolo restituisce crediti (prezzo_svincolo, spesso 0)
    - Il mercato invernale permette scambi fra squadre
    """

    ROSA_MAX = 25   # dimensione massima rosa (configurabile)

    def __init__(self, db: Database, id_lega: int, anno: int,
                 rosa_max: int = 25):
        self.db = db
        self.id_lega = id_lega
        self.anno = anno
        self.rosa_max = rosa_max

    # ------------------------------------------------------------------
    # Acquisto
    # ------------------------------------------------------------------

    def acquista(
        self,
        id_fantasquadra: int,
        id_giocatore: int,
        prezzo: int,
        contratto: int = 1,
        crediti_svincolo: int = 0,
    ) -> OperazioneMercato:
        """
        Tesserare un giocatore libero ad una fantasquadra.

        Parameters
        ----------
        id_fantasquadra : int
        id_giocatore    : int
        prezzo          : int  (crediti pagati, >= 1)
        contratto       : int  (anni, -1 = illimitato, -2 = prestito)
        crediti_svincolo: int  (da incassare allo svincolo)

        Raises
        ------
        CreditiInsufficienti     se la squadra non ha abbastanza budget
        GiocatoreGiaInRosa       se il giocatore è già tesserato
        RosaCompleta             se la rosa è piena
        """
        if prezzo < 1:
            raise MercatoError("Il prezzo di acquisto deve essere almeno 1 credito.")

        self._verifica_libero(id_giocatore)
        self._verifica_crediti(id_fantasquadra, prezzo)
        self._verifica_spazio_rosa(id_fantasquadra)

        # Recupera nome per la descrizione bilancio
        conn0 = self.db.connetti()
        row = conn0.execute(
            "SELECT nome FROM giocatori WHERE id=?", (id_giocatore,)
        ).fetchone()
        nome_g = row["nome"] if row else str(id_giocatore)

        # Unica transazione: rose + crediti + bilancio
        with self.db.transazione() as conn:
            conn.execute(
                """INSERT INTO rose
                   (id_fantasquadra, id_giocatore, stato, contratto,
                    prezzo_acquisto, prezzo_svincolo)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (id_fantasquadra, id_giocatore,
                 StatoGiocatore.ROSA.value, contratto, prezzo, crediti_svincolo),
            )
            conn.execute(
                "UPDATE fantasquadre SET crediti_residui=crediti_residui-? WHERE id=?",
                (prezzo, id_fantasquadra),
            )
            conn.execute(
                """INSERT INTO bilanci
                   (id_fantasquadra, descrizione, valore, data)
                   VALUES (?, ?, ?, date('now'))""",
                (id_fantasquadra, f"Acquisto: {nome_g}", -prezzo),
            )

        return OperazioneMercato(
            tipo="acquisto",
            id_fantasquadra=id_fantasquadra,
            id_giocatore=id_giocatore,
            crediti=-prezzo,
        )

    # ------------------------------------------------------------------
    # Svincolo
    # ------------------------------------------------------------------

    def svincola(
        self,
        id_fantasquadra: int,
        id_giocatore: int,
        crediti_incassati: Optional[int] = None,
    ) -> OperazioneMercato:
        """
        Svincola un giocatore dalla fantasquadra.
        Se crediti_incassati è None usa il prezzo_svincolo registrato.
        """
        conn = self.db.connetti()
        row = conn.execute(
            """SELECT * FROM rose
               WHERE id_fantasquadra=? AND id_giocatore=?
               AND stato=?""",
            (id_fantasquadra, id_giocatore, StatoGiocatore.ROSA.value),
        ).fetchone()
        if not row:
            raise GiocatoreNonInRosa(
                f"Giocatore {id_giocatore} non in rosa di {id_fantasquadra}."
            )

        incasso = crediti_incassati if crediti_incassati is not None \
            else row["prezzo_svincolo"]

        conn.execute(
            """UPDATE rose SET stato=?
               WHERE id_fantasquadra=? AND id_giocatore=?""",
            (StatoGiocatore.SVINCOLATO.value, id_fantasquadra, id_giocatore),
        )
        conn.commit()

        self._aggiorna_crediti(id_fantasquadra, incasso)
        self._registra_movimento(
            id_fantasquadra, id_giocatore, incasso, "Svincolo"
        )

        return OperazioneMercato(
            tipo="svincolo",
            id_fantasquadra=id_fantasquadra,
            id_giocatore=id_giocatore,
            crediti=incasso,
        )

    # ------------------------------------------------------------------
    # Scambio fra due fantasquadre
    # ------------------------------------------------------------------

    def scambia(
        self,
        id_sq_a: int,
        id_giocatore_a: int,
        id_sq_b: int,
        id_giocatore_b: int,
        conguaglio: int = 0,
    ) -> list[OperazioneMercato]:
        """
        Scambia due giocatori fra fantasquadre.
        conguaglio > 0: la squadra B paga la differenza alla A.
        conguaglio < 0: la squadra A paga la differenza alla B.
        """
        self._verifica_in_rosa(id_sq_a, id_giocatore_a)
        self._verifica_in_rosa(id_sq_b, id_giocatore_b)

        if conguaglio > 0:
            self._verifica_crediti(id_sq_b, conguaglio)
        elif conguaglio < 0:
            self._verifica_crediti(id_sq_a, abs(conguaglio))

        conn = self.db.connetti()
        # Trasferisci giocatore A → B
        conn.execute(
            """UPDATE rose SET id_fantasquadra=?
               WHERE id_fantasquadra=? AND id_giocatore=? AND stato=?""",
            (id_sq_b, id_sq_a, id_giocatore_a, StatoGiocatore.ROSA.value),
        )
        # Trasferisci giocatore B → A
        conn.execute(
            """UPDATE rose SET id_fantasquadra=?
               WHERE id_fantasquadra=? AND id_giocatore=? AND stato=?""",
            (id_sq_a, id_sq_b, id_giocatore_b, StatoGiocatore.ROSA.value),
        )
        conn.commit()

        ops = []
        if conguaglio != 0:
            paga, incassa = (id_sq_b, id_sq_a) if conguaglio > 0 \
                else (id_sq_a, id_sq_b)
            self._aggiorna_crediti(paga, -abs(conguaglio))
            self._aggiorna_crediti(incassa, abs(conguaglio))
            self._registra_movimento(
                paga, id_giocatore_a, -abs(conguaglio), "Conguaglio scambio"
            )
            self._registra_movimento(
                incassa, id_giocatore_b, abs(conguaglio), "Conguaglio scambio"
            )
            ops.append(OperazioneMercato("scambio", paga, id_giocatore_a,
                                         crediti=-abs(conguaglio)))
            ops.append(OperazioneMercato("scambio", incassa, id_giocatore_b,
                                         crediti=abs(conguaglio)))
        else:
            ops.append(OperazioneMercato("scambio", id_sq_a, id_giocatore_a))
            ops.append(OperazioneMercato("scambio", id_sq_b, id_giocatore_b))

        return ops

    # ------------------------------------------------------------------
    # Segnalazione infortunio
    # ------------------------------------------------------------------

    def segna_infortunio(
        self,
        id_fantasquadra: int,
        id_giocatore: int,
        giornate_stop: int,
    ) -> None:
        """Segna un giocatore come infortunato per N giornate."""
        self._verifica_in_rosa(id_fantasquadra, id_giocatore)
        conn = self.db.connetti()
        conn.execute(
            """UPDATE rose
               SET stato=?, giornate_infortunio=?
               WHERE id_fantasquadra=? AND id_giocatore=? AND stato=?""",
            (StatoGiocatore.INFORTUNATO.value, giornate_stop,
             id_fantasquadra, id_giocatore, StatoGiocatore.ROSA.value),
        )
        conn.commit()

    def recupera_infortunio(
        self,
        id_fantasquadra: int,
        id_giocatore: int,
    ) -> None:
        """Riporta un giocatore infortunato in rosa attiva."""
        conn = self.db.connetti()
        conn.execute(
            """UPDATE rose
               SET stato=?, giornate_infortunio=0
               WHERE id_fantasquadra=? AND id_giocatore=? AND stato=?""",
            (StatoGiocatore.ROSA.value,
             id_fantasquadra, id_giocatore, StatoGiocatore.INFORTUNATO.value),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Riepilogo budget
    # ------------------------------------------------------------------

    def riepilogo_budget(self, id_fantasquadra: int) -> dict:
        """
        Restituisce un dict con il budget attuale della fantasquadra.
        """
        conn = self.db.connetti()
        sq = conn.execute(
            "SELECT * FROM fantasquadre WHERE id=?", (id_fantasquadra,)
        ).fetchone()
        if not sq:
            raise MercatoError(f"Fantasquadra {id_fantasquadra} non trovata.")

        # Costo rosa attuale
        row = conn.execute(
            """SELECT SUM(prezzo_acquisto) as totale, COUNT(*) as n
               FROM rose
               WHERE id_fantasquadra=? AND stato IN (0, 2)""",
            (id_fantasquadra,),
        ).fetchone()

        speso = row["totale"] or 0
        giocatori = row["n"] or 0
        crediti_residui = sq["crediti_residui"]

        return {
            "id_fantasquadra": id_fantasquadra,
            "nome": sq["nome"],
            "crediti_residui": crediti_residui,
            "crediti_spesi": speso,
            "giocatori_in_rosa": giocatori,
            "spazio_disponibile": self.rosa_max - giocatori,
        }

    # ------------------------------------------------------------------
    # Asta: lotto e aggiudicazione
    # ------------------------------------------------------------------

    def aggiudica_lotto(
        self,
        id_fantasquadra: int,
        id_giocatore: int,
        prezzo_aggiudicazione: int,
    ) -> OperazioneMercato:
        """
        Aggiudica un lotto d'asta (acquisto immediato al prezzo battuto).
        Alias di acquista() con descrizione specifica.
        """
        op = self.acquista(id_fantasquadra, id_giocatore,
                           prezzo_aggiudicazione, contratto=1)
        op.tipo = "asta"
        op.note = f"Aggiudicato a {prezzo_aggiudicazione} crediti"
        return op

    # ------------------------------------------------------------------
    # Mercato libero con crediti extra
    # ------------------------------------------------------------------

    def acquista_mercato_libero(
        self,
        id_fantasquadra: int,
        id_giocatore: int,
    ) -> OperazioneMercato:
        """
        Acquisto dal mercato libero a prezzo fisso (1 credito).
        Usa i crediti_mercato_libero della lega se configurati.
        """
        conn = self.db.connetti()
        lega = conn.execute(
            "SELECT * FROM leghe WHERE id=?", (self.id_lega,)
        ).fetchone()
        costo = 1
        if lega and lega["crediti_mercato_libero"] > 0:
            # Usa il fondo mercato libero (non i crediti normali)
            self._verifica_libero(id_giocatore)
            self._verifica_spazio_rosa(id_fantasquadra)
            conn.execute(
                """INSERT INTO rose
                   (id_fantasquadra, id_giocatore, stato, contratto,
                    prezzo_acquisto, prezzo_svincolo)
                   VALUES (?, ?, ?, 1, 1, 0)""",
                (id_fantasquadra, id_giocatore, StatoGiocatore.ROSA.value),
            )
            conn.commit()
            return OperazioneMercato(
                tipo="mercato_libero",
                id_fantasquadra=id_fantasquadra,
                id_giocatore=id_giocatore,
                crediti=0,
                note="Acquisto mercato libero (fondo extra)",
            )

        return self.acquista(id_fantasquadra, id_giocatore, costo)

    # ------------------------------------------------------------------
    # Helpers privati
    # ------------------------------------------------------------------

    def _verifica_libero(self, id_giocatore: int) -> None:
        conn = self.db.connetti()
        row = conn.execute(
            """SELECT id FROM rose
               WHERE id_giocatore=? AND stato IN (0, 2)
               AND id_fantasquadra IN (
                   SELECT id FROM fantasquadre WHERE id_lega=?
               )""",
            (id_giocatore, self.id_lega),
        ).fetchone()
        if row:
            raise GiocatoreGiaInRosa(
                f"Giocatore {id_giocatore} è già tesserato in questa lega."
            )

    def _verifica_in_rosa(self, id_fantasquadra: int,
                           id_giocatore: int) -> None:
        conn = self.db.connetti()
        row = conn.execute(
            """SELECT id FROM rose
               WHERE id_fantasquadra=? AND id_giocatore=? AND stato=?""",
            (id_fantasquadra, id_giocatore, StatoGiocatore.ROSA.value),
        ).fetchone()
        if not row:
            raise GiocatoreNonInRosa(
                f"Giocatore {id_giocatore} non in rosa di {id_fantasquadra}."
            )

    def _verifica_crediti(self, id_fantasquadra: int, costo: int) -> None:
        conn = self.db.connetti()
        sq = conn.execute(
            "SELECT crediti_residui FROM fantasquadre WHERE id=?",
            (id_fantasquadra,),
        ).fetchone()
        if not sq or sq["crediti_residui"] < costo:
            disponibili = sq["crediti_residui"] if sq else 0
            raise CreditiInsufficienti(
                f"Crediti insufficienti: servono {costo}, "
                f"disponibili {disponibili}."
            )

    def _verifica_spazio_rosa(self, id_fantasquadra: int) -> None:
        conn = self.db.connetti()
        row = conn.execute(
            """SELECT COUNT(*) as n FROM rose
               WHERE id_fantasquadra=? AND stato IN (0, 2)""",
            (id_fantasquadra,),
        ).fetchone()
        if row and row["n"] >= self.rosa_max:
            raise RosaCompleta(
                f"Rosa completa ({self.rosa_max} giocatori)."
            )

    def _aggiorna_crediti(self, id_fantasquadra: int, delta: int) -> None:
        conn = self.db.connetti()
        conn.execute(
            "UPDATE fantasquadre SET crediti_residui = crediti_residui + ? WHERE id=?",
            (delta, id_fantasquadra),
        )
        conn.commit()

    def _registra_movimento(
        self,
        id_fantasquadra: int,
        id_giocatore: int,
        valore: int,
        descrizione: str,
    ) -> None:
        conn = self.db.connetti()
        # Recupera nome giocatore per la descrizione
        row = conn.execute(
            "SELECT nome FROM giocatori WHERE id=?", (id_giocatore,)
        ).fetchone()
        nome = row["nome"] if row else str(id_giocatore)
        desc = f"{descrizione}: {nome}"
        conn.execute(
            """INSERT INTO bilanci
               (id_fantasquadra, descrizione, valore, data)
               VALUES (?, ?, ?, date('now'))""",
            (id_fantasquadra, desc, valore),
        )
        conn.commit()
