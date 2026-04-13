from typing import Optional
import logging
import time
from .robot_hardware import RobotHardware, MoveResult
from .movement_planner import MovementPlanner
import asyncio
from ..performance_logger import PerformanceLogger


class MovementController:
    def __init__(self, node, perf_logger=None):
        self.node = node
        self.logger = node.get_logger()
        self.robot = RobotHardware(node)
        self.planner = MovementPlanner(self.logger)
        self.perf_logger = perf_logger or PerformanceLogger()

    async def execute_movement(self, start_square, end_square, game_board=None, move_type="normal"):
        try:
            start_time = time.time()
            if not self.planner.validate_square(start_square):
                self.logger.error(f"Invalid start square: {start_square}")
                return False
            if end_square.lower() == "xx":
                result = await self._handle_capture_movement(start_square, game_board)
            else:
                result = await self._handle_regular_movement(
                    start_square, end_square, game_board, move_type=move_type)
            self.perf_logger.log_latency("move_execution", start_time)
            return result
        except Exception as e:
            self.logger.error(f"Error: {e}")
            return False

    async def _handle_capture_movement(self, start_square, game_board=None):
        start_time = time.time()
        capture_index = self.planner.get_next_capture_position()
        self.logger.info(
            f"\nMoving captured piece from {start_square} "
            f"to capture zone {capture_index}")
        planning_time = (time.time() - start_time) * 1000
        movements = self.planner.create_capture_movement_sequence(
            start_square, capture_index, game_board)
        execution_start = time.time()
        result = await self._execute_movement_sequence(movements)
        execution_time = (time.time() - execution_start) * 1000
        self.perf_logger.log_move_execution(
            f"{start_square}-XX", result, planning_time, execution_time, move_type="capture")
        return result

    async def _handle_regular_movement(self, start_square, end_square, game_board=None, move_type="normal"):
        start_time = time.time()
        if not self.planner.validate_square(end_square):
            self.logger.error(f"Invalid end square: {end_square}")
            return False
        self.logger.info(
            f"\nMoving piece from {start_square} to {end_square}")
        movements = self.planner.create_movement_sequence(
            start_square, end_square, game_board)
        planning_time = (time.time() - start_time) * 1000
        execution_start = time.time()
        result = await self._execute_movement_sequence(movements)
        execution_time = (time.time() - execution_start) * 1000
        self.perf_logger.log_move_execution(
            f"{start_square}-{end_square}", result,
            planning_time, execution_time, move_type=move_type)
        return result

    async def _execute_movement_sequence(self, movements):
        for i, movement in enumerate(movements):
            step_start = time.time()
            self.logger.info(f"\nExecuting: {movement['description']}")
            if movement['type'] == 'gripper':
                success = await self.robot.control_gripper(
                    movement['action'])
                if not success:
                    return False
            else:
                result = await self.robot.move_to_pose(
                    *movement['position'])
                if result != MoveResult.SUCCESS:
                    return False
            self.perf_logger.log_latency(
                f"movement_step_{i+1}_{movement['type']}", step_start)
            await asyncio.sleep(0.2)

        # De-energize gripper after move complete
        await self.robot.stop_gripper()
        return True
