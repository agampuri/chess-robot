# Remote Chess Robot

Play chess remotely over the internet with a physical board and robot arm.
Your Chessnut Air Lite detects your moves, LiChess relays them, and a
UFactory Lite 6 with two-finger gripper executes the opponent's moves.

## Architecture

```
Chessnut Air Lite ──USB──► Linux Desktop ◄──Ethernet──► Lite 6 Robot
                              │
                         LiChess API
                              │
                     Internet (opponent)
```

## Prerequisites (Already Installed)

- Ubuntu 22.04
- ROS2 Humble
- MoveIt2
- xarm_ros2 (built at ~/dev_ws)
- Python 3.10

## Setup (One Time)

### 1. Install Python packages

```bash
pip3 install berserk python-chess hidapi
```

### 2. USB permissions for Chessnut board

```bash
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="2d80", ATTRS{idProduct}=="8003", MODE="0666"
KERNEL=="hidraw*", MODE="0666"' | sudo tee /etc/udev/rules.d/99-chessnutair.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

### 3. Create a LiChess account and API token

1. Go to https://lichess.org and create an account
2. Go to https://lichess.org/account/oauth/token
3. Enable: board:play, challenge:read, challenge:write
4. Copy the token

### 4. Save the LiChess token

Add to your ~/.bashrc:
```bash
echo 'export LICHESS_TOKEN="lip_your_token_here"' >> ~/.bashrc
source ~/.bashrc
```

### 5. Copy xarm_user_params.yaml

This enables the two-finger gripper services in ROS2:
```bash
cp ~/chess_remote/xarm_user_params.yaml ~/dev_ws/src/xarm_ros2/xarm_api/config/xarm_user_params.yaml
cd ~/dev_ws
colcon build --packages-select xarm_api
source install/setup.bash
```

### 6. Adjust board_config.yaml

Edit `config/board_config.yaml` to match your physical setup:
- `board.origin.x/y/z` — where the a1 corner of the board is relative to the robot base
- `board.square_size` — 0.03 for Chessnut Air Lite (30mm squares)
- `board.piece_height` — measure from board surface to where the gripper should grab

## Usage

### Terminal 1: Launch ROS2 + MoveIt + Robot

```bash
source ~/dev_ws/install/setup.bash

# For real robot with two-finger gripper:
ros2 launch xarm_moveit_config lite6_moveit_realmove.launch.py robot_ip:=192.168.1.175 add_gripper:=true

# For simulation (no real robot):
ros2 launch xarm_moveit_config lite6_moveit_fake.launch.py add_gripper:=true
```

### Terminal 2: Run the chess robot

```bash
cd ~/chess_remote/robot/src
source ~/dev_ws/install/setup.bash
export LICHESS_TOKEN="lip_your_token_here"

# Test vs AI level 1 (type moves manually):
python3 main.py --color white --mode ai --no-board

# Test vs AI (auto-detect moves from Chessnut board):
python3 main.py --color white --mode ai

# Play vs a specific LiChess user:
python3 main.py --color white --mode challenge --opponent THEIR_USERNAME

# Wait for someone to challenge you:
python3 main.py --color black --mode accept
```

## Testing the Board Reader (Standalone)

```bash
cd ~/chess_remote/robot/src
python3 -m chess_robot.board.chessnut_reader
```

This will:
1. Connect to the Chessnut Air Lite via USB
2. Read and display the current board state
3. Enter move detection mode — make moves and it will detect them

## Project Structure

```
chess_remote/
├── config/
│   ├── board_config.yaml          # Board dimensions and robot params
│   └── logging_config.yaml        # Logging configuration
├── robot/
│   └── src/
│       ├── main.py                # Entry point
│       └── chess_robot/
│           ├── board/
│           │   └── chessnut_reader.py    # Chessnut Air Lite USB reader
│           ├── messaging/
│           │   └── lichess_client.py     # LiChess Board API client
│           ├── movement/
│           │   ├── movement_controller.py
│           │   ├── movement_planner.py
│           │   └── robot_hardware.py     # Two-finger gripper control
│           ├── nodes/
│           │   └── chess_node.py         # ROS2 node
│           ├── visualization/
│           │   └── visualizer.py         # RViz board visualization
│           ├── logging_utils.py
│           └── performance_logger.py
├── xarm_user_params.yaml          # Gripper services config
├── requirements.txt
└── README.md
```

