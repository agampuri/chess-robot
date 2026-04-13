"""
Remote Chess Robot — Main Entry Point
"""

import rclpy
import asyncio
import argparse
import time
import sys
import os
import select
import logging
import chess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chess_robot.messaging.lichess_client import LiChessClient
from chess_robot.board.chessnut_reader import ChessnutReader
from chess_robot.nodes.chess_node import ChessNode
from chess_robot.performance_logger import PerformanceLogger
from chess_robot.logging_utils import setup_logging


class ChessRobotApp:
    """Main application coordinating board, LiChess, and robot."""

    def __init__(self, args):
        self.args = args
        self.logger = setup_logging('chess_robot_app')
        self.perf_logger = PerformanceLogger(participant_id=getattr(args, "participant", None))

        self.game_board = chess.Board()
        self.board_snapshot = None
        self.running = True
        self.robot_moving = False
        self.game_over = False

        self.lichess = None
        self.reader = None
        self.node = None

    def setup_lichess(self):
        self.logger.info("Connecting to LiChess...")
        if self.lichess is None:
            self.lichess = LiChessClient(
                color=self.args.color, logger=self.logger)

        if self.args.mode == 'ai':
            self.lichess.create_game_vs_ai(ai_level=self.args.ai_level)
        elif self.args.mode == 'challenge':
            if not self.args.opponent:
                self.logger.error("--opponent required for challenge mode")
                sys.exit(1)
            self.lichess.create_challenge(self.args.opponent)
        elif self.args.mode == 'accept':
            self.lichess.accept_challenge()

        self.logger.info(f"LiChess setup done. game_id={self.lichess.game_id}")
        self.perf_logger.set_game_info(self.lichess.game_id)

    def setup_board_reader(self):
        if self.args.no_board:
            self.logger.info("Board reader disabled (--no-board)")
            self.reader = None
            return

        if self.reader is not None:
            return  # Already connected

        self.logger.info("Connecting to Chessnut Air Lite...")
        self.reader = ChessnutReader(logger=self.logger)

        if not self.reader.connect():
            self.logger.warning(
                "Could not connect to Chessnut board. "
                "Falling back to manual input.")
            self.reader = None
            return

        self.board_snapshot = self.reader.read_board()
        if self.board_snapshot:
            self.logger.info("Board state read successfully")

    def refresh_board_snapshot(self):
        """Take a fresh board snapshot (e.g. after rematch reset)."""
        if self.reader:
            self.logger.info("Refreshing board snapshot...")
            time.sleep(1)
            snap = self.reader.read_board()
            if snap is not None:
                self.board_snapshot = snap
                self.logger.info("Board snapshot refreshed for new game")

    def setup_ros_node(self):
        self.logger.info("Initializing ROS2...")
        rclpy.init()
        self.node = ChessNode(perf_logger=self.perf_logger)
        self.logger.info("ROS2 node ready")

    # ── Keyboard controls ────────────────────────────────────

    def print_controls(self):
        print()
        print("  ┌─────────────────────────────────┐")
        print("  │  KEYBOARD CONTROLS               │")
        print("  │  d + Enter = offer/accept draw   │")
        print("  │  n + Enter = decline draw         │")
        print("  │  r + Enter = resign               │")
        print("  │  h + Enter = show this help       │")
        print("  └─────────────────────────────────┘")
        print()

    def check_keyboard(self):
        """Non-blocking check for keyboard input."""
        if not select.select([sys.stdin], [], [], 0)[0]:
            return

        try:
            line = sys.stdin.readline().strip().lower()
        except (EOFError, OSError):
            return

        if not line:
            return

        if line == 'h':
            self.print_controls()
        elif line == 'd':
            self.handle_draw()
        elif line == 'n':
            self.handle_decline_draw()
        elif line == 'r':
            self.handle_resign()

    def handle_draw(self):
        if self.lichess.opponent_offered_draw:
            print("  Accepting opponent's draw offer...")
            success = self.lichess.offer_draw()
            if success:
                print("  Draw accepted!")
            else:
                print("  Failed to accept draw.")
        else:
            print("  Offering draw to opponent...")
            success = self.lichess.offer_draw()
            if success:
                print("  Draw offered. Waiting for opponent's response.")
            else:
                print("  Failed to offer draw.")

    def handle_decline_draw(self):
        if self.lichess.opponent_offered_draw:
            print("  Declining draw offer...")
            success = self.lichess.decline_draw()
            if success:
                print("  Draw declined.")
            else:
                print("  Failed to decline draw.")
        else:
            print("  No draw offer to decline.")

    def handle_resign(self):
        print("  Are you sure you want to resign? Type 'y' + Enter:")
        try:
            confirm = input("  ").strip().lower()
        except (EOFError, OSError):
            return
        if confirm == 'y':
            print("  Resigning...")
            success = self.lichess.resign()
            if success:
                print("  You resigned the game.")
            else:
                print("  Failed to resign.")
        else:
            print("  Resign cancelled.")

    # ── Rematch ──────────────────────────────────────────────

    def ask_rematch(self) -> bool:
        """Ask if the player wants a rematch. Returns True if yes."""
        opponent = self.lichess.opponent_username or "opponent"
        last_mode = self.args.mode

        print()
        print("  ┌─────────────────────────────────────┐")
        print(f"  │  Rematch vs {opponent[:20]:<20s}    │")
        print("  │  y + Enter = yes                     │")
        print("  │  n + Enter = no, quit                │")
        print("  └─────────────────────────────────────┘")

        try:
            answer = input("  Rematch? (y/n): ").strip().lower()
        except (EOFError, OSError):
            return False

        if answer != 'y':
            return False

        # Ask color
        print()
        print("  Choose your color:")
        print("  w = white")
        print("  b = black")
        print("  r = random")

        try:
            color_input = input("  Color (w/b/r): ").strip().lower()
        except (EOFError, OSError):
            return False

        if color_input == 'w':
            new_color = 'white'
        elif color_input == 'b':
            new_color = 'black'
        elif color_input == 'r':
            new_color = 'random'
        else:
            print(f"  Invalid choice '{color_input}', defaulting to white.")
            new_color = 'white'

        # For AI rematch, also ask level
        if last_mode == 'ai':
            try:
                level_input = input(
                    f"  AI level (1-8, Enter={self.args.ai_level}): "
                ).strip()
                if level_input:
                    new_level = int(level_input)
                    if 1 <= new_level <= 8:
                        self.args.ai_level = new_level
                    else:
                        print(f"  Invalid level, keeping {self.args.ai_level}")
            except (EOFError, OSError, ValueError):
                pass

        # Reset lichess client for new game
        self.lichess.reset_for_new_game(new_color)

        # For rematch vs human, always use challenge mode with known opponent
        if last_mode in ('challenge', 'accept') and opponent and not opponent.startswith('AI-'):
            self.args.mode = 'challenge'
            self.args.opponent = opponent

        self.args.color = new_color

        print(f"\n  Starting rematch as {new_color} vs {opponent}...")
        return True

    # ── Callbacks ────────────────────────────────────────────

    def on_draw_offer(self):
        """Called from stream thread when opponent offers a draw."""
        print()
        print("  ╔═══════════════════════════════════╗")
        print("  ║  OPPONENT OFFERS A DRAW!           ║")
        print("  ║  d + Enter = accept                ║")
        print("  ║  n + Enter = decline               ║")
        print("  ║  (or just make a move to decline)  ║")
        print("  ╚═══════════════════════════════════╝")
        print()

    def on_opponent_move(self, move_uci):
        """Called when the opponent makes a move on LiChess."""
        self.robot_moving = True
        self.logger.info(f"=== OPPONENT MOVE: {move_uci} ===")

        move = chess.Move.from_uci(move_uci)
        from_sq = chess.square_name(move.from_square)
        to_sq = chess.square_name(move.to_square)

        robot_moves = []

        if self.game_board.is_castling(move):
            self.logger.info(f"Castling: {from_sq} -> {to_sq}")
            robot_moves.append((from_sq, to_sq, "castling"))
            rank = "1" if self.game_board.turn == chess.WHITE else "8"
            if to_sq == f"c{rank}":
                robot_moves.append((f"a{rank}", f"d{rank}", "castling"))
            else:
                robot_moves.append((f"h{rank}", f"f{rank}", "castling"))
        elif self.game_board.is_en_passant(move):
            captured_sq = to_sq[0] + from_sq[1]
            self.logger.info(f"En passant: capture {captured_sq}")
            robot_moves.append((captured_sq, "xx", "en_passant"))
            robot_moves.append((from_sq, to_sq, "en_passant"))
        elif self.game_board.is_capture(move):
            self.logger.info(f"Capture: remove piece at {to_sq}")
            robot_moves.append((to_sq, "xx", "capture"))
            robot_moves.append((from_sq, to_sq, "capture"))
        else:
            robot_moves.append((from_sq, to_sq, "normal"))

        self.logger.info(f"Robot sequence: {robot_moves}")
        self.perf_logger.log_robot_move()

        async def execute_all():
            for item in robot_moves:
                start, end = item[0], item[1]
                mtype = item[2] if len(item) > 2 else "normal"
                self.logger.info(f"Robot executing: {start} -> {end} ({mtype})")
                success = await self.node.movement.execute_movement(
                    start, end, self.game_board, move_type=mtype)
                if not success:
                    self.logger.error(f"Robot failed: {start} -> {end}")
                    return False
                rclpy.spin_once(self.node, timeout_sec=0.1)
            return True

        loop = asyncio.new_event_loop()
        try:
            success = loop.run_until_complete(execute_all())
        finally:
            loop.close()

        self.game_board.push(move)

        if self.reader:
            self.logger.info("Waiting for board sensors to settle...")
            time.sleep(3)
            snap1 = self.reader.read_board()
            time.sleep(1)
            snap2 = self.reader.read_board()
            if snap2 is not None:
                self.board_snapshot = snap2
            elif snap1 is not None:
                self.board_snapshot = snap1
            self.logger.info("Board snapshot refreshed")

        self.robot_moving = False

        if success:
            self.logger.info("=== OPPONENT MOVE COMPLETE ===")
        else:
            self.logger.error("=== OPPONENT MOVE FAILED ===")

        print(f"\n  Board after opponent's move ({move_uci}):")
        print(f"  {self.game_board}\n")
        print(f"  Your turn! Make your move on the board.")

    def on_game_end(self, result):
        self.logger.info(f"Game ended: {result}")
        self.perf_logger.set_game_info(self.lichess.game_id, game_outcome=result)
        session_file = self.perf_logger.export_session()
        print(f"\n{'='*50}")
        print(f"  GAME OVER: {result}")
        if session_file:
            print(f"  Session data saved to: {session_file}")
        print(f"{'='*50}")
        self.game_over = True

    def detect_and_push_move(self):
        """Detect a move from the physical board and push to LiChess."""
        if self.reader is None or self.board_snapshot is None:
            return False
        if not self.lichess.my_turn:
            return False
        if self.robot_moving:
            return False

        new_state = self.reader.read_board()
        if new_state is None:
            return False
        if new_state.board_fen() == self.board_snapshot.board_fen():
            return False

        changed = 0
        for sq in chess.SQUARES:
            if new_state.piece_at(sq) != self.board_snapshot.piece_at(sq):
                changed += 1

        if changed > 4:
            self.logger.debug(f"Ignoring noisy reading ({changed} squares changed)")
            self.board_snapshot = new_state
            return False

        self.logger.info(f"Board change detected! {changed} squares differ. Verifying...")

        time.sleep(0.5)
        check1 = self.reader.read_board()
        if check1 is None or check1.board_fen() == self.board_snapshot.board_fen():
            return False

        time.sleep(0.5)
        check2 = self.reader.read_board()
        if check2 is None or check2.board_fen() != check1.board_fen():
            return False

        time.sleep(0.5)
        check3 = self.reader.read_board()
        if check3 is None or check3.board_fen() != check1.board_fen():
            return False

        self.logger.info("Triple-check passed — board is stable")

        changed_final = 0
        for sq in chess.SQUARES:
            if check3.piece_at(sq) != self.board_snapshot.piece_at(sq):
                changed_final += 1

        if changed_final > 4:
            self.logger.info(f"Too many changes after settling ({changed_final}), absorbing")
            self.board_snapshot = check3
            return False

        self.logger.info(
            f"Game board FEN: {self.game_board.board_fen()}")
        self.logger.info(
            f"Board snapshot FEN: {self.board_snapshot.board_fen()}")
        self.logger.info(
            f"Physical board FEN: {check3.board_fen()}")
        self.logger.info(
            f"Legal moves: {[m.uci() for m in self.game_board.legal_moves]}")

        move = None
        for m in self.game_board.legal_moves:
            test = self.game_board.copy()
            test.push(m)
            if test.board_fen() == check3.board_fen():
                move = m
                self.logger.info(f"Exact FEN match: {m.uci()}")
                break

        if move is None:
            emptied = []
            filled = []
            for sq in chess.SQUARES:
                was = self.board_snapshot.piece_at(sq) is not None
                now = check3.piece_at(sq) is not None
                if was and not now:
                    emptied.append(sq)
                elif not was and now:
                    filled.append(sq)

            self.logger.info(
                f"No exact match. Emptied: {[chess.square_name(s) for s in emptied]}, "
                f"Filled: {[chess.square_name(s) for s in filled]}")

            if len(emptied) == 1 and len(filled) == 1:
                from_sq = chess.square_name(emptied[0])
                to_sq = chess.square_name(filled[0])
                uci_str = from_sq + to_sq
                for m in self.game_board.legal_moves:
                    if m.uci() == uci_str or m.uci()[:4] == uci_str:
                        move = m
                        self.logger.info(f"1-piece-moved match: {m.uci()}")
                        break

        if move is None:
            self.logger.info("Trying fuzzy match...")
            move = self.reader.detect_move(
                self.board_snapshot, check3, self.game_board)
            if move:
                self.logger.info(f"Fuzzy match: {move.uci()}")

        if move is None:
            self.logger.warning("Could not identify move from board change")
            return False

        move_uci = move.uci()
        self.logger.info(f"Detected move: {move_uci} ({self.game_board.san(move)})")

        success = self.lichess.push_move(move_uci)
        if success:
            self.game_board.push(move)
            self.board_snapshot = check3
            self.perf_logger.log_player_move(move_uci)
            print(f"\n  Your move: {move_uci}")
            print(f"  Waiting for opponent...")
            return True
        else:
            self.logger.error(f"LiChess rejected move {move_uci}!")
            return False

    def manual_move_input(self):
        if not self.lichess.my_turn:
            return

        legal = [m.uci() for m in self.game_board.legal_moves]
        display = (f"{', '.join(legal[:10])}... ({len(legal)} total)"
                   if len(legal) > 10 else ', '.join(legal))
        print(f"\n  Your turn ({self.lichess.color}). Legal: {display}")
        print(f"  (d=draw, r=resign, h=help)")

        try:
            move_input = input("  Enter move (UCI, e.g. e2e4): ").strip()
        except EOFError:
            return

        if move_input == "quit":
            self.running = False
            return
        elif move_input.lower() == 'd':
            self.handle_draw()
            return
        elif move_input.lower() == 'n':
            self.handle_decline_draw()
            return
        elif move_input.lower() == 'r':
            self.handle_resign()
            return
        elif move_input.lower() == 'h':
            self.print_controls()
            return

        if move_input in legal:
            success = self.lichess.push_move(move_input)
            if success:
                self.game_board.push(chess.Move.from_uci(move_input))
                if self.reader:
                    time.sleep(0.5)
                    self.board_snapshot = self.reader.read_board()
                print(f"\n  {self.game_board}\n")
                print(f"  Waiting for opponent...")
        else:
            print(f"  '{move_input}' is not legal. Try again.")

    # ── Single game loop ─────────────────────────────────────

    def play_one_game(self):
        """Run a single game. Returns True if game ended normally."""
        self.game_over = False
        self.game_board = chess.Board()
        self.robot_moving = False

        self.setup_lichess()
        self.setup_board_reader()

        # Start stream
        self.logger.info(f"Starting game stream for game_id={self.lichess.game_id}")
        self.lichess.start_streaming(
            self.on_opponent_move, self.on_game_end, self.on_draw_offer)

        # Sync game_board from stream
        self.game_board = self.lichess.board.copy()

        # Set capture zone side based on our color
        self.node.movement.planner.set_playing_color(self.lichess.color)

        # Verify board snapshot
        if self.reader and self.board_snapshot:
            expected_fen = self.game_board.board_fen()
            actual_fen = self.board_snapshot.board_fen()
            if expected_fen != actual_fen:
                self.logger.warning(
                    f"Board mismatch! Game expects: {expected_fen}")
                self.logger.warning(
                    f"Physical board shows: {actual_fen}")
                self.logger.info(
                    "Taking fresh board snapshot as baseline...")
                self.board_snapshot = self.reader.read_board()

        print(f"\n  Playing as {self.lichess.color}")
        print(f"  Game: https://lichess.org/{self.lichess.game_id}")
        self.print_controls()

        if self.lichess.my_turn:
            print(f"  Your turn! Make your move on the board.")
        else:
            print(f"  Waiting for opponent's move...")

        while self.running and not self.game_over and self.lichess.game_active:
            rclpy.spin_once(self.node, timeout_sec=0.1)

            if self.reader:
                self.check_keyboard()

            if not self.lichess.my_turn:
                time.sleep(0.2)
                continue

            if self.reader and not self.robot_moving:
                detected = self.detect_and_push_move()
                if not detected:
                    time.sleep(0.2)
            elif not self.reader:
                self.manual_move_input()
            else:
                time.sleep(0.2)

        return self.game_over

    # ── Main entry ───────────────────────────────────────────

    def run(self):
        print("╔══════════════════════════════════════╗")
        print("║   REMOTE CHESS ROBOT v1.0            ║")
        print("╚══════════════════════════════════════╝\n")

        self.setup_ros_node()

        try:
            while self.running:
                self.play_one_game()

                if not self.running:
                    break

                # Game ended — ask for rematch
                if self.ask_rematch():
                    # Player needs to reset pieces on the board
                    print()
                    print("  ┌─────────────────────────────────────┐")
                    print("  │  Reset pieces to starting position  │")
                    print("  │  Press Enter when ready...          │")
                    print("  └─────────────────────────────────────┘")
                    try:
                        input("  ")
                    except (EOFError, OSError):
                        break

                    self.refresh_board_snapshot()
                    continue
                else:
                    break

        except KeyboardInterrupt:
            print("\n\nInterrupted by user.")

        self.logger.info("Shutting down...")
        if self.reader:
            self.reader.disconnect()
        if self.node:
            self.node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass
        print("Goodbye!")


def main():
    parser = argparse.ArgumentParser(description="Remote Chess Robot")
    parser.add_argument("--color", choices=["white", "black"],
                        default="white")
    parser.add_argument("--mode", choices=["ai", "challenge", "accept"],
                        default="ai")
    parser.add_argument("--opponent", type=str, default="")
    parser.add_argument("--ai-level", type=int, default=1)
    parser.add_argument("--no-board", action="store_true")
    parser.add_argument("--participant", type=str, default=None,
                        help="Participant ID for user study (e.g. P01)")
    args = parser.parse_args()
    app = ChessRobotApp(args)
    app.run()


if __name__ == "__main__":
    main()
