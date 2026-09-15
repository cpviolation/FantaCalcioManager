"""
Fantacalcio Manager - Motore di calcolo punteggi
Replica la logica di FCM 8.5.0 per:
  - Fantapunti della singola giornata
  - Calcolo formazione con sostituzioni automatiche
  - Gol di squadra e risultato virtuale
  - Statistiche aggregate (Fantamedia, MediaVoto, Andamento, ...)
"""
from __future__ import annotations
import math
import statistics
from typing import Optional

from .models import Ruolo, RegolePunteggio, VotoGiornata


# ---------------------------------------------------------------------------
# Calcolo fantapunti singolo giocatore
# ---------------------------------------------------------------------------

def calcola_fantapunti(
    voto: float,
    ruolo: Ruolo,
    gol_fatti: int = 0,
    gol_fatti_su_rigore: int = 0,
    gol_subiti: int = 0,
    gol_subiti_su_rigore: int = 0,
    assist: int = 0,
    autogol: int = 0,
    ammonizioni: int = 0,
    espulsioni: int = 0,
    rigori_parati: int = 0,
    rigori_sbagliati: int = 0,
    regole: Optional[RegolePunteggio] = None,
) -> float:
    """
    Calcola i fantapunti di un giocatore in una giornata.
    Il voto base (media voto) viene modificato da bonus e malus.
    Restituisce 0.0 se il giocatore non ha giocato (voto==0).
    """
    if voto == 0:
        return 0.0

    if regole is None:
        regole = RegolePunteggio()

    punti = voto

    # Gol fatti
    if gol_fatti > 0:
        punti += gol_fatti * regole.bonus_gol(ruolo)

    # Assist
    if assist > 0:
        punti += assist * regole.bonus_assist

    # Autogol
    if autogol > 0:
        punti -= autogol * regole.malus_autogol

    # Ammonizioni
    if ammonizioni > 0:
        punti -= ammonizioni * regole.malus_ammonizione

    # Espulsioni
    if espulsioni > 0:
        punti -= espulsioni * regole.malus_espulsione

    # Rigori parati (portieri)
    if rigori_parati > 0 and ruolo == Ruolo.PORTIERE:
        punti += rigori_parati * regole.bonus_rigore_parato

    # Rigori sbagliati
    if rigori_sbagliati > 0:
        punti -= rigori_sbagliati * regole.malus_rigore_sbagliato

    # Gol subiti (portieri): solo i gol non su rigore contano
    if ruolo == Ruolo.PORTIERE and gol_subiti > 0:
        gol_non_rigore = max(0, gol_subiti - gol_subiti_su_rigore)
        punti -= gol_non_rigore * regole.malus_gol_subito_portiere

    return round(punti, 2)


# ---------------------------------------------------------------------------
# Calcolo formazione completa con sostituzioni automatiche
# ---------------------------------------------------------------------------

class RisultatoFormazione:
    """Risultato del calcolo punteggio di una formazione."""

    def __init__(self):
        self.punteggio_totale: float = 0.0
        self.gol_segnati: int = 0
        self.titolari_utilizzati: list[dict] = []
        self.sostituzioni: list[dict] = []
        self.panchina_non_utilizzata: list[dict] = []
        self.note: list[str] = []

    def __repr__(self) -> str:
        return (
            f"RisultatoFormazione(punti={self.punteggio_totale:.2f}, "
            f"gol={self.gol_segnati})"
        )


def calcola_formazione(
    titolari: list[dict],
    panchina: list[dict],
    regole: Optional[RegolePunteggio] = None,
    max_sostituzioni: int = 3,
) -> RisultatoFormazione:
    """
    Calcola il punteggio totale di una formazione con sostituzioni automatiche.

    Parametri
    ---------
    titolari : lista di dict con chiavi:
        id_giocatore, nome, ruolo (int), fantapunti, ha_giocato
    panchina : stessa struttura, ordinata per priorità di sostituzione
    regole : RegolePunteggio (default: regole Gazzetta)
    max_sostituzioni : numero massimo di sostituzioni (default 3)

    La sostituzione avviene se:
    - Il titolare non ha giocato (ha_giocato=False, fantapunti=0)
    - Il sostituto ha giocato e ha lo stesso ruolo (o ruolo compatibile)
    - Non si supera max_sostituzioni e si mantiene minimo 1 portiere

    Restituisce RisultatoFormazione.
    """
    if regole is None:
        regole = RegolePunteggio()

    res = RisultatoFormazione()
    sostituzioni_effettuate = 0
    panchina_disponibile = list(panchina)  # copia

    lineup_finale: list[dict] = []

    for tit in titolari:
        if tit.get("ha_giocato") or tit.get("fantapunti", 0) > 0:
            lineup_finale.append({**tit, "sostituzione": False})
        else:
            # Cerca sostituto compatibile
            sostituto = None
            if sostituzioni_effettuate < max_sostituzioni:
                sostituto = _trova_sostituto(
                    tit, panchina_disponibile, lineup_finale
                )
            if sostituto:
                panchina_disponibile.remove(sostituto)
                sostituzioni_effettuate += 1
                res.sostituzioni.append({
                    "uscito": tit["nome"],
                    "entrato": sostituto["nome"],
                    "ruolo": Ruolo(sostituto["ruolo"]).nome_completo(),
                })
                lineup_finale.append({**sostituto, "sostituzione": True})
                res.note.append(
                    f"Sostituzione: {tit['nome']} → {sostituto['nome']}"
                )
            else:
                # Titolare assente, punteggio 0
                lineup_finale.append({**tit, "sostituzione": False, "fantapunti": 0})
                res.note.append(f"Assente: {tit['nome']} (0 punti)")

    # Somma punteggi
    res.titolari_utilizzati = lineup_finale
    res.punteggio_totale = sum(p.get("fantapunti", 0) for p in lineup_finale)
    res.gol_segnati = _punti_in_gol(res.punteggio_totale)
    res.panchina_non_utilizzata = panchina_disponibile

    return res


def _trova_sostituto(
    titolare: dict,
    panchina: list[dict],
    lineup_attuale: list[dict],
) -> Optional[dict]:
    """
    Trova il miglior sostituto dalla panchina rispettando:
    - Ha giocato (ha_giocato=True)
    - Stesso ruolo del titolare (regola semplificata)
    - Non si scende sotto 1 portiere in campo
    """
    ruolo_richiesto = titolare.get("ruolo")
    portieri_in_campo = sum(
        1 for p in lineup_attuale
        if p.get("ruolo") == Ruolo.PORTIERE.value
        and p.get("ha_giocato")
    )

    for sub in panchina:
        if not sub.get("ha_giocato"):
            continue
        # Il portiere di riserva entra solo se il portiere titolare è assente
        if (sub.get("ruolo") == Ruolo.PORTIERE.value
                and ruolo_richiesto != Ruolo.PORTIERE.value):
            continue
        # Non fare entrare un portiere se ce n'è già uno
        if (sub.get("ruolo") == Ruolo.PORTIERE.value
                and portieri_in_campo >= 1):
            continue
        if sub.get("ruolo") == ruolo_richiesto:
            return sub

    # Sostituzione di emergenza: qualsiasi giocatore che ha giocato
    # (solo per campi non portieri)
    if ruolo_richiesto != Ruolo.PORTIERE.value:
        for sub in panchina:
            if (sub.get("ha_giocato")
                    and sub.get("ruolo") != Ruolo.PORTIERE.value):
                return sub

    return None


def _punti_in_gol(punti: float) -> int:
    """
    Converte il punteggio totale in gol virtuale.
    Regola standard: ogni 6 punti sopra 60 → 1 gol.
    Esempio: 66 pt = 1 gol, 72 pt = 2 gol, ecc.
    """
    if punti < 60:
        return 0
    return int((punti - 60) // 6) + 1


def calcola_risultato_incontro(
    punti_casa: float,
    punti_fuori: float,
) -> tuple[int, int]:
    """
    Data la coppia di punteggi, restituisce il risultato in gol.
    Regola base: 66pt = 1 gol, 72pt = 2 gol, ...
    In caso di parità nei gol, si ha 0-0.
    """
    gol_casa = _punti_in_gol(punti_casa)
    gol_fuori = _punti_in_gol(punti_fuori)
    return gol_casa, gol_fuori


# ---------------------------------------------------------------------------
# Statistiche aggregate (Fantamedia, MediaVoto, Andamento, ...)
# ---------------------------------------------------------------------------

def calcola_statistiche_aggregate(
    voti: list[dict],
    giornate_totali: int = 38,
    n_andamento: int = 5,
) -> dict:
    """
    Calcola le statistiche aggregate per un giocatore partendo dalla lista
    dei suoi voti (una riga per giornata).

    Parametri
    ---------
    voti : lista di dict (dalle righe di voti_giornata)
    giornate_totali : numero totale di giornate della stagione
    n_andamento : ultime N giornate per calcolare l'andamento

    Restituisce un dict con tutti i campi di StatisticheGiocatore.
    """
    giocate = [v for v in voti if v.get("ha_giocato")]
    presenze = len(giocate)

    if presenze == 0:
        return _stats_vuote()

    # Totali
    gol_fatti            = sum(v.get("gol_fatti", 0) for v in giocate)
    gol_fatti_rigore     = sum(v.get("gol_fatti_su_rigore", 0) for v in giocate)
    gol_subiti           = sum(v.get("gol_subiti", 0) for v in giocate)
    gol_subiti_rigore    = sum(v.get("gol_subiti_su_rigore", 0) for v in giocate)
    assist               = sum(v.get("assist", 0) for v in giocate)
    autogol              = sum(v.get("autogol", 0) for v in giocate)
    ammonizioni          = sum(v.get("ammonizioni", 0) for v in giocate)
    espulsioni           = sum(v.get("espulsioni", 0) for v in giocate)
    rigori_parati        = sum(v.get("rigori_parati", 0) for v in giocate)
    rigori_sbagliati     = sum(v.get("rigori_sbagliati", 0) for v in giocate)

    # Affidabilità
    affidabilita = round(presenze / giornate_totali * 100, 1) if giornate_totali else 0

    # Medie
    voti_mv  = [v.get("voto", 0) for v in giocate]
    voti_fm  = [v.get("fantapunti", 0) for v in giocate]

    mv_totale  = round(statistics.mean(voti_mv), 2)
    fm_totale  = round(statistics.mean(voti_fm), 2)

    mv_dev_std = round(statistics.pstdev(voti_mv), 2) if len(voti_mv) > 1 else 0
    fm_dev_std = round(statistics.pstdev(voti_fm), 2) if len(voti_fm) > 1 else 0

    # Andamento: media ultime N giornate - media precedente
    # (positivo = in crescita, come in FCM)
    mv_andamento = _calcola_andamento(voti_mv, n_andamento)
    fm_andamento = _calcola_andamento(voti_fm, n_andamento)

    # Delta: ultimo voto - media complessiva
    mv_delta = round(voti_mv[-1] - mv_totale, 2) if voti_mv else 0
    fm_delta = round(voti_fm[-1] - fm_totale, 2) if voti_fm else 0

    return {
        "presenze":               presenze,
        "affidabilita":           affidabilita,
        "gol_fatti":              gol_fatti,
        "gol_fatti_su_rigore":    gol_fatti_rigore,
        "gol_subiti":             gol_subiti,
        "gol_subiti_su_rigore":   gol_subiti_rigore,
        "assist":                 assist,
        "autogol":                autogol,
        "ammonizioni":            ammonizioni,
        "espulsioni":             espulsioni,
        "rigori_parati":          rigori_parati,
        "rigori_sbagliati":       rigori_sbagliati,
        "mediavoto_totale":       mv_totale,
        "mediavoto_dev_std":      mv_dev_std,
        "mediavoto_delta":        mv_delta,
        "mediavoto_andamento":    mv_andamento,
        "fantamedia_totale":      fm_totale,
        "fantamedia_dev_std":     fm_dev_std,
        "fantamedia_delta":       fm_delta,
        "fantamedia_andamento":   fm_andamento,
    }


def _calcola_andamento(valori: list[float], n: int) -> float:
    """
    Andamento = media ultimi N - media precedenti.
    Più è positivo, più il giocatore è in crescita.
    """
    if len(valori) <= n:
        return 0.0
    ultimi = valori[-n:]
    precedenti = valori[:-n]
    media_ultimi = statistics.mean(ultimi)
    media_prec   = statistics.mean(precedenti)
    return round(media_ultimi - media_prec, 2)


def _stats_vuote() -> dict:
    return {
        "presenze": 0, "affidabilita": 0,
        "gol_fatti": 0, "gol_fatti_su_rigore": 0,
        "gol_subiti": 0, "gol_subiti_su_rigore": 0,
        "assist": 0, "autogol": 0, "ammonizioni": 0, "espulsioni": 0,
        "rigori_parati": 0, "rigori_sbagliati": 0,
        "mediavoto_totale": 0, "mediavoto_dev_std": 0,
        "mediavoto_delta": 0, "mediavoto_andamento": 0,
        "fantamedia_totale": 0, "fantamedia_dev_std": 0,
        "fantamedia_delta": 0, "fantamedia_andamento": 0,
    }


# ---------------------------------------------------------------------------
# Rendimento (Fantamedia / costo)
# ---------------------------------------------------------------------------

def calcola_rendimento(fantamedia: float, costo: int) -> float:
    """
    Rendimento = Fantamedia / Costo.
    Indica quanto rende ogni credito speso.
    """
    if costo <= 0:
        return 0.0
    return round(fantamedia / costo, 3)
