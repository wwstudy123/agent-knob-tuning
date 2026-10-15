"""Dynamic workload analyzer for database performance bottlenecks.

Analyzes database metrics to identify performance bottlenecks (CPU, memory, IO, lock, etc.)
and provides recommendations for LLM prompt enhancement.
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum


class BottleneckType(Enum):
    """Database performance bottleneck types."""
    CPU = "cpu"
    MEMORY = "memory"
    IO = "io"
    LOCK = "lock"
    BUFFER_POOL = "buffer_pool"
    LOG = "log"
    NETWORK = "network"
    UNKNOWN = "unknown"


@dataclass
class BottleneckInfo:
    """Information about a detected bottleneck."""
    type: BottleneckType
    severity: float  # 0.0 - 1.0
    description: str
    metrics: Dict[str, float]
    recommendations: List[str]


class WorkloadAnalyzer:
    """Analyzes database workload and identifies performance bottlenecks."""

    def __init__(
        self,
        cpu_threshold: float = 0.8,
        memory_threshold: float = 0.85,
        io_wait_threshold: float = 0.3,
        lock_wait_threshold: float = 0.2,
        buffer_hit_ratio_threshold: float = 0.95,
        log_wait_threshold: float = 0.1
    ):
        """Initialize workload analyzer with thresholds.

        Args:
            cpu_threshold: CPU utilization threshold (0-1)
            memory_threshold: Memory utilization threshold (0-1)
            io_wait_threshold: IO wait time ratio threshold (0-1)
            lock_wait_threshold: Lock wait time ratio threshold (0-1)
            buffer_hit_ratio_threshold: Buffer pool hit ratio threshold (0-1)
            log_wait_threshold: Log wait time ratio threshold (0-1)
        """
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold
        self.io_wait_threshold = io_wait_threshold
        self.lock_wait_threshold = lock_wait_threshold
        self.buffer_hit_ratio_threshold = buffer_hit_ratio_threshold
        self.log_wait_threshold = log_wait_threshold

    def analyze_metrics(self, metrics: Dict[str, float]) -> List[BottleneckInfo]:
        """Analyze database metrics and identify bottlenecks.

        Args:
            metrics: Dictionary of database performance metrics

        Returns:
            List of detected bottlenecks with severity and recommendations
        """
        bottlenecks = []

        # Analyze CPU bottleneck
        cpu_bottleneck = self._analyze_cpu(metrics)
        if cpu_bottleneck:
            bottlenecks.append(cpu_bottleneck)

        # Analyze memory bottleneck
        memory_bottleneck = self._analyze_memory(metrics)
        if memory_bottleneck:
            bottlenecks.append(memory_bottleneck)

        # Analyze IO bottleneck
        io_bottleneck = self._analyze_io(metrics)
        if io_bottleneck:
            bottlenecks.append(io_bottleneck)

        # Analyze lock bottleneck
        lock_bottleneck = self._analyze_lock(metrics)
        if lock_bottleneck:
            bottlenecks.append(lock_bottleneck)

        # Analyze buffer pool bottleneck
        buffer_bottleneck = self._analyze_buffer_pool(metrics)
        if buffer_bottleneck:
            bottlenecks.append(buffer_bottleneck)

        # Analyze log bottleneck
        log_bottleneck = self._analyze_log(metrics)
        if log_bottleneck:
            bottlenecks.append(log_bottleneck)

        # Sort by severity (highest first)
        bottlenecks.sort(key=lambda b: b.severity, reverse=True)

        return bottlenecks

    def _analyze_cpu(self, metrics: Dict[str, float]) -> Optional[BottleneckInfo]:
        """Analyze CPU bottleneck."""
        # Extract CPU-related metrics
        # Note: These metrics depend on the actual database metrics collected
        # For MySQL, we can use adaptive hash searches, DML operations, etc.

        adaptive_hash_searches = metrics.get('adaptive_hash_searches', 0)
        dml_inserts = metrics.get('dml_inserts', 0)
        dml_updates = metrics.get('dml_updates', 0)
        dml_deletes = metrics.get('dml_deletes', 0)

        # Estimate CPU load (simplified)
        total_operations = adaptive_hash_searches + dml_inserts + dml_updates + dml_deletes

        # If no CPU metrics available, skip
        if total_operations == 0:
            return None

        # Check if CPU is overloaded (simplified heuristic)
        # In a real implementation, this would use actual CPU utilization metrics
        cpu_load = min(1.0, total_operations / 1000000000)  # Normalize to 0-1

        if cpu_load > self.cpu_threshold:
            severity = (cpu_load - self.cpu_threshold) / (1.0 - self.cpu_threshold)
            return BottleneckInfo(
                type=BottleneckType.CPU,
                severity=severity,
                description=f"CPU utilization is high ({cpu_load:.1%})",
                metrics={
                    'adaptive_hash_searches': adaptive_hash_searches,
                    'dml_operations': dml_inserts + dml_updates + dml_deletes,
                    'estimated_cpu_load': cpu_load
                },
                recommendations=[
                    "Consider increasing innodb_buffer_pool_size to reduce CPU-intensive disk reads",
                    "Enable query cache if not already enabled",
                    "Consider increasing thread_pool_size for better CPU utilization",
                    "Review slow queries that may be consuming excessive CPU"
                ]
            )

        return None

    def _analyze_memory(self, metrics: Dict[str, float]) -> Optional[BottleneckInfo]:
        """Analyze memory bottleneck."""
        buffer_pool_size = metrics.get('buffer_pool_size', 0)
        buffer_pool_pages_data = metrics.get('buffer_pool_pages_data', 0)
        buffer_pool_pages_free = metrics.get('buffer_pool_pages_free', 0)
        buffer_pool_pages_total = metrics.get('buffer_pool_pages_total', 0)

        if buffer_pool_pages_total == 0:
            return None

        # Calculate buffer pool utilization
        buffer_pool_utilization = (buffer_pool_pages_data + (buffer_pool_pages_total - buffer_pool_pages_free - buffer_pool_pages_data)) / buffer_pool_pages_total

        # Check if memory is under pressure
        if buffer_pool_utilization > self.memory_threshold:
            severity = (buffer_pool_utilization - self.memory_threshold) / (1.0 - self.memory_threshold)
            return BottleneckInfo(
                type=BottleneckType.MEMORY,
                severity=severity,
                description=f"Buffer pool utilization is high ({buffer_pool_utilization:.1%})",
                metrics={
                    'buffer_pool_size': buffer_pool_size,
                    'buffer_pool_utilization': buffer_pool_utilization,
                    'buffer_pool_pages_data': buffer_pool_pages_data,
                    'buffer_pool_pages_free': buffer_pool_pages_free
                },
                recommendations=[
                    "Increase innodb_buffer_pool_size to reduce memory pressure",
                    "Consider enabling innodb_buffer_pool_dump_at_shutdown for faster restart",
                    "Review memory-intensive queries",
                    "Consider increasing system memory if possible"
                ]
            )

        return None

    def _analyze_io(self, metrics: Dict[str, float]) -> Optional[BottleneckInfo]:
        """Analyze IO bottleneck."""
        buffer_pool_reads = metrics.get('buffer_pool_reads', 0)
        buffer_pool_read_requests = metrics.get('buffer_pool_read_requests', 0)
        os_data_reads = metrics.get('os_data_reads', 0)
        os_data_writes = metrics.get('os_data_writes', 0)

        if buffer_pool_read_requests == 0:
            return None

        # Calculate buffer pool hit ratio
        buffer_pool_hit_ratio = 1.0 - (buffer_pool_reads / buffer_pool_read_requests) if buffer_pool_read_requests > 0 else 1.0

        # Calculate IO wait ratio (simplified)
        total_io = os_data_reads + os_data_writes
        io_wait_ratio = min(1.0, total_io / 1000000)  # Normalize

        if buffer_pool_hit_ratio < self.buffer_hit_ratio_threshold or io_wait_ratio > self.io_wait_threshold:
            severity = max(
                (self.buffer_hit_ratio_threshold - buffer_pool_hit_ratio) / self.buffer_hit_ratio_threshold,
                (io_wait_ratio - self.io_wait_threshold) / (1.0 - self.io_wait_threshold)
            )
            return BottleneckInfo(
                type=BottleneckType.IO,
                severity=severity,
                description=f"IO bottleneck detected (buffer hit ratio: {buffer_pool_hit_ratio:.1%})",
                metrics={
                    'buffer_pool_hit_ratio': buffer_pool_hit_ratio,
                    'buffer_pool_reads': buffer_pool_reads,
                    'os_data_reads': os_data_reads,
                    'os_data_writes': os_data_writes
                },
                recommendations=[
                    "Increase innodb_buffer_pool_size to improve cache hit ratio",
                    "Consider using faster storage (SSD)",
                    "Enable innodb_read_io_threads and innodb_write_io_threads",
                    "Review queries with full table scans"
                ]
            )

        return None

    def _analyze_lock(self, metrics: Dict[str, float]) -> Optional[BottleneckInfo]:
        """Analyze lock bottleneck."""
        lock_row_lock_waits = metrics.get('lock_row_lock_waits', 0)
        lock_row_lock_time = metrics.get('lock_row_lock_time', 0)
        lock_row_lock_time_avg = metrics.get('lock_row_lock_time_avg', 0)
        lock_row_lock_time_max = metrics.get('lock_row_lock_time_max', 0)

        # Check if there are significant lock waits
        if lock_row_lock_waits > 0:
            # Calculate lock wait severity
            lock_wait_ratio = min(1.0, lock_row_lock_waits / 10000)  # Normalize
            avg_lock_time_ms = lock_row_lock_time_avg / 1000 if lock_row_lock_time_avg > 0 else 0

            if lock_wait_ratio > self.lock_wait_threshold or avg_lock_time_ms > 100:
                severity = max(
                    (lock_wait_ratio - self.lock_wait_threshold) / (1.0 - self.lock_wait_threshold),
                    min(1.0, avg_lock_time_ms / 1000)
                )
                return BottleneckInfo(
                    type=BottleneckType.LOCK,
                    severity=severity,
                    description=f"Lock contention detected ({lock_row_lock_waits} waits, avg {avg_lock_time_ms:.1f}ms)",
                    metrics={
                        'lock_row_lock_waits': lock_row_lock_waits,
                        'lock_row_lock_time_avg_ms': avg_lock_time_ms,
                        'lock_row_lock_time_max_ms': lock_row_lock_time_max / 1000
                    },
                    recommendations=[
                        "Review transaction isolation levels",
                        "Consider reducing transaction scope",
                        "Enable innodb_lock_wait_timeout tuning",
                        "Review queries that hold locks for extended periods",
                        "Consider using READ COMMITTED isolation level if appropriate"
                    ]
                )

        return None

    def _analyze_buffer_pool(self, metrics: Dict[str, float]) -> Optional[BottleneckInfo]:
        """Analyze buffer pool bottleneck."""
        buffer_pool_wait_free = metrics.get('buffer_pool_wait_free', 0)
        buffer_pool_read_ahead = metrics.get('buffer_pool_read_ahead', 0)
        buffer_pool_pages_dirty = metrics.get('buffer_pool_pages_dirty', 0)
        buffer_pool_pages_total = metrics.get('buffer_pool_pages_total', 0)

        if buffer_pool_pages_total == 0:
            return None

        # Check if there are buffer pool waits
        if buffer_pool_wait_free > 0:
            wait_ratio = buffer_pool_wait_free / buffer_pool_pages_total
            if wait_ratio > 0.01:  # More than 1% waits
                severity = min(1.0, wait_ratio * 10)
                return BottleneckInfo(
                    type=BottleneckType.BUFFER_POOL,
                    severity=severity,
                    description=f"Buffer pool wait events detected ({buffer_pool_wait_free} waits)",
                    metrics={
                        'buffer_pool_wait_free': buffer_pool_wait_free,
                        'buffer_pool_pages_dirty': buffer_pool_pages_dirty,
                        'buffer_pool_pages_total': buffer_pool_pages_total
                    },
                    recommendations=[
                        "Increase innodb_buffer_pool_size",
                        "Consider increasing innodb_buffer_pool_instances",
                        "Review dirty page flushing settings",
                        "Enable innodb_adaptive_flushing"
                    ]
                )

        return None

    def _analyze_log(self, metrics: Dict[str, float]) -> Optional[BottleneckInfo]:
        """Analyze log bottleneck."""
        log_waits = metrics.get('log_waits', 0)
        log_write_requests = metrics.get('log_write_requests', 0)
        log_writes = metrics.get('log_writes', 0)

        if log_write_requests == 0:
            return None

        # Calculate log wait ratio
        log_wait_ratio = log_waits / log_write_requests if log_write_requests > 0 else 0

        if log_wait_ratio > self.log_wait_threshold:
            severity = (log_wait_ratio - self.log_wait_threshold) / (1.0 - self.log_wait_threshold)
            return BottleneckInfo(
                type=BottleneckType.LOG,
                severity=severity,
                description=f"Log write bottleneck detected ({log_wait_ratio:.1%} wait ratio)",
                metrics={
                    'log_waits': log_waits,
                    'log_write_requests': log_write_requests,
                    'log_wait_ratio': log_wait_ratio
                },
                recommendations=[
                    "Increase innodb_log_file_size",
                    "Consider using faster storage for redo logs",
                    "Enable innodb_flush_log_at_trx_commit=2 for better performance (with trade-offs)",
                    "Review transaction commit frequency"
                ]
            )

        return None

    def generate_prompt_enhancement(self, bottlenecks: List[BottleneckInfo]) -> str:
        """Generate prompt enhancement text based on detected bottlenecks.

        Args:
            bottlenecks: List of detected bottlenecks

        Returns:
            Formatted text to inject into LLM prompt
        """
        if not bottlenecks:
            return ""

        lines = ["\n=== Database Performance Bottleneck Analysis ===\n"]

        for i, bottleneck in enumerate(bottlenecks[:3], 1):  # Top 3 bottlenecks
            lines.append(f"{i}. {bottleneck.type.value.upper()} Bottleneck (Severity: {bottleneck.severity:.1%})")
            lines.append(f"   {bottleneck.description}")
            lines.append(f"   Recommendations:")
            for rec in bottleneck.recommendations[:2]:  # Top 2 recommendations
                lines.append(f"     - {rec}")
            lines.append("")

        lines.append("Please consider these bottlenecks when recommending configuration changes.\n")

        return "\n".join(lines)

    def to_dict(self) -> Dict:
        """Convert analyzer state to dictionary for serialization."""
        return {
            'cpu_threshold': self.cpu_threshold,
            'memory_threshold': self.memory_threshold,
            'io_wait_threshold': self.io_wait_threshold,
            'lock_wait_threshold': self.lock_wait_threshold,
            'buffer_hit_ratio_threshold': self.buffer_hit_ratio_threshold,
            'log_wait_threshold': self.log_wait_threshold
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'WorkloadAnalyzer':
        """Create analyzer from dictionary."""
        return cls(
            cpu_threshold=data.get('cpu_threshold', 0.8),
            memory_threshold=data.get('memory_threshold', 0.85),
            io_wait_threshold=data.get('io_wait_threshold', 0.3),
            lock_wait_threshold=data.get('lock_wait_threshold', 0.2),
            buffer_hit_ratio_threshold=data.get('buffer_hit_ratio_threshold', 0.95),
            log_wait_threshold=data.get('log_wait_threshold', 0.1)
        )
