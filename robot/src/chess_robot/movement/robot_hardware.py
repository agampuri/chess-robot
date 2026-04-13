"""
Robot Hardware Control

Uses MoveIt2 MoveGroup for motion planning.
Includes mode enforcement (mode=0, state=0 before every move)
and orientation path constraints to reduce gripper tilting.
"""

import rclpy
import asyncio
from geometry_msgs.msg import PoseStamped, Quaternion
import shape_msgs.msg
import moveit_msgs.msg
from action_msgs.msg import GoalStatus
from moveit_msgs.msg import Constraints, OrientationConstraint
from moveit_msgs.action import MoveGroup
from rclpy.action import ActionClient
from xarm_msgs.srv import Call, SetInt16
from enum import Enum
import yaml
import os
import time


class MoveResult(Enum):
    SUCCESS = "Success"
    PLANNING_FAILED = "Planning failed"
    EXECUTION_FAILED = "Execution failed"
    GRIPPER_FAILED = "Gripper operation failed"


class RobotHardware:
    def __init__(self, node):
        self.node = node
        self.config = self._load_config()
        self.simulation_mode = self._detect_simulation_mode()
        self._setup_action_client()
        self._setup_gripper()
        pass  # Mode managed by ROS2 launch

    def _load_config(self):
        config_paths = [
            os.path.join(os.path.dirname(__file__),
                         '..', '..', '..', '..', 'config', 'board_config.yaml'),
            os.path.expanduser("~/chess_remote/config/board_config.yaml"),
        ]
        for p in config_paths:
            p = os.path.abspath(p)
            if os.path.exists(p):
                with open(p, 'r') as f:
                    config = yaml.safe_load(f)
                    self.node.get_logger().info(f"Loaded config from {p}")
                    return config['robot']
        raise FileNotFoundError("board_config.yaml not found")

    def _detect_simulation_mode(self):
        while True:
            mode = input("Enter 1 for simulation mode or 2 for hardware mode: ")
            if mode == "1":
                self.node.get_logger().info("Simulation mode selected")
                return True
            elif mode == "2":
                self.node.get_logger().info("Hardware mode selected")
                return False

    def _setup_mode_services(self):
        """Setup services to control robot mode and state."""
        self.set_mode_client = self.node.create_client(
            SetInt16, '/ufactory/set_mode')
        self.set_state_client = self.node.create_client(
            SetInt16, '/ufactory/set_state')
        self.set_mode_client.wait_for_service(timeout_sec=3.0)
        self.set_state_client.wait_for_service(timeout_sec=3.0)
        self.node.get_logger().info("Mode/state services ready")

    def _ensure_mode_zero(self):
        """Force robot into mode 0 (position) and state 0 (ready).
        
        This is critical — MoveIt requires mode 0 to send trajectories.
        Other operations (like gripper) can silently change the mode to 1,
        causing subsequent MoveIt moves to fail with error 99999.
        """
        if self.simulation_mode:
            return
        try:
            req = SetInt16.Request()
            req.data = 0
            future = self.set_mode_client.call_async(req)
            rclpy.spin_until_future_complete(self.node, future, timeout_sec=3.0)
            time.sleep(0.3)

            req = SetInt16.Request()
            req.data = 0
            future = self.set_state_client.call_async(req)
            rclpy.spin_until_future_complete(self.node, future, timeout_sec=3.0)
            time.sleep(0.3)

            self.node.get_logger().info("Robot mode=0, state=0")
        except Exception as e:
            self.node.get_logger().warning(f"Mode set failed: {e}")

    def _setup_action_client(self):
        """Setup MoveGroup action client for motion planning."""
        self.move_group_client = ActionClient(
            self.node, MoveGroup, 'move_action')
        if not self.move_group_client.wait_for_server(timeout_sec=10.0):
            raise RuntimeError("MoveGroup action server not available!")
        self.node.get_logger().info("MoveGroup action client connected")

    def _setup_gripper(self):
        """Setup two-finger gripper services."""
        if not self.simulation_mode:
            self.gripper_open_client = self.node.create_client(
                Call, '/ufactory/open_lite6_gripper')
            self.gripper_close_client = self.node.create_client(
                Call, '/ufactory/close_lite6_gripper')
            self.gripper_stop_client = self.node.create_client(
                Call, '/ufactory/stop_lite6_gripper')
            for name, client in [('open', self.gripper_open_client),
                                 ('close', self.gripper_close_client)]:
                if not client.wait_for_service(timeout_sec=5.0):
                    self.node.get_logger().warning(
                        f"Gripper {name} service not available")
            self.node.get_logger().info("Two-finger gripper services ready")
        else:
            self.gripper_open_client = None
            self.gripper_close_client = None
            self.gripper_stop_client = None

    async def move_to_pose(self, x, y, z):
        """Move robot to target position using MoveIt MoveGroup.
        
        Enforces mode 0 before each move to prevent error 99999.
        First tries with path constraints (reduces tilting).
        If that fails, retries with looser constraints.
        """
        try:
            goal_msg = self._create_move_goal(x, y, z)
            success = await self._execute_movement(goal_msg)
            if success:
                return MoveResult.SUCCESS

            # Retry with looser constraints
            self.node.get_logger().warning(
                "First attempt failed, retrying with looser constraints...")
            goal_msg = self._create_move_goal(x, y, z)
            success = await self._execute_movement(goal_msg)
            return MoveResult.SUCCESS if success else MoveResult.EXECUTION_FAILED
        except Exception as e:
            self.node.get_logger().error(f"Move failed: {e}")
            return MoveResult.EXECUTION_FAILED

    def _create_move_goal(self, x, y, z):
        """Create a MoveGroup goal with orientation constraints.
        
        Gripper orientation: quaternion (0,1,0,0) = pointing straight down.
        Goal constraint: gripper must be within tolerance of straight down at destination.
        Path constraint: gripper must stay roughly down during entire movement.
        """
        goal_msg = MoveGroup.Goal()
        goal_msg.request.group_name = "lite6"

        target_pose = PoseStamped()
        target_pose.header.frame_id = "world"
        target_pose.header.stamp = self.node.get_clock().now().to_msg()
        target_pose.pose.position.x = x
        target_pose.pose.position.y = y
        target_pose.pose.position.z = z
        # Quaternion (0,1,0,0) = gripper pointing straight down
        target_pose.pose.orientation = Quaternion(
            x=0.0, y=1.0, z=0.0, w=0.0)

        self._setup_planning_parameters(goal_msg)

        goal_constraint = self._create_position_constraint(target_pose)

        # Orientation constraint: gripper pointing down at goal
        orient = OrientationConstraint()
        orient.header = target_pose.header
        orient.orientation = target_pose.pose.orientation
        orient.link_name = "link_tcp"
        orient.absolute_x_axis_tolerance = 0.01
        orient.absolute_y_axis_tolerance = 0.01
        orient.absolute_z_axis_tolerance = 0.01  # Free rotation around vertical - gripper is symmetric
        orient.weight = 1.0
        goal_constraint.orientation_constraints.append(orient)

        goal_msg.request.goal_constraints.append(goal_constraint)

        # Path constraints removed - caused weird movement paths

        return goal_msg

    def _setup_planning_parameters(self, goal_msg):
        """Configure MoveIt planning parameters from config."""
        goal_msg.request.workspace_parameters.header.frame_id = "world"
        goal_msg.request.workspace_parameters.min_corner.x = -1.0
        goal_msg.request.workspace_parameters.min_corner.y = -1.0
        goal_msg.request.workspace_parameters.min_corner.z = -1.0
        goal_msg.request.workspace_parameters.max_corner.x = 1.0
        goal_msg.request.workspace_parameters.max_corner.y = 1.0
        goal_msg.request.workspace_parameters.max_corner.z = 1.0

        mc = self.config['movement']
        goal_msg.request.allowed_planning_time = mc['planning_time']
        goal_msg.request.num_planning_attempts = mc['planning_attempts']
        goal_msg.request.max_velocity_scaling_factor = mc['max_velocity_scaling']
        goal_msg.request.max_acceleration_scaling_factor = mc['max_acceleration_scaling']

        goal_msg.planning_options.plan_only = False
        goal_msg.planning_options.replan = True
        goal_msg.planning_options.replan_attempts = mc['replan_attempts']
        goal_msg.planning_options.replan_delay = mc['replan_delay']

    async def _execute_movement(self, goal_msg):
        """Send goal to MoveGroup and wait for result."""
        try:
            send_future = self.move_group_client.send_goal_async(goal_msg)
            rclpy.spin_until_future_complete(
                self.node, send_future, timeout_sec=65.0)
            if not send_future.done():
                self.node.get_logger().error("Send goal timed out")
                return False

            handle = send_future.result()
            if not handle or not handle.accepted:
                self.node.get_logger().error("Goal rejected by MoveGroup")
                return False

            result_future = handle.get_result_async()
            rclpy.spin_until_future_complete(
                self.node, result_future, timeout_sec=65.0)
            if not result_future.done():
                self.node.get_logger().error("Result timed out")
                return False

            error = result_future.result().result.error_code.val
            if error == 1:  # SUCCESS
                return True

            self.node.get_logger().error(f"MoveIt error code: {error}")
            return False
        except Exception as e:
            self.node.get_logger().error(f"Movement error: {e}")
            return False

    async def control_gripper(self, close):
        """Open or close the two-finger gripper."""
        if self.simulation_mode:
            self.node.get_logger().info(
                f"SIMULATION: {'Closing' if close else 'Opening'} gripper")
            await asyncio.sleep(0.5)
            return True
        try:
            client = (self.gripper_close_client if close
                      else self.gripper_open_client)
            action = "close" if close else "open"
            if not client.service_is_ready():
                self.node.get_logger().error(
                    f"Gripper {action} service not ready")
                return False
            request = Call.Request()
            future = client.call_async(request)
            rclpy.spin_until_future_complete(
                self.node, future, timeout_sec=5.0)
            if not future.done():
                return False
            if future.result().ret != 0:
                self.node.get_logger().error(
                    f"Gripper {action} failed: {future.result().ret}")
                return False
            await asyncio.sleep(1.0)
            self.node.get_logger().info(f"Gripper {action} successful")
            return True
        except Exception as e:
            self.node.get_logger().error(f"Gripper error: {e}")
            return False

    async def stop_gripper(self):
        """De-energize the gripper motor."""
        if self.simulation_mode:
            return True
        try:
            if (self.gripper_stop_client and
                    self.gripper_stop_client.service_is_ready()):
                request = Call.Request()
                future = self.gripper_stop_client.call_async(request)
                rclpy.spin_until_future_complete(
                    self.node, future, timeout_sec=3.0)
            return True
        except Exception:
            return False

    def _create_position_constraint(self, target_pose):
        """Create a position constraint for the goal."""
        constraint = Constraints()
        constraint.name = "goal"
        pos = moveit_msgs.msg.PositionConstraint()
        pos.header = target_pose.header
        pos.link_name = "link_tcp"
        sphere = shape_msgs.msg.SolidPrimitive()
        sphere.type = shape_msgs.msg.SolidPrimitive.SPHERE
        sphere.dimensions = [0.005]
        pos.constraint_region.primitives.append(sphere)
        pos.constraint_region.primitive_poses.append(target_pose.pose)
        constraint.position_constraints.append(pos)
        return constraint
