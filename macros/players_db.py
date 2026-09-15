# A script to manage the players database
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fantacalciomanager.database import PlayersDatabase
from fantacalciomanager.web import download_season_archive

DATA_DIR = Path(__file__).parent.parent / "data"


def db_path(year: int) -> Path:
    return DATA_DIR / f"players_{year}.db"


def zip_path(year: int) -> Path:
    return DATA_DIR / f"ArchivioA{year}SerieA.zip"


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS teams (
            id              INTEGER PRIMARY KEY,
            name            TEXT NOT NULL,
            point_penalty   INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS players (
            id          INTEGER PRIMARY KEY,
            gazza_code  INTEGER,
            role        INTEGER,
            role_label  TEXT,
            gazza_cost  INTEGER,
            name        TEXT NOT NULL,
            birthplace  TEXT,
            birthdate   TEXT,
            height      INTEGER,
            weight      INTEGER,
            foreign_    INTEGER NOT NULL DEFAULT 0,
            team_id     INTEGER REFERENCES teams(id)
        );
    """)
    conn.commit()


def populate(conn: sqlite3.Connection, fcm_db: PlayersDatabase) -> None:
    conn.execute("DELETE FROM players")
    conn.execute("DELETE FROM teams")

    conn.executemany(
        "INSERT INTO teams (id, name, point_penalty) VALUES (?, ?, ?)",
        [(t.id, t.name, t.point_penalty) for t in fcm_db.teams.values()],
    )

    conn.executemany(
        """INSERT INTO players
               (id, gazza_code, role, role_label, gazza_cost, name,
                birthplace, birthdate, height, weight, foreign_, team_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                p.id,
                p.gazza_code,
                p.role.value if p.role else None,
                p.role.label() if p.role else None,
                p.gazza_cost,
                p.name,
                p.birthplace,
                p.birthdate.isoformat() if p.birthdate else None,
                p.height,
                p.weight,
                int(p.foreign),
                p.team_id,
            )
            for p in fcm_db.players.values()
        ],
    )
    conn.commit()


def build_db(year: int, *, force_download: bool = False, dryrun: bool = False) -> None:
    DATA_DIR.mkdir(exist_ok=True)

    zp = zip_path(year)
    if not zp.exists() or force_download:
        if dryrun:
            print(f"[dryrun] would download archive for {year}")
        else:
            download_season_archive(year, DATA_DIR, force=force_download, show_progress=True)
    else:
        print(f"Archive already present: {zp}")

    if dryrun:
        print(f"[dryrun] would load .fca from {zp} and write {db_path(year)}")
        return

    print(f"Loading player data from {zp} ...")
    fcm_db = PlayersDatabase.from_zip(zp)
    print(f"  {len(fcm_db.teams)} teams, {len(fcm_db.players)} players")

    out = db_path(year)
    conn = sqlite3.connect(out)
    create_schema(conn)
    populate(conn, fcm_db)
    conn.close()
    print(f"Database written to {out}")


def reset_db(year: int, *, dryrun: bool = False) -> None:
    out = db_path(year)
    if not out.exists():
        print(f"Nothing to reset: {out} does not exist")
        return
    if dryrun:
        print(f"[dryrun] would delete {out}")
    else:
        out.unlink()
        print(f"Deleted {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the FantaCalcio players database.")
    parser.add_argument("--update", action="store_true", help="Re-download and rebuild the database")
    parser.add_argument("--reset", action="store_true", help="Delete the local SQLite database")
    parser.add_argument("--year", type=int, default=datetime.now().year,
                        help="Season year (default: current year)")
    parser.add_argument("--dryrun", action="store_true", help="Show what would happen without making changes")
    args = parser.parse_args()

    if args.reset:
        reset_db(args.year, dryrun=args.dryrun)
    elif args.update:
        build_db(args.year, force_download=True, dryrun=args.dryrun)
    else:
        if db_path(args.year).exists():
            print(f"Database already exists: {db_path(args.year)}")
        else:
            build_db(args.year, dryrun=args.dryrun)


if __name__ == "__main__":
    main()
