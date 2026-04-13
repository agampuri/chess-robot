"""
Chess Robot ROS2 Node

Simplified: no RViz wait, no RabbitMQ dependency.
"""

import rclpy
from rclpy.node import Node
import time
import yaml
import os
from ..visualization.visualizer import ChessboardVisualizer
from ..movement.movement_controller import MovementController
from ..performance_logger import PerformanceLogger
from typing import Optional


class ChessNode(Node):
    def __init__(self, perf_logger: Optional[PerformanceLogger] = None):
        super().__init__('chess_robot')
        self.get_logger().info('Initializing Chess Robot Node')
        try:
            self.visualizer = ChessboardVisualizer(self)
        except Exception as e:
            self.get_logger().warning(f'Visualization disabled: {e}')
            self.visualizer = None
        self.movement = MovementController(self, perf_logger)
        self.get_logger().info('Chess Robot Node initialized')
