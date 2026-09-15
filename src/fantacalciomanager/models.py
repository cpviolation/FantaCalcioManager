"""
Fantacalcio Manager - Modelli dati
Equivalente Python del database MS Access usato da FCM 8.5.0
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from enum import IntEnum
from typing import Optional


# ---------------------------------------------------------------------------
# Enum / costanti
# ---------------------------------------------------------------------------

class Ruolo(IntEnum):
    PORTIERE       = 1
    DIFENSORE      = 2
    CENTROCAMPISTA = 3
    ATTACCANTE     = 4

    def abbreviazione(self) -> str:
        return {1: "P", 2: "D", 3: "C", 4: "A"}[self.value]

    def nome_completo(self) -> str:
        return {1: "Portiere", 2: "Difensore",
                3: "Centrocampista", 4: "Attaccante"}[self.value]


class StatoGiocatore(IntEnum):
    ROSA        = 0   # in rosa
    SVINCOLATO  = 1   # ceduto / svincolato
    INFORTUNATO = 2   # lista infortunati


CONTRATTO_INFORTUNATO = -1
CONTRATTO_PRESTITO    = -2


# ---------------------------------------------------------------------------
# Lega
# ---------------------------------------------------------------------------

@dataclass
class Lega:
    id: int
    nome: str
    anno: int           # anno di inizio stagione (es. 2024 per 2024/25)
    stagione: int       # numero edizione
    crediti_iniziali: int = 500
    giornate_totali: int = 38
    crediti_mercato_libero: int = 0
    note: str = ""


# ---------------------------------------------------------------------------
# Divisione (girone)
# ---------------------------------------------------------------------------

@dataclass
class Divisione:
    id: int
    id_lega: int
    nome: str


# ---------------------------------------------------------------------------
# Fantasquadra
# ---------------------------------------------------------------------------

@dataclass
class Fantasquadra:
    id: int
    id_lega: int
    nome: str
    presidente: str
    email: str = ""
    telefono_casa: str = ""
    telefono_lavoro: str = ""
    cellulare: str = ""
    id_divisione: int = 0
    crediti_residui: int = 0


# ---------------------------------------------------------------------------
# Giocatore (Serie A)
# ---------------------------------------------------------------------------

@dataclass
class Giocatore:
    id: int
    nome: str
    squadra: str
    ruolo: Ruolo
    codice_gazzetta: str = ""   # codice usato dal quotidiano
    costo_iniziale: int = 1     # costo in crediti
    extracomunitario: bool = False


# ---------------------------------------------------------------------------
# Rosa (assegnazione giocatore a fantasquadra)
# ---------------------------------------------------------------------------

@dataclass
class Rosa:
    id: int
    id_fantasquadra: int
    id_giocatore: int
    stato: StatoGiocatore = StatoGiocatore.ROSA
    contratto: int = 1          # anni di contratto; -1=inf, -2=prestito
    prezzo_acquisto: int = 1    # crediti pagati all'asta
    prezzo_svincolo: int = 0    # crediti incassati alla cessione
    giornate_infortunio: int = 0  # numero giornate di stop


# ---------------------------------------------------------------------------
# Voto di una singola giornata per un giocatore
# ---------------------------------------------------------------------------

@dataclass
class VotoGiornata:
    id: int
    id_giocatore: int
    giornata: int
    anno: int                       # anno stagione
    ha_giocato: bool = False
    voto: float = 0.0               # voto base (es. 6.5)
    gol_fatti: int = 0
    gol_fatti_su_rigore: int = 0
    gol_subiti: int = 0
    gol_subiti_su_rigore: int = 0
    assist: int = 0
    autogol: int = 0
    ammonizioni: int = 0
    espulsioni: int = 0
    rigori_parati: int = 0
    rigori_sbagliati: int = 0
    fantapunti: float = 0.0         # calcolato


# ---------------------------------------------------------------------------
# Regole di calcolo punteggio (configurabili per lega)
# ---------------------------------------------------------------------------

@dataclass
class RegolePunteggio:
    """
    Bonus/malus configurabili per la lega.
    Default: regole classiche Gazzetta dello Sport.
    """
    # Bonus gol per ruolo
    gol_portiere:       float = 10.0
    gol_difensore:      float = 6.0
    gol_centrocampista: float = 4.5
    gol_attaccante:     float = 3.0
    # Altri bonus
    bonus_assist:       float = 1.0
    bonus_rigore_parato: float = 3.0
    # Malus
    malus_autogol:        float = 2.0
    malus_ammonizione:    float = 0.5
    malus_espulsione:     float = 1.0
    malus_rigore_sbagliato: float = 3.0
    # Gol subiti (portieri): malus per gol (non su rigore)
    malus_gol_subito_portiere: float = 1.0

    def bonus_gol(self, ruolo: Ruolo) -> float:
        return {
            Ruolo.PORTIERE:       self.gol_portiere,
            Ruolo.DIFENSORE:      self.gol_difensore,
            Ruolo.CENTROCAMPISTA: self.gol_centrocampista,
            Ruolo.ATTACCANTE:     self.gol_attaccante,
        }[ruolo]


# ---------------------------------------------------------------------------
# Formazione per una giornata
# ---------------------------------------------------------------------------

@dataclass
class Formazione:
    id: int
    id_fantasquadra: int
    giornata: int
    anno: int
    titolari: list[int] = field(default_factory=list)   # id_giocatore (11)
    panchina: list[int] = field(default_factory=list)   # id_giocatore (fino a 7)
    modulo: str = "4-3-3"
    note: str = ""


# ---------------------------------------------------------------------------
# Incontro (partita fantasy) tra due fantasquadre
# ---------------------------------------------------------------------------

@dataclass
class Incontro:
    id: int
    id_lega: int
    giornata: int
    anno: int
    id_casa: int
    id_fuori: int
    punti_casa: float = 0.0
    punti_fuori: float = 0.0
    gol_casa: int = 0
    gol_fuori: int = 0
    giocata: bool = False


# ---------------------------------------------------------------------------
# Bilancio (movimenti economici fantasquadra)
# ---------------------------------------------------------------------------

@dataclass
class MovimentoBilancio:
    id: int
    id_fantasquadra: int
    descrizione: str
    valore: int          # positivo=entrata, negativo=uscita
    data: date = field(default_factory=date.today)


# ---------------------------------------------------------------------------
# Statistiche aggregate per giocatore (calcolate, non persistite)
# ---------------------------------------------------------------------------

@dataclass
class StatisticheGiocatore:
    id_giocatore: int
    nome: str
    squadra: str
    ruolo: Ruolo
    anno: int
    # Medie e totali
    presenze: int = 0
    affidabilita: float = 0.0      # % presenze su giornate totali
    gol_fatti: int = 0
    gol_fatti_su_rigore: int = 0
    gol_subiti: int = 0
    gol_subiti_su_rigore: int = 0
    assist: int = 0
    autogol: int = 0
    ammonizioni: int = 0
    espulsioni: int = 0
    rigori_parati: int = 0
    rigori_sbagliati: int = 0
    # MediaVoto
    mediavoto_totale: float = 0.0
    mediavoto_casa: float = 0.0
    mediavoto_fuori: float = 0.0
    mediavoto_dev_std: float = 0.0
    mediavoto_delta: float = 0.0    # ultimo - media precedente
    mediavoto_andamento: float = 0.0  # trend ultime N giornate
    mediavoto_rendimento: float = 0.0  # media/costo
    # Fantamedia
    fantamedia_totale: float = 0.0
    fantamedia_casa: float = 0.0
    fantamedia_fuori: float = 0.0
    fantamedia_dev_std: float = 0.0
    fantamedia_delta: float = 0.0
    fantamedia_andamento: float = 0.0
    fantamedia_rendimento: float = 0.0
    # Costo (fantamiliardi)
    costo_gazzetta: int = 1
    # Tesseramento
    tesserato: bool = False
    id_fantasquadra: int = 0       # 0 se svincolato
