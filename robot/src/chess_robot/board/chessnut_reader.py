

import hid
import chess
import time
import logging
from typing import Optional


NIBBLE_TO_PIECE = {
    0x0: None,
    0x1: chess.Piece(chess.QUEEN, chess.BLACK),
    0x2: chess.Piece(chess.KING, chess.BLACK),
    0x3: chess.Piece(chess.BISHOP, chess.BLACK),
    0x4: chess.Piece(chess.PAWN, chess.BLACK),
    0x5: chess.Piece(chess.KNIGHT, chess.BLACK),
    0x6: chess.Piece(chess.ROOK, chess.WHITE),
    0x7: chess.Piece(chess.PAWN, chess.WHITE),
    0x8: chess.Piece(chess.ROOK, chess.BLACK),
    0x9: chess.Piece(chess.BISHOP, chess.WHITE),
    0xA: chess.Piece(chess.KNIGHT, chess.WHITE),
    0xB: chess.Piece(chess.QUEEN, chess.WHITE),
    0xC: chess.Piece(chess.KING, chess.WHITE),
}

# Nibble index -> file: high0=g, low0=h, high1=e, low1=f, high2=c, low2=d, high3=a, low3=b
NIBBLE_ORDER_TO_FILE = [6, 7, 4, 5, 2, 3, 0, 1]


class ChessnutReader:
    VENDOR_ID = 0x2D80
    PRODUCT_ID = 0x8003
    INIT_COMMAND = [0x21, 0x01, 0x00]
    BOARD_DATA_OFFSET = 2
    PACKET_MIN_LENGTH = 34

    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("chessnut_reader")
        self.device = hid.device()
        self.connected = False

    def connect(self):
        try:
            self.device.open(self.VENDOR_ID, self.PRODUCT_ID)
            self.device.set_nonblocking(1)
            self.connected = True
            self.logger.info("Connected to Chessnut Air Lite")
            self.device.write(self.INIT_COMMAND)
            time.sleep(1.5)
            for _ in range(20):
                self.device.read(256)
                time.sleep(0.05)
            self.logger.info("Board state streaming active")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect: {e}")
            return False

    def disconnect(self):
        if self.connected:
            try:
                self.device.close()
            except Exception:
                pass
            self.connected = False
            self.logger.info("Disconnected from Chessnut Air Lite")

    def _parse_packet(self, data):
        if len(data) < self.PACKET_MIN_LENGTH:
            return None
        if data[0] != 0x01 or data[1] != 0x3D:
            return None

        board = chess.Board(fen=None)

        for rank_idx in range(8):
            rank_number = 7 - rank_idx
            offset = self.BOARD_DATA_OFFSET + (rank_idx * 4)

            nibbles = []
            for byte_idx in range(4):
                b = data[offset + byte_idx]
                nibbles.append((b >> 4) & 0x0F)
                nibbles.append(b & 0x0F)

            for nibble_idx, file_idx in enumerate(NIBBLE_ORDER_TO_FILE):
                piece = NIBBLE_TO_PIECE.get(nibbles[nibble_idx])
                if piece is not None:
                    square = chess.square(file_idx, rank_number)
                    board.set_piece_at(square, piece)

        return board

    def read_board(self):
        if not self.connected:
            return None
        board = None
        for _ in range(30):
            data = self.device.read(256)
            if data and len(data) > 4:
                parsed = self._parse_packet(list(data))
                if parsed is not None:
                    board = parsed
            time.sleep(0.02)
        return board

    def wait_for_change(self, reference_board, game_board=None,
                        timeout=300.0, stable_time=2.0):
        if not self.connected:
            return None

        ref_fen = reference_board.board_fen()
        last_different_fen = None
        stable_since = None
        start_time = time.time()

        while (time.time() - start_time) < timeout:
            current = self.read_board()
            if current is None:
                time.sleep(0.1)
                continue

            current_fen = current.board_fen()

            if current_fen == ref_fen:
                last_different_fen = None
                stable_since = None
                time.sleep(0.1)
                continue

            if current_fen == last_different_fen:
                if stable_since is not None:
                    if (time.time() - stable_since) >= stable_time:
                        return current
            else:
                last_different_fen = current_fen
                stable_since = time.time()

            time.sleep(0.1)

        return None

    def _get_occupied(self, board):
        occupied = set()
        for sq in chess.SQUARES:
            if board.piece_at(sq) is not None:
                occupied.add(sq)
        return occupied

    def detect_move(self, before, after, game_board):
        """Detect move using exact match first, then presence-based fuzzy matching."""
        # First try exact match
        for move in game_board.legal_moves:
            test = game_board.copy()
            test.push(move)
            if test.board_fen() == after.board_fen():
                return move

        # Fallback: presence-based matching
        before_occupied = self._get_occupied(before)
        after_occupied = self._get_occupied(after)
        emptied = before_occupied - after_occupied
        filled = after_occupied - before_occupied

        if not emptied and not filled:
            return None

        best_move = None
        best_score = -999

        for move in game_board.legal_moves:
            test = game_board.copy()
            test.push(move)
            expected_occupied = self._get_occupied(test)

            expected_emptied = before_occupied - expected_occupied
            expected_filled = expected_occupied - before_occupied

            empty_match = len(emptied & expected_emptied)
            fill_match = len(filled & expected_filled)
            empty_extra = len(emptied - expected_emptied)
            fill_extra = len(filled - expected_filled)
            empty_missing = len(expected_emptied - emptied)
            fill_missing = len(expected_filled - filled)

            score = (empty_match + fill_match) * 3 - (
                empty_extra + fill_extra + empty_missing + fill_missing)

            if score > best_score:
                best_score = score
                best_move = move

        if best_move is not None and best_score >= 2:
            self.logger.info(
                f"Detected: {best_move.uci()} (score={best_score})")
            return best_move

        self.logger.warning(f"No confident match (best score={best_score})")
        return None


def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger("chessnut_test")

    reader = ChessnutReader(logger)
    if not reader.connect():
        print("Could not connect. Is the board plugged in?")
        return

    print("\n=== Reading current board state ===")
    board = reader.read_board()
    if board:
        print(f"\n{board}\n")
        print(f"FEN: {board.board_fen()}")
        start = chess.Board()
        if board.board_fen() == start.board_fen():
            print("\n* Board matches starting position!")
        else:
            print(f"\nBoard does NOT match starting position.")
            print(f"Expected: {start.board_fen()}")
    else:
        print("Failed to read board state")
        reader.disconnect()
        return

    print("\n=== Move detection test ===")
    print("Make moves on the board. Wait 3 seconds after each piece.")
    print("Press Ctrl+C to stop.\n")

    game = chess.Board()
    current_state = reader.read_board()

    try:
        while True:
            color = 'White' if game.turn else 'Black'
            print(f"{color} to move. Make a move on the board...")
            new_state = reader.wait_for_change(current_state, game_board=game)

            if new_state is None:
                print("Timeout waiting for move.")
                continue

            move = reader.detect_move(current_state, new_state, game)
            if move:
                san = game.san(move)
                print(f"Detected move: {move.uci()} ({san})")
                game.push(move)
                print(f"\n{game}\n")
                current_state = new_state
            else:
                print("Could not identify the move. Please redo it.")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        reader.disconnect()


if __name__ == "__main__":
    main()
