"""
gt_dataset.py — Georgia Tech 下肢生物力学数据集的路径解析。

数据集有两种目录布局：

    新布局 (new)     <root>/<SUBJECT>/<condition>/<sensor>/<trial>.csv
    旧布局 (legacy)  <root>/<condition>/<sensor>/<trial>.csv

新布局是 ``convert_mat_to_csv.py`` 的产物 —— 全量 21 个受试者 × 4 种运动条件 × 13 类传感器
(约 36,000 个 CSV / 130 GB)，根目录默认是 ``data_GeorgiaTech_csv/``。

旧布局是仓库根目录那套 ``levelground/ ramp/ stair/ treadmill/``，其实是 **AB06 一人的残缺镜像**
(emg 只有 27/30，conditions 只有 1/30)。保留它是为了兼容老的调用方式：探测不到新布局时
自动回退到它，此时受试者固定为 AB06。

本模块只回答两件事 —— **路径怎么拼**、**目录里有什么**，不碰数据本身。
``view_emg.py`` / ``view_emg_imu.py`` / ``view_emg_envelope.py`` 都从这里取路径。

用法::

    uv run python gt_dataset.py                    # 打印数据集结构概览
    uv run python gt_dataset.py --data-dir D:\\x    # 指定别的根目录
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# ── 常量 ─────────────────────────────────────────────────────────────────────

#: 仓库根目录（本文件所在处）。
REPO = Path(__file__).resolve().parent

#: 新布局的默认根目录名。
NEW_DATA_DIRNAME = "data_GeorgiaTech_csv"

#: 4 种运动条件，顺序即 UI 里下拉框的顺序。
CONDITIONS = ("levelground", "ramp", "stair", "treadmill")

#: 运动条件的中文名（UI 文案，改这里就够）。
CONDITION_LABELS = {
    "levelground": "平地行走",
    "ramp": "坡道行走",
    "stair": "楼梯行走",
    "treadmill": "跑步机行走",
}

#: 13 类传感器目录。`emg_envelope` 是由 emg 现场算出来的派生目录，与 emg 同级。
SENSORS = (
    "conditions", "emg", "emg_envelope", "fp", "gcLeft", "gcRight",
    "gon", "id", "ik", "ik_offset", "imu", "jp", "markers",
)

#: 旧布局只有一个受试者（根目录那套镜像就是 AB06 的）。
LEGACY_SUBJECT = "AB06"

#: 环境变量名，用来覆盖数据根目录。
ENV_DATA_DIR = "GEOGETECH_DATA_DIR"

#: 受试者目录名的形状：AB + 数字。
_SUBJECT_RE = re.compile(r"^AB\d+$", re.IGNORECASE)


# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _has_csv(directory: Path) -> bool:
    """目录是否存在且至少含一个 CSV。"""
    try:
        for _ in directory.glob("*.csv"):
            return True
    except OSError:
        return False
    return False


def _looks_new_layout(root: Path) -> bool:
    """root 下有没有 ``ABxx/<condition>/`` 这样的新布局。"""
    if not root.is_dir():
        return False
    for p in root.iterdir():
        if not p.is_dir() or p.name.startswith("_"):
            continue
        if not _SUBJECT_RE.match(p.name):
            continue
        if any((p / c).is_dir() for c in CONDITIONS):
            return True
    return False


def _looks_legacy_layout(root: Path) -> bool:
    """root 下有没有 ``<condition>/<sensor>/`` 这样的旧布局。"""
    return root.is_dir() and any((root / c).is_dir() for c in CONDITIONS)


# ── 数据集 ───────────────────────────────────────────────────────────────────

class Dataset:
    """把 (受试者, 运动条件, 传感器) 映射到 CSV 文件路径。

    布局在构造时自动探测一次：优先新布局 (``<root>/<SUBJECT>/...``)，
    其次旧布局 (``<root>/<condition>/...``)，都没有则抛 ``FileNotFoundError``。

    列表类是**现扫现用**的 —— 转换脚本还在后台跑时，新出现的受试者/条件能立刻被看到，
    不需要重启看图程序。
    """

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()

        if _looks_new_layout(self.root):
            self.layout = "new"
        elif _looks_legacy_layout(self.root):
            self.layout = "legacy"
        else:
            raise FileNotFoundError(
                f"在 {self.root} 下没找到数据集。\n"
                f"期望新布局 <SUBJECT>/<condition>/<sensor>/*.csv "
                f"(如 {NEW_DATA_DIRNAME}/AB06/levelground/emg/)，"
                f"或旧布局 <condition>/<sensor>/*.csv。"
            )

    # ── 受试者 / 运动条件 ─────────────────────────────

    @property
    def subjects(self) -> list[str]:
        """所有可用受试者。

        新布局：扫描 root 下形如 ``AB06`` 的目录（跳过 ``_`` 前缀的辅助目录），
        且至少含一个运动条件目录；旧布局：固定 ``["AB06"]``。
        """
        if self.layout == "legacy":
            return [LEGACY_SUBJECT]

        out: list[str] = []
        for p in sorted(self.root.iterdir()):
            if not p.is_dir() or p.name.startswith("_"):
                continue
            if not _SUBJECT_RE.match(p.name):
                continue
            if any((p / c).is_dir() for c in CONDITIONS):
                out.append(p.name)
        return out

    def conditions_for(self, subject: str, *sensors: str) -> list[str]:
        """该受试者下、且 ``sensors`` 里每个传感器目录都非空的运动条件。

        ``sensors`` 为空时只要求运动条件目录本身存在。
        """
        if not subject:
            return []

        out: list[str] = []
        for cond in CONDITIONS:
            if not self.condition_dir(subject, cond).is_dir():
                continue
            if sensors and not all(
                _has_csv(self.sensor_dir(subject, cond, s)) for s in sensors
            ):
                continue
            out.append(cond)
        return out

    # ── 路径 ──────────────────────────────────────────

    def condition_dir(self, subject: str, condition: str) -> Path:
        """某个运动条件目录的路径。

        新布局 ``<root>/<subject>/<condition>``；旧布局 ``<root>/<condition>``。
        """
        if self.layout == "legacy":
            return self.root / condition
        return self.root / subject / condition

    def sensor_dir(self, subject: str, condition: str, sensor: str) -> Path:
        """某个传感器目录的路径。

        新布局 ``<root>/<subject>/<condition>/<sensor>``；
        旧布局 ``<root>/<condition>/<sensor>``（忽略 subject）。
        """
        return self.condition_dir(subject, condition) / sensor

    def has(self, subject: str, condition: str, sensor: str) -> bool:
        """该传感器目录是否存在。"""
        if not subject or not condition:
            return False
        return self.sensor_dir(subject, condition, sensor).is_dir()

    def trial_path(self, subject: str, condition: str, sensor: str,
                   trial: str) -> Path:
        """某个 trial 文件的完整路径。"""
        return self.sensor_dir(subject, condition, sensor) / trial

    # ── trial 列表 ───────────────────────────────────

    def trials(self, subject: str, condition: str, *sensors: str) -> list[str]:
        """trial 文件名列表（带 ``.csv``，已排序）。

        * 只给一个传感器 → 该目录下全部 CSV；
        * 给多个传感器 → 各目录的**交集**（同名文件才算同一个 trial），
          对应 ``view_emg_imu.py`` 里 emg/imu 的配对逻辑。

        文件名按不透明字符串处理 —— 数据集里有 ``treadmill_04_2_01`` 这种不合命名规律的，
        不做解析。
        """
        if not subject or not condition or not sensors:
            return []

        names: set[str] | None = None
        for sensor in sensors:
            found = {f.name for f in self.sensor_dir(subject, condition, sensor)
                     .glob("*.csv")}
            names = found if names is None else (names & found)
        return sorted(names or ())

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Dataset(root={self.root}, layout={self.layout})"


# ── 根目录定位 ───────────────────────────────────────────────────────────────

def detect_root(explicit: str | None = None) -> Path:
    """定位数据根目录。

    优先级：``--data-dir`` > 环境变量 ``GEOGETECH_DATA_DIR`` >
    ``<repo>/data_GeorgiaTech_csv``（是新布局时）> ``<repo>``（旧布局）。
    """
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_dir():
            raise FileNotFoundError(f"--data-dir 指向的目录不存在: {p}")
        return p.resolve()

    env = os.environ.get(ENV_DATA_DIR)
    if env:
        p = Path(env).expanduser()
        if not p.is_dir():
            raise FileNotFoundError(f"环境变量 {ENV_DATA_DIR} 指向的目录不存在: {p}")
        return p.resolve()

    new_root = REPO / NEW_DATA_DIRNAME
    if _looks_new_layout(new_root):
        return new_root.resolve()

    return REPO.resolve()


def resolve_dataset(args: argparse.Namespace) -> Dataset:
    """按命令行参数构造 ``Dataset``。"""
    return Dataset(detect_root(getattr(args, "data_dir", None)))


def add_data_args(parser: argparse.ArgumentParser) -> None:
    """给 parser 挂上数据集相关的命令行参数。"""
    parser.add_argument(
        "--data-dir",
        help=f"数据根目录（默认自动探测 {NEW_DATA_DIRNAME}/，找不到则退回旧的根目录布局）",
    )
    parser.add_argument("--subject", help="启动时预选的受试者，如 AB12")
    parser.add_argument(
        "--condition", choices=list(CONDITIONS),
        help="启动时预选的运动条件",
    )
    parser.add_argument(
        "--trial",
        help="启动时预选的 trial 文件名，如 levelground_ccw_fast_01_01（可省略 .csv）",
    )


# ── 诊断入口 ─────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    """打印数据集结构概览 —— 兼作冒烟测试。"""
    # 控制台可能是 GBK，遇到无法编码的字符时不要把脚本炸掉（与 convert_mat_to_csv.py 一致）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="打印 Georgia Tech 数据集结构")
    add_data_args(parser)
    args = parser.parse_args(argv)

    dataset = resolve_dataset(args)

    print(f"根目录 : {dataset.root}")
    print(f"布局   : {dataset.layout}")
    subjects = dataset.subjects
    print(f"受试者 : {len(subjects)} 个 -> {', '.join(subjects) or '(无)'}")
    print()

    for subject in subjects:
        conds = dataset.conditions_for(subject, "emg")
        print(f"{subject}  ({len(conds)} 个运动条件)")
        for cond in conds:
            emg = len(dataset.trials(subject, cond, "emg"))
            paired = len(dataset.trials(subject, cond, "emg", "imu"))
            env = len(dataset.trials(subject, cond, "emg_envelope"))
            print(f"    {cond:12s} emg={emg:3d}  emg∩imu={paired:3d}  envelope={env:3d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
