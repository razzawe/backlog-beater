"""Explicit bounded upgrade of previously collected metadata to game-mode fields."""
import argparse
import asyncio
from sqlmodel import Session, select
from app.database import Game, GameMetadata, engine
from .steam_metadata import lookup_metadata, SteamMetadataError
from .steam_pacing import wait_for_slot, SteamPacingError


def backfill(limit=100, after_id=0):
    if type(limit) is not int or not 1 <= limit <= 1000 or type(after_id) is not int or after_id < 0:
        raise ValueError('Invalid batch bounds')
    with Session(engine) as session:
        rows = session.exec(select(Game.id, Game.steam_app_id).join(GameMetadata,
            GameMetadata.game_id == Game.id).where(Game.id > after_id,
            GameMetadata.last_success_at.is_not(None), GameMetadata.multiplayer.is_(None))
            .order_by(Game.id).limit(limit)).all()
    updated = failed = 0
    for game_id, app_id in rows:
        try:
            wait_for_slot()
            result = asyncio.run(lookup_metadata(app_id))
        except (SteamMetadataError, SteamPacingError):
            failed += 1
            continue
        with Session(engine) as session:
            game = session.exec(select(Game).where(Game.id == game_id).with_for_update()).first()
            record = session.get(GameMetadata, game_id)
            if game and game.steam_app_id == app_id and record and record.multiplayer is None:
                record.multiplayer = result.multiplayer
                if record.single_player is None:
                    record.single_player = result.single_player
                session.add(record)
                session.commit()
                updated += result.multiplayer is not None
    return {'updated': updated, 'failed': failed, 'last_game_id': rows[-1][0] if rows else after_id}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--after-id', type=int, default=0)
    args = parser.parse_args()
    print(backfill(args.limit, args.after_id), flush=True)
