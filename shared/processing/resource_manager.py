"""
リソース管理モジュール

コンテナ環境（cgroup）のリソース監視と、
メモリ使用率に基づく並列数の動的調整を担当
"""
import os
import time
import psutil
from loguru import logger


def get_cgroup_memory() -> dict:
    """
    コンテナ環境(cgroup)のメモリ使用量を取得

    Returns:
        dict: {'percent': float, 'used_gb': float, 'total_gb': float}
    """
    try:
        current = None
        max_mem = None
        inactive_file = 0

        # cgroup v2 パス（Cloud Run Gen2）
        v2_current = '/sys/fs/cgroup/memory.current'
        v2_max = '/sys/fs/cgroup/memory.max'
        v2_stat = '/sys/fs/cgroup/memory.stat'

        # cgroup v1 パス（フォールバック）
        v1_current = '/sys/fs/cgroup/memory/memory.usage_in_bytes'
        v1_max = '/sys/fs/cgroup/memory/memory.limit_in_bytes'
        v1_stat = '/sys/fs/cgroup/memory/memory.stat'

        if os.path.exists(v2_current):
            # cgroup v2
            with open(v2_current, 'r') as f:
                current = int(f.read().strip())
            with open(v2_max, 'r') as f:
                max_val = f.read().strip()
                max_mem = psutil.virtual_memory().total if max_val == 'max' else int(max_val)

            if os.path.exists(v2_stat):
                with open(v2_stat, 'r') as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) == 2 and parts[0] == 'inactive_file':
                            inactive_file = int(parts[1])

        elif os.path.exists(v1_current):
            # cgroup v1
            with open(v1_current, 'r') as f:
                current = int(f.read().strip())
            with open(v1_max, 'r') as f:
                max_mem = int(f.read().strip())
                if max_mem > 1e15:
                    max_mem = psutil.virtual_memory().total

            if os.path.exists(v1_stat):
                with open(v1_stat, 'r') as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) == 2 and parts[0] in ('total_inactive_file', 'inactive_file'):
                            inactive_file = int(parts[1])
                            break

        if current is None or max_mem is None:
            raise FileNotFoundError("cgroup memory files not found")

        # キャッシュを除いた実際の使用量
        actual_used = current - inactive_file
        percent = (actual_used / max_mem) * 100
        used_gb = actual_used / (1024 ** 3)
        total_gb = max_mem / (1024 ** 3)

        return {
            'percent': round(percent, 1),
            'used_gb': round(used_gb, 2),
            'total_gb': round(total_gb, 2)
        }

    except Exception as e:
        # psutilにフォールバック
        logger.debug(f"cgroup memory read failed, falling back to psutil: {e}")
        try:
            memory = psutil.virtual_memory()
            return {
                'percent': round(memory.percent, 1),
                'used_gb': round(memory.used / (1024 ** 3), 2),
                'total_gb': round(memory.total / (1024 ** 3), 2)
            }
        except Exception:
            return {'percent': 50.0, 'used_gb': 8.0, 'total_gb': 16.0}


# CPU使用率計算用のグローバル変数
_last_cpu_stats = {'usage_usec': 0, 'timestamp': time.time()}


def get_cgroup_cpu() -> float:
    """
    コンテナ環境のCPU使用率を取得

    Returns:
        float: CPU使用率（0-100%）
    """
    global _last_cpu_stats

    try:
        usage_nsec = None
        cpu_paths = [
            '/sys/fs/cgroup/cpu.stat',           # cgroup v2
            '/sys/fs/cgroup/cpuacct/cpuacct.usage'  # cgroup v1
        ]

        for path in cpu_paths:
            try:
                if path.endswith('cpu.stat'):
                    with open(path, 'r') as f:
                        for line in f:
                            if line.startswith('usage_usec'):
                                usage_nsec = int(line.split()[1]) * 1000
                                break
                else:
                    with open(path, 'r') as f:
                        usage_nsec = int(f.read().strip())

                if usage_nsec is not None:
                    break
            except Exception:
                continue

        if usage_nsec is None:
            return round(psutil.cpu_percent(interval=0.1), 1)

        current_time = time.time()

        if _last_cpu_stats['usage_usec'] == 0:
            _last_cpu_stats = {'usage_usec': usage_nsec, 'timestamp': current_time}
            return round(psutil.cpu_percent(interval=0.1), 1)

        time_delta = current_time - _last_cpu_stats['timestamp']
        usage_delta = usage_nsec - _last_cpu_stats['usage_usec']

        if time_delta > 0:
            cpu_count = psutil.cpu_count() or 4
            cpu_percent = (usage_delta / (time_delta * 1_000_000_000)) * 100 / cpu_count
            cpu_percent = max(0.0, min(cpu_percent, 100.0))
        else:
            cpu_percent = 0.0

        _last_cpu_stats = {'usage_usec': usage_nsec, 'timestamp': current_time}
        return round(cpu_percent, 1)

    except Exception as e:
        logger.debug(f"cgroup CPU read failed: {e}")
        return round(psutil.cpu_percent(interval=0.1), 1)


class AdaptiveResourceManager:
    """
    アダプティブリソース制御マネージャー

    メモリ使用率に基づいて並列数とスロットル遅延を動的に調整

    メモリ閾値（総メモリ量に応じて自動調整）:
    - 16GB: 保守的（60% → 増加, 90% → 削減）
    - 32GB: 攻めの設定（70% → 増加, 95% → 削減）
    """

    def __init__(self, initial_max_parallel: int = 2, min_parallel: int = 1, max_parallel_limit: int = 100):
        self.max_parallel = initial_max_parallel
        self.min_parallel = min_parallel
        self.max_parallel_limit = max_parallel_limit
        self.throttle_delay = 0.0
        self.adjustment_count = 0
        self.last_adjustment_time = time.time()

        # メモリサイズに応じた閾値設定
        total_memory_gb = 16.0
        try:
            mem_info = get_cgroup_memory()
            total_memory_gb = mem_info['total_gb']
        except Exception:
            pass

        if total_memory_gb >= 32:
            self.memory_low = 70.0
            self.memory_high = 90.0
            self.memory_critical = 95.0
            self.memory_recover = 80.0
        elif total_memory_gb >= 24:
            self.memory_low = 65.0
            self.memory_high = 87.0
            self.memory_critical = 92.0
            self.memory_recover = 75.0
        else:
            self.memory_low = 60.0
            self.memory_high = 85.0
            self.memory_critical = 90.0
            self.memory_recover = 70.0

        # 移動平均用
        self.memory_history = []
        self.history_size = 3

        logger.info(
            f"AdaptiveResourceManager initialized: "
            f"total_memory={total_memory_gb:.1f}GB, "
            f"thresholds=[{self.memory_low}%, {self.memory_high}%, {self.memory_critical}%]"
        )

    def adjust_resources(self, memory_percent: float, current_workers: int) -> dict:
        """
        リソース使用率に基づいて並列数とスロットルを調整

        Args:
            memory_percent: 現在のメモリ使用率（%）
            current_workers: 現在の実行中ワーカー数

        Returns:
            dict: {'max_parallel': int, 'throttle_delay': float, 'adjusted': bool}
        """
        # 移動平均計算
        self.memory_history.append(memory_percent)
        if len(self.memory_history) > self.history_size:
            self.memory_history.pop(0)
        memory_avg = sum(self.memory_history) / len(self.memory_history)

        original_parallel = self.max_parallel
        original_throttle = self.throttle_delay
        adjusted = False

        # 危機的状況: 並列数削減
        if memory_avg > self.memory_critical:
            if self.max_parallel > self.min_parallel:
                self.max_parallel = max(self.max_parallel - 1, self.min_parallel)
                adjusted = True
                logger.warning(
                    f"[Resource] Memory critical ({memory_avg:.1f}%) -> "
                    f"Reducing parallel: {original_parallel} -> {self.max_parallel}"
                )

        # 高負荷: スロットル増加
        if memory_avg > self.memory_high:
            if self.throttle_delay < 5.0:
                self.throttle_delay = min(self.throttle_delay + 0.5, 5.0)
                adjusted = True
                logger.info(
                    f"[Resource] Memory high ({memory_avg:.1f}%) -> "
                    f"Throttle: {original_throttle:.1f}s -> {self.throttle_delay:.1f}s"
                )
        # 回復: スロットル減少
        elif memory_avg < self.memory_recover and self.throttle_delay > 0:
            self.throttle_delay = max(self.throttle_delay - 0.5, 0.0)
            adjusted = True
            logger.info(
                f"[Resource] Memory recovered ({memory_avg:.1f}%) -> "
                f"Throttle: {original_throttle:.1f}s -> {self.throttle_delay:.1f}s"
            )

        # 余裕あり: 並列数増加（現在のワーカーが上限に達している場合のみ）
        if memory_avg < self.memory_low and current_workers >= self.max_parallel:
            if self.max_parallel < self.max_parallel_limit:
                self.max_parallel = min(self.max_parallel + 1, self.max_parallel_limit)
                adjusted = True
                logger.info(
                    f"[Resource] Memory available ({memory_avg:.1f}%) -> "
                    f"Increasing parallel: {original_parallel} -> {self.max_parallel}"
                )

        if adjusted:
            self.adjustment_count += 1
            self.last_adjustment_time = time.time()

        return {
            'max_parallel': self.max_parallel,
            'throttle_delay': self.throttle_delay,
            'adjusted': adjusted,
            'memory_percent': memory_avg
        }

    async def apply_throttle(self):
        """スロットル遅延を適用"""
        if self.throttle_delay > 0:
            import asyncio
            await asyncio.sleep(self.throttle_delay)

    def get_status(self) -> dict:
        """現在の状態を取得"""
        return {
            'max_parallel': self.max_parallel,
            'throttle_delay': self.throttle_delay,
            'adjustment_count': self.adjustment_count
        }
