import os
import logging
import logging.config
import time
from datetime import datetime
import json
import threading
from typing import Optional
from .logging_utils import setup_logging

class PerformanceMetrics:
    """Simple performance metrics collector for chess robot"""
    
    def __init__(self):
        """Initialize with a logger name"""
        self.logger = setup_logging('chess_robot.performance')
    
    def log_latency(self, operation: str, start_time: float) -> float:
        """Log latency for an operation"""
        duration_ms = (time.time() - start_time) * 1000
        self.logger.info(f"LATENCY: {operation} took {duration_ms:.2f}ms")
        return duration_ms
    
    def log_move_execution(self, move: str, success: bool, 
                         planning_time: Optional[float] = None, 
                         execution_time: Optional[float] = None):
        """Log move execution metrics"""
        status = "SUCCESS" if success else "FAILURE"
        timing = ""
        if planning_time and execution_time:
            timing = f" (planning: {planning_time:.2f}ms, execution: {execution_time:.2f}ms)"
        self.logger.info(f"MOVE: {move} {status}{timing}")
    
    def log_error(self, component: str, error_type: str, details: str):
        """Log error information"""
        self.logger.error(f"ERROR: {component} - {error_type}: {details}")

class PerformanceLogger:
    """Enhanced logger for collecting performance and reliability metrics"""
    
    def __init__(self, export_interval=300, participant_id=None):
        """
        Initialize the performance logger
        
        Args:
            export_interval: How often to export metrics (in seconds)
        """
        self.logger = setup_logging('chess_robot.performance')
        self.metrics = {
            "latency": [],
            "message_delivery": [],
            "move_execution": [],
            "errors": [],
            "recovery_events": []
        }
        self.export_interval = export_interval
        self.start_time = time.time()
        self.participant_id = participant_id
        self.phantom_detections = 0
        self.game_restarts = 0
        self.total_player_moves = 0
        self.total_robot_moves = 0
        self.game_id = None
        self.game_outcome = None
        self._all_move_executions = []
        self._all_player_moves = []
        self._all_latency = []
        
        # Create logs directory if it doesn't exist
        logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        
        # Start the export thread
        self._start_export_thread()
    
    def _start_export_thread(self):
        """Start a thread that exports metrics periodically"""
        def export_thread():
            while True:
                time.sleep(self.export_interval)
                self.export_metrics()
        
        thread = threading.Thread(target=export_thread, daemon=True)
        thread.start()
        self.logger.info(f"Started metrics export thread (interval: {self.export_interval}s)")
    
    def log_latency(self, operation, start_time, end_time=None):
        """Log latency for an operation"""
        if end_time is None:
            end_time = time.time()
        
        duration_ms = (end_time - start_time) * 1000
        
        lat_entry = {
            "timestamp": time.time(),
            "operation": operation,
            "duration_ms": duration_ms
        }
        self.metrics["latency"].append(lat_entry)
        self._all_latency.append(lat_entry)
        
        self.logger.info(f"LATENCY: {operation} took {duration_ms:.2f}ms")
        return duration_ms
    
    def log_message_delivery(self, message_id, status, latency=None):
        """Log message delivery status"""
        self.metrics["message_delivery"].append({
            "timestamp": time.time(),
            "message_id": message_id,
            "status": status,
            "latency": latency
        })
        
        self.logger.info(f"MESSAGE: {message_id} delivery {status}" + 
                        (f" ({latency:.2f}ms)" if latency else ""))
    
    def log_move_execution(self, move, success, planning_time=None, execution_time=None, move_type="normal"):
        """Log move execution metrics"""
        entry = {
            "timestamp": time.time(),
            "move": move,
            "success": success,
            "planning_time": planning_time,
            "execution_time": execution_time,
            "move_type": move_type
        }
        self.metrics["move_execution"].append(entry)
        self._all_move_executions.append(entry)
        
        status = "SUCCESS" if success else "FAILURE"
        self.logger.info(f"MOVE: {move} {status}" + 
                        (f" (planning: {planning_time:.2f}ms, execution: {execution_time:.2f}ms)" 
                         if planning_time and execution_time else ""))
    
    def log_error(self, component, error_type, details):
        """Log error information"""
        self.metrics["errors"].append({
            "timestamp": time.time(),
            "component": component,
            "error_type": error_type,
            "details": details
        })
        
        self.logger.error(f"ERROR: {component} - {error_type}: {details}")
    
    def log_recovery(self, component, event_type, success, duration=None):
        """Log recovery event"""
        self.metrics["recovery_events"].append({
            "timestamp": time.time(),
            "component": component,
            "event_type": event_type,
            "success": success,
            "duration": duration
        })
        
        status = "SUCCESS" if success else "FAILURE"
        self.logger.info(f"RECOVERY: {component} {event_type} {status}" + 
                        (f" ({duration:.2f}ms)" if duration else ""))
    

    def log_phantom_detection(self):
        """Increment phantom move detection counter"""
        self.phantom_detections += 1
        self.logger.warning(f"PHANTOM: detection #{self.phantom_detections}")

    def log_player_move(self, move_uci):
        """Log a successfully detected player move"""
        self.total_player_moves += 1
        pm_entry = {
            "timestamp": time.time(),
            "move": move_uci,
            "move_number": self.total_player_moves
        }
        self.metrics.setdefault("player_moves", []).append(pm_entry)
        self._all_player_moves.append(pm_entry)

    def log_robot_move(self):
        """Increment robot move counter"""
        self.total_robot_moves += 1

    def log_game_restart(self):
        """Increment game restart counter"""
        self.game_restarts += 1
        self.logger.warning(f"GAME RESTART #{self.game_restarts}")

    def set_game_info(self, game_id, game_outcome=None):
        """Set game metadata"""
        self.game_id = game_id
        if game_outcome:
            self.game_outcome = game_outcome

    def export_session(self):
        """Export full session metrics to a participant-tagged file"""
        try:
            logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'logs')
            os.makedirs(logs_dir, exist_ok=True)

            if self.participant_id:
                filename = f"session_{self.participant_id}.json"
            else:
                filename = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

            filepath = os.path.join(logs_dir, filename)

            session_data = {
                "participant_id": self.participant_id,
                "game_id": self.game_id,
                "game_outcome": self.game_outcome,
                "start_time": self.start_time,
                "end_time": time.time(),
                "game_duration_seconds": round(time.time() - self.start_time, 1),
                "total_robot_moves": self.total_robot_moves,
                "total_player_moves": self.total_player_moves,
                "phantom_detections": self.phantom_detections,
                "game_restarts": self.game_restarts,
                "metrics": {
                    "move_execution": self._all_move_executions,
                    "player_moves": self._all_player_moves,
                    "latency": self._all_latency,
                    "errors": self.metrics.get("errors", []),
                    "recovery_events": self.metrics.get("recovery_events", []),
                    "message_delivery": self.metrics.get("message_delivery", [])
                },
                "summary": self._generate_summary()
            }

            with open(filepath, 'w') as f:
                json.dump(session_data, f, indent=2)

            self.logger.info(f"Session exported to {filepath}")
            return filepath
        except Exception as e:
            self.logger.error(f"Failed to export session: {e}")
            return None

    def export_metrics(self):
        """Export collected metrics to a file"""
        try:
            # Create timestamp for filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Create filepath
            logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'logs')
            filepath = os.path.join(logs_dir, f"metrics_{timestamp}.json")
            
            # Add summary statistics
            export_data = {
                "metrics": {
                    "move_execution": self._all_move_executions,
                    "player_moves": self._all_player_moves,
                    "latency": self._all_latency,
                    "errors": self.metrics.get("errors", []),
                    "recovery_events": self.metrics.get("recovery_events", []),
                    "message_delivery": self.metrics.get("message_delivery", [])
                },
                "summary": self._generate_summary(),
                "start_time": self.start_time,
                "end_time": time.time()
            }
            
            # Write to file
            with open(filepath, 'w') as f:
                json.dump(export_data, f, indent=2)
            
            self.logger.info(f"Exported metrics to {filepath}")
            
            # Clear metrics after export (but keep a copy of errors)
            errors = self.metrics["errors"].copy()
            recovery = self.metrics["recovery_events"].copy()
            self.metrics = {
                "latency": [],
                "message_delivery": [],
                "move_execution": [],
                "errors": errors,
                "recovery_events": recovery
            }
            
        except Exception as e:
            self.logger.error(f"Failed to export metrics: {e}")
    
    def _generate_summary(self):
        """Generate summary statistics from collected metrics"""
        summary = {}
        
        # Latency stats
        if self.metrics["latency"]:
            latencies = {}
            for entry in self.metrics["latency"]:
                op = entry["operation"]
                if op not in latencies:
                    latencies[op] = []
                latencies[op].append(entry["duration_ms"])
            
            summary["latency"] = {
                op: {
                    "min": min(vals),
                    "max": max(vals),
                    "avg": sum(vals) / len(vals),
                    "count": len(vals)
                }
                for op, vals in latencies.items()
            }
        
        # Message delivery stats
        if self.metrics["message_delivery"]:
            total = len(self.metrics["message_delivery"])
            success = sum(1 for m in self.metrics["message_delivery"] if m["status"] == "success")
            
            summary["message_delivery"] = {
                "total": total,
                "success": success,
                "failure": total - success,
                "success_rate": (success / total) if total else 0
            }
        
        # Move execution stats
        if self.metrics["move_execution"]:
            total = len(self.metrics["move_execution"])
            success = sum(1 for m in self.metrics["move_execution"] if m["success"])
            
            planning_times = [m["planning_time"] for m in self.metrics["move_execution"] 
                             if m["planning_time"] is not None]
            execution_times = [m["execution_time"] for m in self.metrics["move_execution"] 
                              if m["execution_time"] is not None]
            
            summary["move_execution"] = {
                "total": total,
                "success": success,
                "failure": total - success,
                "success_rate": (success / total) if total else 0
            }
            
            if planning_times:
                summary["move_execution"]["planning_time"] = {
                    "min": min(planning_times),
                    "max": max(planning_times),
                    "avg": sum(planning_times) / len(planning_times)
                }
                
            if execution_times:
                summary["move_execution"]["execution_time"] = {
                    "min": min(execution_times),
                    "max": max(execution_times),
                    "avg": sum(execution_times) / len(execution_times)
                }
        
        # Error stats
        if self.metrics["errors"]:
            by_component = {}
            by_type = {}
            
            for error in self.metrics["errors"]:
                component = error["component"]
                error_type = error["error_type"]
                
                if component not in by_component:
                    by_component[component] = 0
                by_component[component] += 1
                
                if error_type not in by_type:
                    by_type[error_type] = 0
                by_type[error_type] += 1
            
            summary["errors"] = {
                "total": len(self.metrics["errors"]),
                "by_component": by_component,
                "by_type": by_type
            }
        
        return summary 