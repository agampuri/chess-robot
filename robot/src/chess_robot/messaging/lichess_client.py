"""
LiChess Board API Client
"""

import os
import threading
import time
import chess
import berserk
import logging
from typing import Optional, Callable


class LiChessClient:
    """Manages a LiChess game for the chess robot."""

    def __init__(self, color: str = "white",
                 logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("lichess_client")
        self.color = color
        self.game_id = None
        self.board = chess.Board()
        self.my_turn = (color == "white")
        self.game_active = False
        self._stream_thread = None
        self.game_ready = threading.Event()
        self.username = None
        self.opponent_username = None
        self.opponent_offered_draw = False
        self.game_end_status = None

        token = os.environ.get("LICHESS_TOKEN")
        if token is None:
            raise ValueError(
                "LICHESS_TOKEN environment variable not set!\n"
                "Run: export LICHESS_TOKEN='lip_your_token_here'"
            )

        session = berserk.TokenSession(token)
        self.client = berserk.Client(session)

        try:
            account = self.client.account.get()
            self.username = account['username']
            self.logger.info(f"Logged in to LiChess as: {self.username}")
        except Exception as e:
            raise ConnectionError(f"Failed to connect to LiChess: {e}")

    def reset_for_new_game(self, color: str):
        """Reset state for a new game (rematch)."""
        self.color = color
        self.game_id = None
        self.board = chess.Board()
        self.my_turn = (color == "white")
        self.game_active = False
        self._stream_thread = None
        self.game_ready = threading.Event()
        self.opponent_offered_draw = False
        self.game_end_status = None
        self.logger.info(f"Client reset for new game as {color}")

    # ── Draw / Resign ────────────────────────────────────────

    def offer_draw(self) -> bool:
        """Offer a draw, or accept if opponent already offered."""
        if not self.game_id:
            self.logger.error("No active game!")
            return False
        try:
            self.client.board.offer_draw(self.game_id)
            if self.opponent_offered_draw:
                self.logger.info("Draw accepted (opponent had offered)")
            else:
                self.logger.info("Draw offered to opponent")
            self.opponent_offered_draw = False
            return True
        except berserk.exceptions.ResponseError as e:
            self.logger.error(f"Draw offer failed: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Draw offer error: {type(e).__name__}: {e}")
            return False

    def decline_draw(self) -> bool:
        """Decline an opponent's draw offer."""
        if not self.game_id:
            self.logger.error("No active game!")
            return False
        try:
            self.client.board.decline_draw(self.game_id)
            self.opponent_offered_draw = False
            self.logger.info("Draw declined")
            return True
        except berserk.exceptions.ResponseError as e:
            self.logger.error(f"Draw decline failed: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Draw decline error: {type(e).__name__}: {e}")
            return False

    def resign(self) -> bool:
        """Resign the game."""
        if not self.game_id:
            self.logger.error("No active game!")
            return False
        try:
            self.client.board.resign_game(self.game_id)
            self.game_active = False
            self.logger.info("Resigned game")
            return True
        except berserk.exceptions.ResponseError as e:
            self.logger.error(f"Resign failed: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Resign error: {type(e).__name__}: {e}")
            return False

    # ── Game setup ───────────────────────────────────────────

    def _abort_ongoing_games(self):
        """Abort/resign any ongoing games so they don't interfere."""
        self.logger.info("Checking for ongoing games...")
        try:
            games = list(self.client.games.get_ongoing())
            if games:
                self.logger.info(f"Found {len(games)} ongoing game(s), aborting/resigning...")
                for g in games:
                    gid = g.get('gameId', g.get('id', ''))
                    if gid:
                        try:
                            self.client.board.abort_game(gid)
                            self.logger.info(f"Aborted game {gid}")
                        except Exception:
                            try:
                                self.client.board.resign_game(gid)
                                self.logger.info(f"Resigned game {gid}")
                            except Exception as e2:
                                self.logger.warning(f"Could not end game {gid}: {e2}")
            else:
                self.logger.info("No ongoing games")
        except Exception as e:
            self.logger.warning(f"Could not check ongoing games: {e}")

    def create_game_vs_ai(self, ai_level: int = 1) -> str:
        """Create a game against LiChess AI (Stockfish)."""
        self._abort_ongoing_games()
        self.logger.info(f"Creating game vs AI level {ai_level}...")

        challenge = self.client.challenges.create_ai(
            level=ai_level,
            clock_limit=10800,
            clock_increment=0,
            color=self.color
        )

        self.game_id = challenge['id']
        self.game_active = True
        self.board = chess.Board()
        self.my_turn = (self.color == "white")
        self.opponent_username = f"AI-level-{ai_level}"

        self.logger.info(f"Game created: https://lichess.org/{self.game_id}")
        return self.game_id

    def create_challenge(self, opponent_username: str) -> str:
        """Challenge a specific LiChess player and wait for acceptance."""
        self._abort_ongoing_games()
        self.logger.info(f"Challenging {opponent_username}...")
        self.opponent_username = opponent_username

        try:
            challenge = self.client.challenges.create(
                opponent_username,
                rated=False,
                clock_limit=10800,
                clock_increment=0,
                color=self.color
            )
            challenge_id = challenge.get('id', '')
            self.logger.info(f"Challenge created! ID: {challenge_id}")
            self.logger.info(f"Accept at: https://lichess.org/{challenge_id}")
            self.logger.info(f"Waiting for {opponent_username} to accept...")
        except Exception as e:
            self.logger.error(f"Failed to create challenge: {e}")
            raise

        # Wait for the CORRECT gameStart event matching our challenge
        event_stream = self.client.board.stream_incoming_events()
        try:
            for event in event_stream:
                etype = event.get('type', '')
                self.logger.info(f"Incoming event: {etype}")

                if etype == 'gameStart':
                    game_data = event.get('game', {})
                    gid = (game_data.get('gameId')
                           or game_data.get('id')
                           or game_data.get('fullId', '')[:8]
                           or '')

                    self.logger.info(
                        f"gameStart: id={gid}, source={game_data.get('source')}, "
                        f"opponent={game_data.get('opponent', {}).get('username', '?')}")

                    opponent_name = game_data.get('opponent', {}).get('username', '')
                    is_our_challenge = (
                        gid == challenge_id
                        or opponent_name.lower() == opponent_username.lower()
                    )

                    if not is_our_challenge:
                        self.logger.info(
                            f"Ignoring gameStart for {gid} (old/unrelated game, "
                            f"waiting for challenge {challenge_id})")
                        continue

                    self.logger.info(f"Matched our challenge! Game ID: {gid}")
                    self.game_id = gid
                    self.game_active = True
                    self.board = chess.Board()
                    if opponent_name:
                        self.opponent_username = opponent_name
                    self.logger.info(f"Game URL: https://lichess.org/{self.game_id}")
                    break

                elif etype == 'challengeDeclined':
                    reason = event.get('challenge', {}).get('declineReason', 'unknown')
                    self.logger.error(f"Challenge declined: {reason}")
                    raise RuntimeError(f"Challenge declined: {reason}")

                elif etype == 'challengeCanceled':
                    self.logger.error("Challenge was canceled")
                    raise RuntimeError("Challenge canceled")
        finally:
            try:
                event_stream.close()
            except Exception:
                pass

        if not self.game_id:
            raise RuntimeError("Event stream ended without game starting")

        return self.game_id

    def accept_challenge(self) -> str:
        """Wait for and accept an incoming challenge."""
        self._abort_ongoing_games()
        self.logger.info("Waiting for incoming challenge...")

        event_stream = self.client.board.stream_incoming_events()
        try:
            for event in event_stream:
                etype = event.get('type', '')
                self.logger.info(f"Incoming event: {etype}")

                if etype == 'challenge':
                    challenge = event['challenge']
                    challenger = challenge['challenger']['name']
                    self.logger.info(f"Challenge from {challenger}")

                    self.client.challenges.accept(challenge['id'])
                    self.game_id = challenge['id']
                    self.game_active = True
                    self.board = chess.Board()
                    self.opponent_username = challenger

                    self.logger.info(f"Accepted challenge {self.game_id}")
                    self.logger.info(f"Game: https://lichess.org/{self.game_id}")
                    break

                elif etype == 'gameStart':
                    game_data = event.get('game', {})
                    gid = (game_data.get('gameId')
                           or game_data.get('id')
                           or '')
                    self.game_id = gid
                    self.game_active = True
                    self.board = chess.Board()
                    opp = game_data.get('opponent', {}).get('username', '')
                    if opp:
                        self.opponent_username = opp
                    self.logger.info(f"Game started: {self.game_id}")
                    break
        finally:
            try:
                event_stream.close()
            except Exception:
                pass

        if not self.game_id:
            raise RuntimeError("No game started")

        return self.game_id

    def push_move(self, move_uci: str) -> bool:
        """Send a move to LiChess."""
        if not self.game_id:
            self.logger.error("No active game!")
            return False

        self.logger.info(
            f"Pushing move {move_uci} to game {self.game_id} "
            f"(we are {self.color}, my_turn={self.my_turn})...")
        try:
            self.client.board.make_move(self.game_id, move_uci)
            self.board.push(chess.Move.from_uci(move_uci))
            self.my_turn = False
            self.logger.info(f"Move sent OK: {move_uci}")
            return True
        except berserk.exceptions.ResponseError as e:
            self.logger.error(f"Move rejected by LiChess: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error pushing move: {type(e).__name__}: {e}")
            return False

    # ── Game stream ──────────────────────────────────────────

    def _check_draw_offers(self, event,
                           on_draw_offer: Optional[Callable[[], None]] = None):
        """Check for draw offer flags in a game state event."""
        wdraw = event.get('wdraw', False)
        bdraw = event.get('bdraw', False)

        opponent_draw = (
            (wdraw and self.color == 'black') or
            (bdraw and self.color == 'white')
        )

        if opponent_draw and not self.opponent_offered_draw:
            self.opponent_offered_draw = True
            self.logger.info("Opponent has offered a draw!")
            if on_draw_offer:
                on_draw_offer()
        elif not opponent_draw and self.opponent_offered_draw:
            self.opponent_offered_draw = False

    def stream_game(self, on_opponent_move: Callable[[str], None],
                     on_game_end: Optional[Callable[[str], None]] = None,
                     on_draw_offer: Optional[Callable[[], None]] = None):
        """
        Stream game state from LiChess (blocking).
        Determines actual color from gameFull event.
        """
        if not self.game_id:
            self.logger.error("No active game to stream!")
            return

        self.logger.info(f"Connecting game stream for {self.game_id}...")

        try:
            for event in self.client.board.stream_game_state(self.game_id):
                event_type = event.get('type', '')
                self.logger.info(f"Game stream event: {event_type}")

                if event_type == 'gameFull':
                    white_player = event.get('white', {})
                    black_player = event.get('black', {})
                    white_id = (white_player.get('id')
                                or white_player.get('name', ''))
                    black_id = (black_player.get('id')
                                or black_player.get('name', ''))

                    self.logger.info(f"White: {white_id}, Black: {black_id}, Us: {self.username}")

                    if self.username and white_id.lower() == self.username.lower():
                        actual_color = "white"
                    elif self.username and black_id.lower() == self.username.lower():
                        actual_color = "black"
                    elif white_player.get('aiLevel'):
                        actual_color = "black"
                    elif black_player.get('aiLevel'):
                        actual_color = "white"
                    else:
                        actual_color = self.color

                    if actual_color != self.color:
                        self.logger.warning(
                            f"COLOR CORRECTION: requested={self.color}, "
                            f"actual={actual_color}")
                    self.color = actual_color
                    self.logger.info(f"Playing as {self.color}")

                    # Extract opponent username from gameFull
                    if self.color == "white":
                        opp_data = black_player
                    else:
                        opp_data = white_player
                    opp_name = (opp_data.get('id')
                                or opp_data.get('name')
                                or opp_data.get('username', ''))
                    if opp_name and not opp_data.get('aiLevel'):
                        self.opponent_username = opp_name
                        self.logger.info(f"Opponent: {self.opponent_username}")

                    # Sync board
                    state = event.get('state', {})
                    moves_str = state.get('moves', '')
                    status = state.get('status', 'started')

                    self.board = chess.Board()
                    if moves_str:
                        for m in moves_str.split():
                            self.board.push(chess.Move.from_uci(m))

                    move_count = len(moves_str.split()) if moves_str else 0
                    self.my_turn = (
                        (self.color == "white" and move_count % 2 == 0) or
                        (self.color == "black" and move_count % 2 == 1)
                    )

                    self.logger.info(
                        f"Synced: {move_count} moves, my_turn={self.my_turn}")

                    self._check_draw_offers(state, on_draw_offer)

                    self.game_ready.set()

                    if status != 'started':
                        self.game_active = False
                        self.game_end_status = status
                        if on_game_end:
                            on_game_end(status)
                        return

                elif event_type == 'gameState':
                    moves_str = event.get('moves', '')
                    status = event.get('status', 'started')

                    if status != 'started':
                        self.game_active = False
                        self.game_end_status = status
                        self.logger.info(f"Game ended: {status}")
                        if on_game_end:
                            on_game_end(status)
                        return

                    self._check_draw_offers(event, on_draw_offer)

                    moves = moves_str.split() if moves_str else []
                    if not moves:
                        continue

                    last_move = moves[-1]
                    move_count = len(moves)

                    now_my_turn = (
                        (self.color == "white" and move_count % 2 == 0) or
                        (self.color == "black" and move_count % 2 == 1)
                    )

                    self.logger.info(
                        f"gameState: {move_count} moves, last={last_move}, "
                        f"now_my_turn={now_my_turn}, was_my_turn={self.my_turn}")

                    if now_my_turn and not self.my_turn:
                        self.my_turn = True
                        self.board = chess.Board()
                        for m in moves:
                            self.board.push(chess.Move.from_uci(m))
                        self.logger.info(f">>> Opponent played: {last_move}")
                        on_opponent_move(last_move)

                    elif not now_my_turn and self.my_turn:
                        self.my_turn = False
                        self.logger.info(f"Our move confirmed: {last_move}")

        except Exception as e:
            self.logger.error(f"Game stream error: {type(e).__name__}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            self.game_active = False

    def start_streaming(self, on_opponent_move: Callable[[str], None],
                         on_game_end: Optional[Callable[[str], None]] = None,
                         on_draw_offer: Optional[Callable[[], None]] = None
                         ) -> threading.Thread:
        """Start streaming in a background thread."""
        self.logger.info(f"Starting game stream for {self.game_id}...")
        self.game_ready.clear()

        thread = threading.Thread(
            target=self.stream_game,
            args=(on_opponent_move, on_game_end, on_draw_offer),
            daemon=True
        )
        thread.start()
        self._stream_thread = thread

        self.logger.info("Waiting for game stream to sync...")
        if self.game_ready.wait(timeout=15):
            self.logger.info(
                f"Stream connected! color={self.color}, my_turn={self.my_turn}")
        else:
            self.logger.error(
                "Stream did not sync within 15s! "
                "LiChess will show 'no wifi' for us.")

        return thread
